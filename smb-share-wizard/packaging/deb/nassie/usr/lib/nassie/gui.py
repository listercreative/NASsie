import contextlib
import io
import os
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText

from core import (
    SMBWizard, QR_PASSWORD_RESET_NOTE, pick_directory_native,
    SHARE_NAME_MAX_LEN, SHARE_NAME_RE,
    USERNAME_MAX_LEN, USERNAME_RE,
)
from tour import GuiTour, tour_state, mark_tour_completed


def _patch_messagebox_front(messagebox_module, simpledialog_module):
    # tkinter.messagebox/simpledialog build their own Toplevel internally -
    # there's no hook to run _bring_window_to_front() on it directly the
    # way every one of NASsie's own dialogs does. Same GNOME/Wayland
    # focus-stealing-prevention issue as those, though: a plain showinfo()
    # can open silently buried behind the main window. Toggling the
    # PARENT's own -topmost around the (blocking) call achieves the same
    # effect indirectly - a transient dialog stacks directly above
    # whatever it's transient-for, so a topmost parent drags its dialog
    # to the front along with it.
    def _wrap(fn):
        def wrapper(*args, **kwargs):
            parent = kwargs.get("parent") or tk._default_root
            if parent is not None:
                try:
                    parent.attributes("-topmost", True)
                except tk.TclError:
                    parent = None
            try:
                return fn(*args, **kwargs)
            finally:
                if parent is not None:
                    try:
                        parent.attributes("-topmost", False)
                    except tk.TclError:
                        pass
        return wrapper

    for name in ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel", "askquestion"):
        setattr(messagebox_module, name, _wrap(getattr(messagebox_module, name)))
    simpledialog_module.askstring = _wrap(simpledialog_module.askstring)


_patch_messagebox_front(messagebox, simpledialog)


def _center_over_parent(win, parent):
    # Tkinter Toplevels default to wherever the window manager feels like
    # (often the screen's top-left corner), not anywhere near the window
    # that spawned them - place it over its parent instead.
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // 2
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def _bring_window_to_front(win):
    # Some window managers (seen here under GNOME/Wayland via Xwayland)
    # don't reliably honor a plain lift()/focus_force() for a window that
    # wasn't just freshly mapped by the WM itself - a background process
    # raising its own window gets silently ignored by focus-stealing
    # prevention. Toggling -topmost forces a z-order change instead, which
    # isn't subject to that restriction, and reliably drags the window to
    # front as a side effect. Used by every popup (dialogs, and
    # LogWindow/UserManagementWindow's show()), not just the main window.
    win.deiconify()
    win.lift()
    win.attributes("-topmost", True)
    win.after(200, lambda: win.attributes("-topmost", False))
    win.focus_force()


class _Tooltip:
    """Small hover label for an icon-only button. Icons alone (no text
    label) don't need translation, but they still need to be discoverable
    without guessing - this is the standard way icon toolbars solve that
    everywhere (VS Code, browsers, ...)."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self._focus_binding = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _show(self, event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        ttk.Label(
            self.tip, text=self.text, background="#ffffe0", relief="solid", borderwidth=1, padding=(4, 2),
        ).pack()
        self.tip.lift()
        # No "-topmost" - same fix as GuiTour's _Callout (see tour.py):
        # it pins the tooltip above EVERY window on the desktop, not just
        # NASsie's own. And <Leave> alone doesn't reliably clean this up
        # either - switching to another window WITHOUT the mouse actually
        # leaving the button first (Alt-Tab, clicking a taskbar icon)
        # never fires it, leaving the tooltip floating over whatever's
        # now in front. Hiding on the owning window's own FocusOut closes
        # that gap.
        toplevel = self.widget.winfo_toplevel()
        self._focus_binding = (toplevel, toplevel.bind("<FocusOut>", self._hide, add="+"))

    def _hide(self, event=None):
        if self._focus_binding:
            toplevel, funcid = self._focus_binding
            try:
                toplevel.unbind("<FocusOut>", funcid)
            except tk.TclError:
                pass
            self._focus_binding = None
        if self.tip:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


def _icon_button(parent, icon, tooltip, command, **kwargs):
    # A plain "+" (or any single glyph) at the default button font size
    # reads as thin/washed-out next to full-color emoji icons - a larger
    # size (see the "Icon.TButton" style) gives it comparable visual
    # weight without needing to fall back to a colored-pill emoji glyph
    # just for "add".
    btn = ttk.Button(
        parent, text=icon, command=command, width=3, style="Icon.TButton", **kwargs
    )
    _Tooltip(btn, tooltip)
    return btn


class _RowActionBar:
    """A horizontal strip of icon buttons that floats over a Treeview,
    positioned in line with whichever row is currently selected - not a
    fixed side panel. build_fn(container, item_id) populates container
    with whatever buttons apply to that row (it's cleared first) and
    returns True if it added any, False to keep the bar hidden for that
    row - pack each one with fill="y" so it stretches to match the row's
    own height (set via place() in update() below) instead of sitting as
    a smaller, oddly-padded blob centered on it. The tree is rebuilt from
    scratch on every refresh, which fires <<TreeviewSelect>> again as
    selection is restored, so this stays in sync without any extra wiring
    on the caller's part."""
    def __init__(self, tree, scrollbar, build_fn):
        self.tree = tree
        self.build_fn = build_fn
        self.bar = ttk.Frame(tree)
        tree.bind("<<TreeviewSelect>>", lambda e: self.update(), add="+")
        tree.bind("<Configure>", lambda e: self.update(), add="+")
        # Scrolling doesn't fire either of the above - reposition (or hide,
        # if the selected row scrolled out of view) after the fact, once
        # the scroll itself has actually been applied.
        tree.bind("<MouseWheel>", lambda e: tree.after_idle(self.update), add="+")
        tree.bind("<Button-4>", lambda e: tree.after_idle(self.update), add="+")
        tree.bind("<Button-5>", lambda e: tree.after_idle(self.update), add="+")
        if scrollbar is not None:
            scrollbar.bind("<B1-Motion>", lambda e: tree.after_idle(self.update), add="+")
            scrollbar.bind("<ButtonRelease-1>", lambda e: tree.after_idle(self.update), add="+")

    def update(self):
        for child in self.bar.winfo_children():
            child.destroy()
        selection = self.tree.selection()
        if not selection:
            self.bar.place_forget()
            return
        item = selection[0]
        bbox = self.tree.bbox(item)
        if not bbox:
            # Selected row not currently visible (scrolled out, or the
            # tree hasn't finished laying out yet right after a refresh).
            self.bar.place_forget()
            return
        if not self.build_fn(self.bar, item):
            self.bar.place_forget()
            return
        self.bar.update_idletasks()
        x, y, _w, h = bbox
        bar_w = self.bar.winfo_reqwidth()
        margin = 4

        tree_w = self.tree.winfo_width()
        if bar_w + margin > tree_w:
            # Whatever the window's startup width was computed to fit,
            # it's not enough for this bar - rather than clip it (which
            # kept happening: icon glyph rendering is font/theme-
            # dependent enough that no startup guess reliably gets this
            # right), grow the window itself, right now, by the exact
            # shortfall. Self-correcting regardless of why the room ran
            # out, instead of a second guess that could just as easily
            # still be wrong.
            toplevel = self.tree.winfo_toplevel()
            toplevel.update_idletasks()
            shortfall = (bar_w + margin) - tree_w
            new_w = toplevel.winfo_width() + shortfall
            new_h = toplevel.winfo_height()
            toplevel.geometry(f"{new_w}x{new_h}")
            # A minsize narrower than this would let the window (or the
            # next one opened this small) shrink right back below what
            # this bar needs - raise the floor to match what was just
            # discovered, not just this one time's geometry.
            min_w, min_h = toplevel.minsize()
            if new_w > min_w or new_h > min_h:
                toplevel.minsize(max(new_w, min_w), max(new_h, min_h))
            toplevel.update_idletasks()
            tree_w = self.tree.winfo_width()

        place_x = max(0, tree_w - bar_w - margin)
        # Explicit height=h (the row's own height), not the bar's natural
        # reqheight - paired with each button packed with fill="y" in
        # build_fn, that's what makes the buttons match the row instead of
        # being a smaller, oddly-padded blob centered on it.
        self.bar.place(x=place_x, y=y, width=bar_w, height=h)
        self.bar.lift()


class _SortableTree:
    """Makes a Treeview's column headings clickable to sort its top-level
    rows (children, if any, move with their parent - a plain sibling
    reorder). Click again to reverse; the active column's heading shows a
    ▲/▼ arrow. The tree is rebuilt from scratch on every refresh (see
    _populate_shares_list/UserManagementWindow.refresh), which would
    otherwise silently drop whatever sort was active - call reapply() right
    after repopulating to restore it instead of resetting to insertion
    order every time."""
    def __init__(self, tree, columns, key_fn):
        # columns: [(column_id, heading_label), ...] - column_id "#0" is
        # the tree's own hierarchy column. key_fn(item_id, column_id) -> str
        self.tree = tree
        self.columns = columns
        self.key_fn = key_fn
        self.sort_col = None
        self.reverse = False
        for col_id, label in columns:
            tree.heading(col_id, text=label, command=lambda c=col_id: self.sort(c))

    def sort(self, col, toggle=True):
        if toggle:
            self.reverse = (self.sort_col == col) and not self.reverse
        self.sort_col = col
        items = [(self.key_fn(k, col), k) for k in self.tree.get_children("")]
        items.sort(key=lambda t: t[0].lower(), reverse=self.reverse)
        for index, (_, k) in enumerate(items):
            self.tree.move(k, "", index)
        for col_id, label in self.columns:
            arrow = ""
            if col_id == self.sort_col:
                arrow = " ▼" if self.reverse else " ▲"
            self.tree.heading(col_id, text=label + arrow, command=lambda c=col_id: self.sort(c))

    def reapply(self):
        if self.sort_col is not None:
            self.sort(self.sort_col, toggle=False)


class AddUserDialog(tk.Toplevel):
    """Creates a brand-new account - a typed username that must NOT
    already exist. Granting an EXISTING account access to a share is a
    separate, inline flow now (see GUIWizard._attach_user_to_selected_
    share()/_build_inline_attach()) - not this dialog; conflating "type a
    new name" and "pick an old one" in one combobox used to make it easy
    to accidentally reset an existing account's password when you meant
    to create a new one, or vice versa, and a whole second dialog just to
    pick a name from a list was more than that one choice needed anyway."""
    def __init__(self, parent, existing_usernames=(), show_access_level=True, app=None):
        super().__init__(parent)
        self.existing_usernames = set(existing_usernames)
        self._app = app
        self.title("New User")
        self.resizable(False, False)
        self.transient(parent)
        self.result = None

        ttk.Label(self, text="Username:").grid(row=0, column=0, sticky="e", padx=8, pady=6)
        self.username_var = tk.StringVar()
        username_vcmd = (self.register(self._validate_username_input), "%P")
        self.username_entry = ttk.Entry(
            self, textvariable=self.username_var, validate="key", validatecommand=username_vcmd,
        )
        self.username_entry.grid(row=0, column=1, padx=8, pady=6)

        # Gridded directly in THIS dialog, not a nested Frame with its own
        # independent grid - a nested frame's column widths are negotiated
        # separately from the parent's, which left "Confirm Password:"
        # (wider than "Username:") pushing its entry out of alignment with
        # the username field above it.
        ttk.Label(self, text="Password:").grid(row=1, column=0, sticky="e", padx=8, pady=6)
        self.password_entry = ttk.Entry(self, show="*")
        self.password_entry.grid(row=1, column=1, padx=8, pady=6)
        ttk.Label(self, text="Confirm Password:").grid(row=2, column=0, sticky="e", padx=8, pady=6)
        self.confirm_entry = ttk.Entry(self, show="*")
        self.confirm_entry.grid(row=2, column=1, padx=8, pady=6)

        # Only relevant when this user is being granted access to a share -
        # not shown for standalone user creation, which has no share (and
        # so no access level) to set at all.
        self.show_access_level = show_access_level
        self.read_only_var = tk.BooleanVar(value=False)
        next_row = 3
        if show_access_level:
            ttk.Checkbutton(
                self, text="Read-only access", variable=self.read_only_var
            ).grid(row=3, column=0, columnspan=2, pady=(0, 6))
            next_row = 4

        # Cancel first (ends up on the left), OK second (ends up on the
        # right, as the primary/default action) - see CreateShareDialog's
        # _build_name_page for the same convention and why.
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=next_row, column=0, columnspan=2, pady=10)
        _icon_button(btn_frame, "✖", "Cancel", self.destroy).pack(side="left", padx=4)
        _icon_button(btn_frame, "✔", "OK", self._on_ok).pack(side="left", padx=4)

        self.username_entry.focus_set()
        _center_over_parent(self, parent)
        _bring_window_to_front(self)
        if self._app is not None:
            self._app._notify_tour("user_dialog_opened", window=self)
        self.grab_set()
        self.wait_window(self)

    def _validate_username_input(self, proposed):
        # Blocks disallowed characters at the keystroke - see
        # core.check_username's docstring for why usernames are restricted
        # (they're written unescaped into smb.conf). Empty string must stay
        # allowed or backspacing to clear the field would be blocked too.
        if proposed == "":
            return True
        return len(proposed) <= USERNAME_MAX_LEN and bool(USERNAME_RE.match(proposed))

    def _on_ok(self):
        username = self.username_var.get().strip()
        password = self.password_entry.get()
        confirm = self.confirm_entry.get()

        if not username:
            messagebox.showerror("Invalid input", "Username cannot be empty.", parent=self)
            return

        username_ok, username_message = SMBWizard.check_username(username)
        if not username_ok:
            messagebox.showerror("Invalid input", username_message, parent=self)
            return
        if username in self.existing_usernames:
            messagebox.showerror(
                "Already exists",
                f"'{username}' already exists - use the attach dropdown on a share's row to "
                "grant an existing account access instead.",
                parent=self,
            )
            return

        if not password:
            messagebox.showerror("Invalid input", "Password cannot be empty.", parent=self)
            return
        if password != confirm:
            messagebox.showerror("Invalid input", "Passwords do not match.", parent=self)
            return

        self.result = {"username": username, "password": password}
        if self.show_access_level:
            self.result["read_only"] = self.read_only_var.get()
        self.destroy()


class ChoiceDialog(tk.Toplevel):
    """Generic pick-one-from-a-list dialog, reused wherever an action needs
    the user to pick one share/user from a short list (e.g. choose which
    share to change a password on)."""
    def __init__(self, parent, title, prompt, choices, ok_label="OK"):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result = None

        ttk.Label(self, text=prompt).grid(row=0, column=0, columnspan=2, padx=8, pady=6)
        self.choice_var = tk.StringVar(value=choices[0])
        combo = ttk.Combobox(self, textvariable=self.choice_var, values=choices, state="readonly")
        combo.grid(row=1, column=0, columnspan=2, padx=8, pady=6)

        # Cancel first (left), primary action second (right) - see
        # AddUserDialog's identical btn_frame for the same convention.
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)
        _icon_button(btn_frame, "✖", "Cancel", self.destroy).pack(side="left", padx=4)
        _icon_button(btn_frame, "✔", ok_label, self._on_ok).pack(side="left", padx=4)

        _center_over_parent(self, parent)
        _bring_window_to_front(self)
        self.grab_set()
        self.wait_window(self)

    def _on_ok(self):
        self.result = self.choice_var.get()
        self.destroy()


class QrCodeDialog(tk.Toplevel):
    """Shows a LockNAS bridge QR code for one just-created (or just-granted)
    user. Only ever constructible with a payload already in hand - NASsie
    doesn't persist plaintext passwords, so this can't be regenerated later
    for an existing user without changing their password again first (see
    QR_PASSWORD_RESET_NOTE)."""
    def __init__(self, parent, share_name, username, payload):
        super().__init__(parent)
        self.title(f"QR Code - {username}")
        self.resizable(False, False)
        self.transient(parent)

        try:
            import qrcode
            img = qrcode.make(payload)
            fd, png_path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            try:
                img.save(png_path)
                # Keep a reference on self - PhotoImage has no strong
                # reference of its own, and Tk garbage-collects it out from
                # under the Label the instant nothing else in Python still
                # points to it.
                self._photo = tk.PhotoImage(file=png_path)
            finally:
                try:
                    os.remove(png_path)
                except Exception:
                    pass
        except Exception as e:
            # Without this, a failure here (e.g. a missing/incompletely
            # bundled Pillow codec) left a blank, empty Toplevel on screen -
            # the window itself is already created by this point (title set
            # above), but nothing was ever packed into it - instead of a
            # clear error.
            self.destroy()
            messagebox.showerror("QR code unavailable", f"Couldn't generate the QR code: {e}", parent=parent)
            return

        ttk.Label(
            self, text=f"Scan for easy external configuration of '{share_name}' as {username}", padding=8
        ).pack()
        ttk.Label(self, image=self._photo).pack(padx=12, pady=4)
        ttk.Label(self, text="Compatible with the LockNAS app's bridge QR scanner.", padding=(8, 0)).pack()
        ttk.Label(
            self,
            text="Contains this user's password in plain sight - don't leave it\n"
                 "on screen or let anyone photograph it who shouldn't have access.",
            foreground="#b00000", justify="center", padding=8,
        ).pack()
        _icon_button(self, "✖", "Close", self.destroy).pack(pady=(0, 10))

        _center_over_parent(self, parent)
        _bring_window_to_front(self)
        self.grab_set()
        self.wait_window(self)


class CreateShareDialog(tk.Toplevel):
    """Reached via the toolbar's "New Share" button (or automatically once,
    during first-run onboarding) - a second share is uncommon enough that
    this doesn't need permanent space in the main window. Users are added
    afterward, from the shares list itself (New/Attach User) - not here;
    there's no reason share creation and granting access need to be the
    same step."""
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.wizard = app.wizard
        self._working = False
        self.title("Create Share")
        self.resizable(False, False)
        self.transient(app.root)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Two pages, shown one at a time - a name, THEN (only if the
        # suggested default isn't fine as-is) a folder - rather than both
        # fields at once. Keeps each screen short/focused, and means
        # "where should this live on disk" only comes up once the share
        # already has a name to suggest a default from.
        self._build_name_page()
        self._build_path_page()
        self._show_name_page()

        self.name_entry.focus_set()
        _center_over_parent(self, app.root)
        _bring_window_to_front(self)
        app._notify_tour("share_dialog_opened", window=self)
        self.grab_set()
        self.wait_window(self)

    def _build_name_page(self):
        self.name_page = ttk.Frame(self)

        form = ttk.Frame(self.name_page)
        form.pack(fill="x", padx=8, pady=8)
        ttk.Label(form, text="Share Name:").grid(row=0, column=0, sticky="e", pady=4)
        name_vcmd = (self.register(self._validate_name_input), "%P")
        self.name_entry = ttk.Entry(form, width=40, validate="key", validatecommand=name_vcmd)
        self.name_entry.grid(row=0, column=1, sticky="w", pady=4)
        self.name_entry.bind("<Return>", lambda e: self._confirm_name())

        # Grouped together on the right (standard OK/Cancel placement -
        # see _build_path_page's identical layout for why these used to
        # be spread to opposite edges of the dialog instead, which read
        # as two unrelated buttons rather than one pair) - packed right-
        # to-left, so Next (the primary/default action) ends up
        # rightmost with Cancel just to its left.
        action_frame = ttk.Frame(self.name_page)
        action_frame.pack(fill="x", padx=8, pady=(4, 8))
        _icon_button(action_frame, "✔", "Next", self._confirm_name).pack(side="right")
        _icon_button(action_frame, "✖", "Cancel", self._on_close).pack(side="right", padx=(0, 4))

    def _build_path_page(self):
        self.path_page = ttk.Frame(self)

        form = ttk.Frame(self.path_page)
        form.pack(fill="x", padx=8, pady=8)
        ttk.Label(form, text="Folder Path:").grid(row=0, column=0, sticky="e", pady=4)
        # Read-only: the only way to set this is Browse below, never typed
        # text - a picked path is always a real directory, closing off
        # malformed/adversarial path strings entirely rather than just
        # filtering characters (see SHARE_PATH_RE) after the fact.
        # Pre-filled with a suggested default (computed from the name once
        # page 1 is confirmed - see _confirm_name) for anyone happy to
        # just accept it - Create Share makes that folder itself if it
        # doesn't exist yet, same as it always has.
        self.path_entry = ttk.Entry(form, width=32, state="readonly")
        self.path_entry.grid(row=0, column=1, sticky="w", pady=4)
        _icon_button(form, "📂", "Browse", self._browse_path).grid(row=0, column=2, padx=4)

        # Back stays isolated on the left (a navigation action, not part
        # of the confirm/cancel decision) - Cancel and Create Share
        # (the primary/default action) are grouped together on the
        # right, same reasoning and pack order as _build_name_page's.
        action_frame = ttk.Frame(self.path_page)
        action_frame.pack(fill="x", padx=8, pady=(4, 8))
        _icon_button(action_frame, "◀", "Back", self._show_name_page).pack(side="left")
        self.create_button = _icon_button(action_frame, "✔", "Create Share", self._on_create_share)
        self.create_button.pack(side="right")
        _icon_button(action_frame, "✖", "Cancel", self._on_close).pack(side="right", padx=(0, 4))

    def _show_name_page(self):
        self.path_page.pack_forget()
        self.name_page.pack(fill="both", expand=True)
        # The window auto-sizes to whichever page is currently packed -
        # re-center now that it just changed size, once Tk has actually
        # recomputed it (immediately after pack() is too early).
        self.after_idle(lambda: _center_over_parent(self, self.app.root))
        self.name_entry.focus_set()

    def _confirm_name(self):
        name = self.name_entry.get().strip()
        name_ok, name_message = self.wizard.check_share_name(name)
        if not name_ok:
            self.app._tour_flash_name_error(name_message)
            messagebox.showerror("Invalid name", name_message, parent=self)
            return
        self._set_path(self.wizard.default_share_path(name))
        self.name_page.pack_forget()
        self.path_page.pack(fill="both", expand=True)
        self.after_idle(lambda: _center_over_parent(self, self.app.root))
        # After the re-center above, not before - the tour's own callout
        # placement (triggered synchronously by this) reads this dialog's
        # CURRENT position, so it needs to see the recentered one, not the
        # stale name-page position. update_idletasks() (which reading a
        # widget's geometry always does) flushes idle callbacks in the
        # order they were queued, so scheduling this after the recenter
        # above is enough to guarantee that ordering.
        self.app._notify_tour("share_name_confirmed", window=self)

    def _validate_name_input(self, proposed):
        # Blocks disallowed characters at the keystroke - see
        # core.check_share_name's docstring for why (share names are
        # written unescaped as a "[name]" smb.conf section header). Empty
        # string must stay allowed or backspacing to clear would be blocked.
        if proposed == "":
            return True
        return len(proposed) <= SHARE_NAME_MAX_LEN and bool(SHARE_NAME_RE.match(proposed))

    def _on_close(self):
        # Ignored while a create is in flight (button disabled during that
        # window too) - the background thread still holds a reference to
        # this dialog's widgets via _apply_done's closure.
        if self._working:
            return
        self.destroy()

    def _set_path(self, value):
        # path_entry is state="readonly", which (like a normal ttk widget's
        # disabled state) blocks .insert()/.delete() the same way it blocks
        # typing - not just keyboard input specifically - so setting it
        # programmatically means briefly lifting that restriction.
        self.path_entry.configure(state="normal")
        self.path_entry.delete(0, tk.END)
        self.path_entry.insert(0, value)
        self.path_entry.configure(state="readonly")

    def _browse_path(self):
        # Prefers a genuinely native picker (zenity/kdialog on Linux) over
        # Tkinter's own askdirectory() - see pick_directory_native's
        # docstring for why: on Linux specifically, Tkinter's bundled
        # fallback chooser is missing a working "New Folder" button and
        # has a click-to-collapse quirk in its tree view, whereas the
        # real native picker (what this already amounts to on Windows/
        # macOS) has proper folder creation built in - no separate
        # NASsie-side "New Folder" flow needed alongside it.
        handled, selected = pick_directory_native("Select Folder to Share")
        if not handled:
            selected = filedialog.askdirectory(parent=self, title="Select Folder to Share")
        if selected:
            # Tkinter's directory picker always returns "/"-separated
            # paths, even on Windows - normalize to the native separator
            # here (see core.select_directory()'s comment for why this
            # isn't just cosmetic).
            self._set_path(os.path.normpath(selected))

    def _on_create_share(self):
        name = self.name_entry.get().strip()
        path = self.path_entry.get().strip() or self.wizard.default_share_path()

        name_ok, name_message = self.wizard.check_share_name(name)
        if not name_ok:
            messagebox.showerror("Invalid name", name_message, parent=self)
            return
        path_ok, path_message = self.wizard.check_share_path(path)
        if not path_ok:
            messagebox.showerror("Invalid path", path_message, parent=self)
            return

        self.wizard.share_name = name
        self.wizard.share_path = path
        self.wizard.users = []

        self._working = True
        self.create_button.configure(state="disabled")

        threading.Thread(target=self._apply_worker, daemon=True).start()

    def _apply_worker(self):
        self.app._busy_start()
        share_name = self.wizard.share_name
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                if self.wizard.has_admin_privileges():
                    self.wizard.dispatch_execution()
                else:
                    print("Not running with elevated privileges — requesting elevation via the OS's native prompt.")
                    self.wizard.elevate_and_apply({
                        "name": self.wizard.share_name,
                        "path": self.wizard.share_path,
                        "users": self.wizard.users
                    })
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")

        # Checked against live share state, not the elevated step's own
        # return value or captured output. When elevation is needed, the
        # actual work runs in a SEPARATE relaunched process
        # (pkexec/UAC/osascript) - its own print() output (including a
        # "Success." marker) lands on that process's stdout, which is not
        # something contextlib.redirect_stdout here can ever see, and on
        # Windows specifically there's no way to stream it back at all
        # (Start-Process -Verb RunAs can't be combined with output
        # redirection across the UAC boundary). Re-querying whether the
        # share now actually exists sidesteps all of that - same technique
        # cli.py's start() already uses after "Create New Share".
        success = any(s["name"] == share_name for s in self.wizard.list_shares())
        self.app.root.after(0, lambda: self._apply_done(buffer.getvalue(), success))

    def _apply_done(self, log_output, success):
        self._working = False
        self.app._busy_stop()
        if log_output.strip():
            self.app._append_log(log_output)
        self.app._refresh_all_lists()
        if success:
            # Release this dialog's modal grab before the follow-on
            # messagebox rather than nesting a new grab underneath it.
            self.destroy()
            self.app._notify_tour("share_created")
            messagebox.showinfo(
                "Share Creation Succeeded",
                "Configuration attempt finished — see the log for details.\n\n"
                "Add users from the shares list (New User / Attach User) whenever you're ready.",
            )
            # _patch_messagebox_front's own -topmost toggle drops back to
            # False the instant this returns (its finally block, right as
            # OK is clicked) - some window managers read that as "no
            # longer wants focus" and drop the main window behind
            # whatever else is on screen. Explicitly re-raising it here
            # closes that gap rather than counting on the NEXT tour
            # step's own _bring_to_front() to happen fast enough.
            _bring_window_to_front(self.app.root)
            self.app._notify_tour("share_apply_confirmed")
        else:
            self.create_button.configure(state="normal")
            messagebox.showerror(
                "Share Creation Failed",
                "Configuration attempt failed — see the log for details.",
                parent=self,
            )


class LogWindow(tk.Toplevel):
    """Raw stdout output from every action (share/user create, delete,
    grant/revoke access, ...) collects here - a separate, non-modal window
    (opened via the header's log button) rather than a permanently visible
    panel, since most people only need to check it occasionally. Closing
    it (the window's own X button) just hides it instead of destroying it,
    so the accumulated log survives across show/hide."""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("NASsie Log")
        self.geometry("640x320")
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

        self.log_text = ScrolledText(self, state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        # Hidden until explicitly opened - not shown at startup, since an
        # empty log window in front of the main one on first launch would
        # just be in the way.
        self.withdraw()

    def append(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def show(self):
        _bring_window_to_front(self)


class UserManagementWindow(tk.Toplevel):
    """Account-level user management, decoupled from any specific share -
    Create User, Change Password, Delete User. A separate, non-modal
    window (opened via the main toolbar's Users button) rather than a
    second page, same reasoning as LogWindow: most of what people do day
    to day is share-scoped (attach/unattach), so this doesn't need to
    compete with the shares list for space. Hidden, not destroyed, on
    close - state survives across show/hide, same as LogWindow."""
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.wizard = app.wizard
        self.title("User Management")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=8, pady=(8, 0))
        self._new_user_toolbar_btn = _icon_button(toolbar, "+👤", "New User", self._create_new_user)
        self._new_user_toolbar_btn.pack(side="left")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        tree_frame = ttk.Frame(body)
        tree_frame.pack(fill="both", expand=True)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.users_list = ttk.Treeview(tree_frame, show="tree headings", height=14)
        self._sort = _SortableTree(
            self.users_list, [("#0", "Username")], key_fn=lambda k, c: self.users_list.item(k, "text"),
        )
        self.users_list.column("#0", width=260, stretch=True)
        self.users_list.grid(row=0, column=0, sticky="nsew")
        vscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.users_list.yview)
        vscroll.grid(row=0, column=1, sticky="ns")
        self.users_list.configure(yscrollcommand=vscroll.set)

        # Floating, row-anchored action buttons instead of a fixed side
        # panel - see _RowActionBar's docstring.
        self._action_bar = _RowActionBar(self.users_list, vscroll, self._build_action_bar)

        # Measured, not guessed - see GUIWizard's sizing block (same
        # reasoning: nothing is selected yet, so the action bar doesn't
        # exist for winfo_reqwidth() to have accounted for on its own).
        self.update_idletasks()
        bar_w = self.app._measure_action_bar_width(("🔑", "📱", "🗑"))
        name_col_w = self.users_list.column("#0", "width")
        width = max(name_col_w + 20 + bar_w + 40, self.winfo_reqwidth())
        height = max(380, self.winfo_reqheight())
        self.geometry(f"{width}x{height}")
        self.minsize(width, height)

        # Hidden until explicitly opened - see LogWindow's identical reasoning.
        self.withdraw()

    def _build_action_bar(self, container, item):
        _icon_button(container, "🔑", "Change Password", self._change_password).pack(
            side="left", fill="y", padx=1
        )
        _icon_button(container, "📱", "Show QR Code", self._show_qr).pack(side="left", fill="y", padx=1)
        _icon_button(container, "🗑", "Delete User", self._delete_user).pack(side="left", fill="y", padx=1)
        return True

    def show(self):
        self.refresh()
        _bring_window_to_front(self)
        self.app._notify_tour("user_mgmt_opened", window=self)

    def _on_close(self):
        self.withdraw()
        self.app._notify_tour("user_mgmt_closed")

    def refresh(self):
        selected = self._selected_username()
        users = self.wizard.list_users()
        for item in self.users_list.get_children():
            self.users_list.delete(item)
        for u in users:
            # list_users() returns every OS-level account on the machine
            # (it has to, so "Attach User" pickers can offer an existing
            # person) - but showing all of those here, unlabeled, means
            # someone's own Windows sign-in or a family member's account
            # shows up in a sharing tool with no explanation of what it is.
            # Show an account NASsie either created itself, or that already
            # has share access through NASsie - not every account on the box.
            if not (u.get("managed") or u["shares"]):
                continue
            item_id = self.users_list.insert("", tk.END, text=u["username"])
            if u["username"] == selected:
                self.users_list.selection_set(item_id)
        self._sort.reapply()
        self._action_bar.update()

    def _selected_username(self):
        selection = self.users_list.selection()
        return self.users_list.item(selection[0], "text") if selection else None

    def _create_new_user(self):
        existing = {u["username"] for u in self.wizard.list_users()}
        dialog = AddUserDialog(
            self, existing_usernames=existing, show_access_level=False, app=self.app
        )
        if not dialog.result:
            return
        username = dialog.result["username"]
        password = dialog.result["password"]
        threading.Thread(target=self.app._create_user_worker, args=(username, password), daemon=True).start()

    def _change_password(self):
        self._change_password_flow(confirm_qr=True)

    def _show_qr(self):
        # Asks for the CURRENT password and verifies it live (see
        # SMBWizard.verify_password()) rather than resetting it - unlike
        # _change_password_flow, this never touches the account, so
        # showing a QR code again doesn't invalidate whatever other
        # devices already connected with the old one. Falls back to that
        # reset flow only if verification fails - covers "I don't
        # remember it" without forcing a reset on the common case.
        username = self._selected_username()
        if not username:
            messagebox.showinfo("Show QR Code", "Select a user first.")
            return
        user = next((u for u in self.wizard.list_users() if u["username"] == username), None)
        shares = user.get("shares", []) if user else []
        if not shares:
            messagebox.showinfo("Show QR Code", f"'{username}' doesn't have access to any share yet.")
            return

        password = simpledialog.askstring(
            "Show QR Code", f"Enter '{username}'s current password to show their QR code:",
            show="*", parent=self,
        )
        if not password:
            return

        # Any one of the user's own shares works - see
        # _verify_password_linux for why a real ("guest ok = no") share
        # has to be named on Linux specifically.
        if not self.wizard.verify_password(username, password, shares[0]):
            if messagebox.askyesno(
                "Incorrect password",
                "That doesn't match this account's current password.\n\n"
                "Forgot it? Reset the password instead to generate a new QR code.",
            ):
                self._change_password_flow(confirm_qr=False)
            return

        if len(shares) == 1:
            share_name = shares[0]
        else:
            dialog = ChoiceDialog(
                self, "Choose share", "Show a QR code for which share?", shares, ok_label="Show",
            )
            if not dialog.result:
                return
            share_name = dialog.result

        self.app._offer_qr_codes(share_name, [{"username": username, "password": password}], confirm=False)

    def _change_password_flow(self, confirm_qr):
        # Passwords are stored as hashes everywhere (Samba, Windows, macOS)
        # - there's no way to retrieve an existing user's current password.
        # Changing it (and encoding the new one) is the only honest way to
        # show a QR code for an already-existing user - see
        # QR_PASSWORD_RESET_NOTE. Reuses _grant_access_worker, the same
        # underlying operation attaching a user to a share uses.
        username = self._selected_username()
        if not username:
            messagebox.showinfo("Change Password", "Select a user first.")
            return
        user = next((u for u in self.wizard.list_users() if u["username"] == username), None)
        shares = user.get("shares", []) if user else []
        if not shares:
            messagebox.showinfo("Change Password", f"'{username}' doesn't have access to any share yet.")
            return

        # On Windows this would reset the account's real sign-in password
        # (no separate SMB password store there - see
        # _add_user_to_share_windows) - never do that to an account NASsie
        # didn't create. Linux is unaffected: Samba's password is already
        # independent of the real login password.
        if not user.get("managed", False) and self.wizard.system == "Windows":
            messagebox.showinfo(
                "Change Password",
                f"'{username}' is an existing Windows account, not one NASsie created - changing its "
                "password here would also change what they sign in with, so NASsie won't do that. "
                "Change their password from Windows' own account settings instead.",
            )
            return

        if not messagebox.askokcancel("Change Password", QR_PASSWORD_RESET_NOTE, parent=self):
            return

        if len(shares) == 1:
            share_name = shares[0]
        else:
            dialog = ChoiceDialog(
                self, "Choose share", "Change password (and show a QR code) for which share?",
                shares, ok_label="Next",
            )
            if not dialog.result:
                return
            share_name = dialog.result

        password = simpledialog.askstring(
            "New password", f"New password for '{username}' (replaces their current one):",
            show="*", parent=self,
        )
        if not password:
            return

        # Preserve the user's current access level on this share - a
        # password change shouldn't silently flip them back to read-write.
        share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
        share_user = next((u for u in (share or {}).get("users", []) if u["username"] == username), None)
        read_only = share_user.get("read_only", False) if share_user else False

        threading.Thread(
            target=self.app._grant_access_worker,
            args=(share_name, username, password, read_only, confirm_qr), daemon=True,
        ).start()

    def _delete_user(self):
        username = self._selected_username()
        if not username:
            messagebox.showinfo("Delete User", "Select a user first.")
            return
        user = next((u for u in self.wizard.list_users() if u["username"] == username), None)
        if not (user and user.get("managed", False)):
            # Never delete an account NASsie didn't create - that's a real
            # person's computer account, not an SMB-only one NASsie can
            # freely remove. Unattaching from shares is still fine;
            # deleting the account itself is not NASsie's call to make.
            messagebox.showinfo(
                "Delete User",
                f"'{username}' is an existing computer account, not one NASsie created - NASsie won't "
                "delete it. Unattach it from its shares instead, or delete the account itself from "
                "your computer's own account settings.",
            )
            return
        if not messagebox.askyesno(
            "Delete User",
            f"Delete user '{username}' entirely? This removes their account everywhere, not just one share.",
        ):
            return
        threading.Thread(target=self.app._delete_user_worker, args=(username,), daemon=True).start()


class GUIWizard:
    def __init__(self):
        self.wizard = SMBWizard()
        # Which shares_list item (if any) is currently showing the inline
        # "Attach User" combobox instead of its normal action-bar icons -
        # see _build_share_action_bar()/_attach_user_to_selected_share().
        self._attaching_item = None
        self._attaching_share = None
        self._attaching_candidates = []
        self._attaching_candidate_users = {}

        # className sets WM_CLASS, which is what taskbars/docks/app-switchers
        # use to match this running window back to nassie.desktop (and thus
        # its Icon=) - without it Tk defaults to the generic class "Tk" and
        # the tray/taskbar icon can end up generic even though the titlebar
        # icon (set below) looks right.
        self.root = tk.Tk(className="NASsie")
        self.root.title("NASsie")
        # Styles are per-interpreter, not per-window - defining these once
        # here covers every icon button/Treeview in the app, including
        # ones on Toplevels (dialogs, LogWindow, UserManagementWindow)
        # created later. Treeview's default rowheight was too short to
        # render a full emoji glyph without vertically clipping it - a
        # clipped person emoji's rounded "head" read as a stray blue arc,
        # not recognizable as part of an icon at all. The taller row (see
        # _RowActionBar, which places each button at height=<row height>)
        # fixes that; the icon font can go back down to a size that
        # actually fits its button's own padding now that it isn't also
        # being sized up to dodge clipping.
        ttk.Style(self.root).configure("Icon.TButton", font=("TkDefaultFont", 11))
        ttk.Style(self.root).configure("Treeview", rowheight=32)
        # Tk has no built-in "center on screen" - left alone, the window
        # manager decides placement, which is commonly the top-left corner
        # rather than anywhere near the middle of the display.
        self._load_icon_image()
        self._set_window_icon()
        self._build_header()
        self._build_shares_page()

        # Both non-modal, hidden until opened - see their own docstrings
        # for why these are separate windows rather than tabs/panels.
        self._log_window = LogWindow(self.root)
        self._user_mgmt_window = UserManagementWindow(self)

        self._refresh_all_lists()

        # Tk has no built-in "center on screen" - left alone, the window
        # manager decides placement, which is commonly the top-left corner
        # rather than anywhere near the middle of the display.
        #
        # The floor is measured from the widgets themselves (via
        # winfo_reqwidth/reqheight, after update_idletasks lays everything
        # out) rather than a hardcoded guess - a fixed constant here doesn't
        # track content, so when the list+contextual-actions layout needed
        # more room than the guess, shrinking to "minimum" still hid part
        # of it below the window edge instead of just clipping/scrolling.
        # That alone isn't enough for the row-action bar specifically,
        # though: nothing is selected at startup, so it doesn't exist yet
        # for winfo_reqwidth() to have measured at all. A guessed pixel
        # constant here previously stood in for it and kept being wrong
        # (icon glyph rendering varies enough by font/theme) - actually
        # building the widest possible bar off-screen and measuring it
        # (see _measure_action_bar_width) is the only way to get this
        # right regardless of that.
        self.root.update_idletasks()
        share_bar_w = self._measure_action_bar_width(("+👤", "🔗", "➖👤", "🔒", "🗑"))
        name_col_w = self.shares_list.column("#0", "width")
        # name column + vertical scrollbar (~20px) + a floor for the Path
        # column so it isn't squeezed to nothing + the action bar itself +
        # outer padding/margins.
        width = max(700, name_col_w + 20 + 150 + share_bar_w + 60, self.root.winfo_reqwidth())
        height = max(600, self.root.winfo_reqheight())
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        # Without a floor, shrinking the window below what the list+actions
        # layout actually needs squishes it into an overlapping, unreadable
        # mess instead of just clipping/scrolling - the window is otherwise
        # freely resizable (no resizable(False) call), so this is the only
        # thing stopping that.
        self.root.minsize(width, height)
        self._bring_to_front()

        # Deferred via after() either way, so the window is fully mapped
        # (real winfo_rootx/rooty) before the tour measures widget
        # positions.
        state = tour_state()
        if state == "new":
            self.root.after(400, self._start_tour)
        elif state == "interrupted":
            # Closing NASsie mid-tour (a crash, force-quit, or just its
            # own window close button) used to permanently lose the tour
            # - the marker was written the instant it STARTED, before the
            # user had actually done anything with it. Now it's only
            # written on genuine completion or an explicit Skip (see
            # tour.py's tour_state()/mark_tour_completed()), so this is
            # reachable - offer to pick it back up rather than either
            # silently never showing it again or restarting it
            # unconditionally on every launch until someone finishes it.
            self.root.after(400, self._offer_tour_resume)

    def _measure_action_bar_width(self, icons):
        # Builds the given icons as real buttons (same style/font as
        # production) in a throwaway, never-packed frame just to read
        # their combined natural width, then discards it - see the sizing
        # block's comment for why a measurement beats a guessed constant.
        probe = ttk.Frame(self.root)
        for icon in icons:
            ttk.Button(probe, text=icon, width=3, style="Icon.TButton").pack(side="left", fill="y", padx=1)
        probe.update_idletasks()
        width = probe.winfo_reqwidth()
        probe.destroy()
        return width

    def _bring_to_front(self):
        # When NASsie is launched by the "Launch NASsie" checkbox
        # (WixShellExec, run from the installer's own process, not the
        # user's foreground one), Windows' foreground-lock restrictions
        # silently ignore a plain lift()/focus_force() from a background
        # process, leaving the window open but buried behind others - the
        # same issue _bring_window_to_front() works around for every popup.
        _bring_window_to_front(self.root)

    def run(self):
        self.root.mainloop()

    def _load_icon_image(self):
        # Ships right next to this file both in the source tree and in the
        # installed package (see build.sh). A frozen PyInstaller --onefile
        # build extracts its data files (see build.ps1's --add-data) into a
        # temp dir at sys._MEIPASS instead - __file__ isn't a real path
        # there, so that has to be checked first.
        if getattr(sys, 'frozen', False):
            base_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_dir, "nassie_icon.png")
        try:
            self._icon_image = tk.PhotoImage(file=icon_path)
        except tk.TclError:
            self._icon_image = None

    def _set_window_icon(self):
        if self._icon_image:
            self.root.iconphoto(True, self._icon_image)

    def _build_header(self):
        # The real logo, front and center - not the TUI's ASCII rendition -
        # so the app's branding is obvious the moment the window opens, not
        # just in the titlebar/taskbar.
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=12, pady=(12, 0))

        if self._icon_image:
            scale = max(1, self._icon_image.width() // 56)
            self._header_icon_image = self._icon_image.subsample(scale, scale)
            ttk.Label(header, image=self._header_icon_image).pack(side="left", padx=(0, 10))

        ttk.Label(header, text="NASsie", font=("TkDefaultFont", 18, "bold")).pack(side="left")
        _icon_button(header, "📋", "View Log", lambda: self._log_window.show()).pack(side="right")
        # Indeterminate progress bar doubling as a busy spinner - packed
        # only while at least one background action is running (see
        # _busy_start/_busy_stop), not a fixed part of the layout, so it
        # doesn't take up space or draw the eye when nothing is happening.
        self._busy_bar = ttk.Progressbar(header, mode="indeterminate", length=100)
        self._busy_count = 0

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=8, pady=(10, 0))

    def _busy_start(self):
        self._busy_count += 1
        if self._busy_count == 1:
            self._busy_bar.pack(side="right", padx=(0, 10))
            self._busy_bar.start(12)

    def _busy_stop(self):
        self._busy_count = max(0, self._busy_count - 1)
        if self._busy_count == 0:
            self._busy_bar.stop()
            self._busy_bar.pack_forget()

    def _notify_tour(self, event, window=None):
        # The active GuiTour (if any) advances itself on real actions
        # happening rather than a "Next" button, and follows the user into
        # whichever dialog just opened - see GuiTour.on_event().
        tour = getattr(self, "_tour", None)
        if tour:
            tour.on_event(event, window=window)

    def _tour_flash_name_error(self, message):
        tour = getattr(self, "_tour", None)
        if tour:
            tour.show_name_error(message)

    def _offer_tour_resume(self):
        # Restarts from step one, not literally mid-step - there's no
        # meaningful way to resume mid-action-driven-flow across a
        # process restart anyway (the exact dialog/step state it
        # interrupted on is gone with the old process), so "continue"
        # and "redo" are the same operation here: run the tour again.
        if messagebox.askyesno(
            "Resume Tour",
            "It looks like the guided tour didn't finish last time.\n\n"
            "Continue it now?",
        ):
            self._start_tour()
        else:
            mark_tour_completed()

    def _start_tour(self):
        # Rebuilt each time rather than cached - a stale GuiTour with a
        # half-run index would otherwise resume mid-tour instead of
        # restarting from step one on the next click.
        self._tour = GuiTour(self)
        self._tour.start()

    def _open_create_share_dialog(self):
        CreateShareDialog(self)

    def _build_shares_page(self):
        # The only page - see the shares/users restructuring discussion:
        # shares as expandable parent rows, each attached user nested
        # underneath as a child row. Most of the old "which share?" picker
        # dialogs (change access level, change password, ...) disappear
        # entirely this way, since the share is already known from
        # whichever row is selected.
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill="x", padx=8, pady=(8, 0))
        self._new_share_btn = _icon_button(toolbar, "➕", "New Share", self._open_create_share_dialog)
        self._new_share_btn.pack(side="left")
        self._manage_users_btn = _icon_button(
            toolbar, "👤", "Manage Users", lambda: self._user_mgmt_window.show()
        )
        self._manage_users_btn.pack(side="left", padx=(6, 0))

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        tree_frame = ttk.Frame(body)
        tree_frame.pack(fill="both", expand=True)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        # "username" is a hidden data column (see displaycolumns) - not
        # visible; just how the raw username survives on a child row
        # independent of that row's DISPLAY text (which carries "(read-only)"/
        # override annotations that would otherwise have to be parsed back
        # out of the label).
        self.shares_list = ttk.Treeview(
            tree_frame, columns=("path", "username"), displaycolumns=("path",), show="tree headings",
        )
        self._shares_sort = _SortableTree(
            self.shares_list, [("#0", "Share Name"), ("path", "Path")],
            key_fn=lambda k, c: self.shares_list.item(k, "text") if c == "#0" else self.shares_list.set(k, c),
        )
        self.shares_list.column("#0", width=220, stretch=False)
        self.shares_list.column("path", width=320, stretch=True)
        self.shares_list.grid(row=0, column=0, sticky="nsew")

        shares_vscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.shares_list.yview)
        shares_vscroll.grid(row=0, column=1, sticky="ns")
        shares_hscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.shares_list.xview)
        shares_hscroll.grid(row=1, column=0, sticky="ew")
        self.shares_list.configure(yscrollcommand=shares_vscroll.set, xscrollcommand=shares_hscroll.set)

        # Floating, row-anchored action buttons instead of a fixed side
        # panel - see _RowActionBar's docstring.
        self._share_action_bar = _RowActionBar(self.shares_list, shares_vscroll, self._build_share_action_bar)
        self.shares_list.bind("<<TreeviewSelect>>", lambda e: self._notify_tour("share_selected"), add="+")

    def _build_share_action_bar(self, container, item):
        # Attach mode replaces the normal icon row with an inline
        # combobox for THIS specific item - see _attach_user_to_selected_
        # share()/_build_inline_attach(). _RowActionBar rebuilds this bar
        # from scratch on every selection/scroll/resize event, so this
        # check has to happen on every call, not just once when attach
        # mode is entered.
        if item == self._attaching_item:
            return self._build_inline_attach(container, item)

        parent = self.shares_list.parent(item)
        if parent:
            username = self.shares_list.set(item, "username") or None
        else:
            username = None
        _icon_button(container, "+👤", "New User", self._new_user_for_selected_share).pack(
            side="left", fill="y", padx=1
        )
        _icon_button(
            container, "🔗", "Attach User", self._attach_user_to_selected_share
        ).pack(side="left", fill="y", padx=1)
        if username:
            _icon_button(
                container, "➖👤", "Unattach User", self._unattach_selected_user
            ).pack(side="left", fill="y", padx=1)
            # The icon itself doubles as the access-level badge (padlock
            # closed = read-only, open = read-write) as well as the
            # toggle control - clicking it flips the level immediately,
            # no confirmation dialog (see _change_access_level_for_
            # selection()). The row's own label (see
            # _populate_shares_list()) carries the same information for
            # when this bar isn't showing at all (nothing selected).
            share_name, _ = self._selected_share_and_user()
            share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
            share_user = next((u for u in (share or {}).get("users", []) if u["username"] == username), None)
            read_only = bool(share_user and share_user.get("read_only"))
            icon, tip = ("🔒", "Read-only - click to make read-write") if read_only else (
                "🔓", "Read-write - click to make read-only"
            )
            _icon_button(
                container, icon, tip, self._change_access_level_for_selection
            ).pack(side="left", fill="y", padx=1)
        _icon_button(container, "🗑", "Delete Share", self._delete_selected_share).pack(
            side="left", fill="y", padx=1
        )
        return True

    def _build_inline_attach(self, container, item):
        # Selecting a value commits immediately - no OK/Cancel, no
        # separate dialog window. See _attach_user_to_selected_share()
        # for how attach mode is entered and _commit_inline_attach() for
        # what happens on selection.
        var = tk.StringVar()
        combo = ttk.Combobox(
            container, textvariable=var, values=self._attaching_candidates, state="readonly",
        )
        combo.pack(side="left", fill="both", expand=True, padx=1)
        combo.bind("<<ComboboxSelected>>", lambda e: self._commit_inline_attach(var.get()))
        _icon_button(container, "✖", "Cancel", self._cancel_inline_attach).pack(side="left", fill="y", padx=1)
        # Opens the dropdown right away - the whole point of "contextual
        # dropdown" is picking a user in one motion, not a second click
        # just to open what clicking the attach icon conceptually already
        # asked for. Guarded: by the time this fires, a fast selection (or
        # Cancel) may have already rebuilt this bar out from under it via
        # _RowActionBar.update(), destroying this exact combobox.
        def _open_dropdown():
            try:
                combo.focus_set()
                combo.event_generate("<Down>")
            except tk.TclError:
                pass
        container.after(10, _open_dropdown)
        return True

    def _cancel_inline_attach(self):
        self._attaching_item = None
        self._attaching_share = None
        self._attaching_candidates = []
        self._attaching_candidate_users = {}
        self._share_action_bar.update()

    def _selected_share_and_user(self):
        # Returns (share_name, username) - username is None when the
        # selected row is the share itself rather than one of its nested
        # users. Returns (None, None) when nothing is selected.
        selection = self.shares_list.selection()
        if not selection:
            return None, None
        item = selection[0]
        parent = self.shares_list.parent(item)
        if parent:
            return self.shares_list.item(parent, "text"), self.shares_list.set(item, "username") or None
        return self.shares_list.item(item, "text"), None

    def _refresh_all_lists(self):
        # Called after every mutating action, so nothing ever needs a
        # manual refresh. list_shares/list_groups each shell out
        # (PowerShell on Windows especially, one process per call even
        # after batching), so running them synchronously here visibly
        # stalled the window. Fetch in the background and apply to the
        # Treeviews on the main thread once ready.
        threading.Thread(target=self._refresh_all_lists_worker, daemon=True).start()

    def _refresh_all_lists_worker(self):
        shares = self.wizard.list_shares()
        # Group management has no UI of its own anymore, but a group could
        # still exist (created by an older NASsie version, or directly via
        # the OS) and grant access that overrides a user's own per-share
        # setting - still worth reflecting accurately here.
        groups = self.wizard.list_groups()
        overrides = self.wizard.effective_share_access(shares, groups)

        def apply():
            self._populate_shares_list(shares, overrides)
            self._user_mgmt_window.refresh()

        self.root.after(0, apply)

    def _append_log(self, text):
        self._log_window.append(text)

    def _populate_shares_list(self, shares, overrides=None):
        # list_shares() only reflects live share config (Get-SmbShare /
        # smb.conf) - it has no idea whether the folder that config points
        # at still exists, since the OS never removes a share just because
        # its target got deleted out from under it. Flag that here instead,
        # so an orphaned share (folder deleted outside NASsie) is visibly
        # different from a normal one rather than silently looking fine.
        overrides = overrides or {}
        # Remember which share (by name) was expanded/selected so a
        # refresh mid-session doesn't collapse everything the user had
        # open or lose their place.
        open_shares = {
            self.shares_list.item(k, "text")
            for k in self.shares_list.get_children("")
            if self.shares_list.item(k, "open")
        }
        selected_share, selected_user = self._selected_share_and_user()

        for item in self.shares_list.get_children():
            self.shares_list.delete(item)

        for share in shares:
            name = share.get("name", "?")
            path = share.get("path") or "Unknown"
            if path != "Unknown" and not os.path.isdir(path):
                path = f"{path}  (folder missing!)"
            share_id = self.shares_list.insert(
                "", tk.END, text=name, values=(path, ""), open=(name in open_shares) or name == selected_share,
            )
            for u in share.get("users", []):
                # Always shown, not just for the read-only exception -
                # otherwise the level is invisible for every user until
                # you select their row and see the toggle button's own
                # icon (see _build_share_action_bar).
                level = "read-only" if u.get("read_only") else "read-write"
                label = f"{u['username']} ({level})"
                override = overrides.get((name, u["username"]))
                if override:
                    # Their own read-only setting above is masked by a
                    # group grant made outside NASsie's UI - see
                    # effective_share_access().
                    label += " [overridden by a group]"
                user_id = self.shares_list.insert(share_id, tk.END, text=label, values=("", u["username"]))
                if name == selected_share and u["username"] == selected_user:
                    self.shares_list.selection_set(user_id)
            if share.get("access_group"):
                # Informational only - a group override isn't a
                # NASsie-managed "user" row to act on, just context that
                # full/read-only access is coming from somewhere else too.
                level = "read-only" if share.get("access_group_read_only") else "full access"
                self.shares_list.insert(share_id, tk.END, text=f"(also granted via a group: {level})", values=("", ""))
            if name == selected_share and selected_user is None:
                self.shares_list.selection_set(share_id)

        self._shares_sort.reapply()
        self._share_action_bar.update()

    def _new_user_for_selected_share(self):
        share_name, _ = self._selected_share_and_user()
        if not share_name:
            messagebox.showinfo("New User", "Select a share first.")
            return
        existing = {u["username"] for u in self.wizard.list_users()}
        dialog = AddUserDialog(self.root, existing_usernames=existing, app=self)
        if not dialog.result:
            return
        username = dialog.result["username"]
        password = dialog.result["password"]
        read_only = dialog.result.get("read_only", False)
        threading.Thread(
            target=self._grant_access_worker, args=(share_name, username, password, read_only), daemon=True
        ).start()

    def _attach_user_to_selected_share(self):
        share_name, _ = self._selected_share_and_user()
        if not share_name:
            messagebox.showinfo("Attach User", "Select a share first.")
            return
        share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
        already = {u["username"] for u in (share or {}).get("users", [])}
        all_users = self.wizard.list_users()
        candidates = [u for u in all_users if u["username"] not in already]
        if not candidates:
            messagebox.showinfo("Attach User", "Every existing user already has access to this share.")
            return
        selection = self.shares_list.selection()
        if not selection:
            return
        # Full dicts (not just names) kept for _commit_inline_attach() -
        # it needs "managed" (existing-computer-account safety check) and
        # "shares" (has_credentials - see its own comment) per candidate,
        # not just the name the combobox itself hands back.
        self._attaching_item = selection[0]
        self._attaching_share = share_name
        self._attaching_candidates = [u["username"] for u in candidates]
        self._attaching_candidate_users = {u["username"]: u for u in candidates}
        self._share_action_bar.update()

    def _commit_inline_attach(self, username):
        share_name = self._attaching_share
        candidate_users = self._attaching_candidate_users
        self._attaching_item = None
        self._attaching_share = None
        self._attaching_candidates = []
        self._attaching_candidate_users = {}
        if not username:
            self._share_action_bar.update()
            return

        # Already attached to at least one OTHER share means already has
        # valid Samba/SMB credentials - Samba (and, worse, macOS) store
        # one password per account, not one per share, so an account
        # already attached elsewhere doesn't need a new one just to
        # attach it here too. See core._add_user_to_share_linux's comment
        # for the full reasoning.
        existing_user = candidate_users.get(username)
        password = None
        if not (existing_user and existing_user.get("shares")):
            password = simpledialog.askstring(
                "Password", f"Set a password for '{username}' on this share:", show="*", parent=self.root,
            )
            if not password:
                self._share_action_bar.update()
                return

        # A name matching a real, pre-existing computer account (not one
        # NASsie created) needs a heads-up before granting it access, and
        # on Windows must never actually use the typed password - see
        # existing_account_grant_message()/_add_user_to_share_windows.
        if existing_user and not existing_user.get("managed", False):
            if not messagebox.askyesno(
                "Existing computer account", self.existing_account_grant_message(username)
            ):
                self._share_action_bar.update()
                return
            if self.wizard.system == "Windows":
                password = None

        # New attaches default to read-write - access level is now a
        # one-click toggle on the row itself (see _build_share_action_bar)
        # rather than a checkbox set once at attach time, so there's no
        # reason to ask for it here too.
        threading.Thread(
            target=self._grant_access_worker, args=(share_name, username, password, False), daemon=True
        ).start()

    def _unattach_selected_user(self):
        share_name, username = self._selected_share_and_user()
        if not share_name or not username:
            messagebox.showinfo("Unattach", "Select a user under a share first.")
            return
        if not messagebox.askyesno(
            "Unattach", f"Remove '{username}''s access to '{share_name}'?"
        ):
            return
        threading.Thread(
            target=self._revoke_access_worker, args=(share_name, username), daemon=True
        ).start()

    def existing_account_grant_message(self, username):
        if self.wizard.system == "Windows":
            return (
                f"'{username}' is an existing Windows account, not one NASsie created.\n\n"
                "Granting access won't change their Windows password - they'll keep signing in "
                "the same way. Add them to this share?"
            )
        if self.wizard.system == "Linux":
            return (
                f"'{username}' is an existing Linux account, not one NASsie created.\n\n"
                "This sets/updates their separate file-sharing (Samba) password only - it "
                "won't touch their regular login password. Add them to this share?"
            )
        return (
            f"'{username}' already exists on this computer, not created by NASsie.\n\n"
            "Add them to this share? This sets the password used for sharing access."
        )

    def _change_access_level_for_selection(self):
        # No confirmation dialog - the button itself already shows the
        # CURRENT level (padlock closed/open, see _build_share_action_bar)
        # and doing this is a one-click toggle back if it was wrong,
        # unlike deleting a share or a user.
        share_name, username = self._selected_share_and_user()
        if not share_name or not username:
            messagebox.showinfo("Change Access Level", "Select a user under a share first.")
            return
        share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
        share_user = next((u for u in (share or {}).get("users", []) if u["username"] == username), None)
        current_read_only = share_user.get("read_only", False) if share_user else False
        threading.Thread(
            target=self._change_access_worker, args=(share_name, username, not current_read_only), daemon=True
        ).start()

    def _change_access_worker(self, share_name, username, read_only):
        self._busy_start()
        buffer = io.StringIO()
        changed = False
        try:
            with contextlib.redirect_stdout(buffer):
                changed = self.wizard.change_share_access(share_name, username, read_only)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._change_access_done(share_name, username, read_only, changed, buffer.getvalue()))

    def _change_access_done(self, share_name, username, read_only, changed, log_output):
        self._busy_stop()
        if log_output.strip():
            self._append_log(log_output)
        if not changed:
            # No success popup (see _change_access_level_for_selection's
            # comment - the toggle button/badge already show the result),
            # but a FAILURE still needs to interrupt: the row silently
            # not changing could otherwise look identical to "there was
            # nothing to change."
            messagebox.showerror("Failed", f"Could not change '{username}''s access level — see log.")
        self._refresh_all_lists()

    def _grant_access_worker(self, share_name, username, password, read_only=False, confirm_qr=True):
        self._busy_start()
        buffer = io.StringIO()
        added = False
        try:
            with contextlib.redirect_stdout(buffer):
                added = self.wizard.grant_share_access(share_name, username, password, read_only)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(
            0,
            lambda: self._grant_access_done(share_name, username, password, added, buffer.getvalue(), confirm_qr),
        )

    def _grant_access_done(self, share_name, username, password, added, log_output, confirm_qr=True):
        self._busy_stop()
        if log_output.strip():
            self._append_log(log_output)
        if added:
            self._notify_tour("user_attached")
            messagebox.showinfo("Added", f"Added '{username}' to share '{share_name}'.")
            # See _apply_done's identical call for why this is needed
            # right here, not left to the next tour step's own
            # _bring_to_front().
            _bring_window_to_front(self.root)
            self._notify_tour("attach_apply_confirmed")
            # password is None for an existing, unmanaged Windows account
            # NASsie deliberately left untouched (see
            # _add_user_to_share_windows) - there's no password to encode,
            # and it must never be guessed at or invented for the QR code.
            if password is not None:
                self._offer_qr_codes(share_name, [{"username": username, "password": password}], confirm=confirm_qr)
        else:
            messagebox.showerror("Failed", f"Could not add '{username}' to share '{share_name}' — see log.")
        self._refresh_all_lists()

    def _revoke_access_worker(self, share_name, username):
        self._busy_start()
        buffer = io.StringIO()
        revoked = False
        try:
            with contextlib.redirect_stdout(buffer):
                revoked = self.wizard.revoke_share_access(share_name, username)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._revoke_access_done(share_name, username, revoked, buffer.getvalue()))

    def _revoke_access_done(self, share_name, username, revoked, log_output):
        self._busy_stop()
        if log_output.strip():
            self._append_log(log_output)
        if revoked:
            messagebox.showinfo("Unattached", f"Removed '{username}''s access to '{share_name}'.")
        else:
            messagebox.showerror("Failed", f"Could not remove access — see log.")
        self._refresh_all_lists()

    def _delete_user_worker(self, username):
        self._busy_start()
        buffer = io.StringIO()
        deleted = False
        try:
            with contextlib.redirect_stdout(buffer):
                deleted = self.wizard.remove_user(username)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._delete_user_done(username, deleted, buffer.getvalue()))

    def _delete_user_done(self, username, deleted, log_output):
        self._busy_stop()
        if log_output.strip():
            self._append_log(log_output)
        if deleted:
            messagebox.showinfo("Deleted", f"Deleted user '{username}'.")
        else:
            messagebox.showerror("Failed", f"Could not delete user '{username}' — see log.")
        self._refresh_all_lists()

    def _create_user_worker(self, username, password):
        self._busy_start()
        buffer = io.StringIO()
        created = False
        try:
            with contextlib.redirect_stdout(buffer):
                created = self.wizard.add_user(username, password)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._create_user_done(username, created, buffer.getvalue()))

    def _create_user_done(self, username, created, log_output):
        self._busy_stop()
        if log_output.strip():
            self._append_log(log_output)
        if created:
            self._notify_tour("user_created")
            messagebox.showinfo("User Created", f"'{username}' has been created.")
            # See _apply_done's identical call for why this is needed
            # right here, not left to the next tour step's own
            # _bring_to_front().
            _bring_window_to_front(self.root)
            self._notify_tour("user_apply_confirmed")
        else:
            messagebox.showerror("Failed", f"Could not set up user '{username}' — see log.")
        self._refresh_all_lists()

    def _delete_selected_share(self):
        name, _ = self._selected_share_and_user()
        if not name:
            return
        if not messagebox.askyesno(
            "Remove share", f"Remove share '{name}'? This updates the live share configuration."
        ):
            return
        delete_folder = False
        share = next((s for s in self.wizard.list_shares() if s["name"] == name), None)
        path = share.get("path") if share else None
        if path:
            delete_folder = messagebox.askyesno(
                "Delete folder too?",
                f"Also permanently delete the folder and everything in it?\n\n{path}\n\n"
                "This cannot be undone.",
            )
        threading.Thread(target=self._delete_worker, args=(name, delete_folder), daemon=True).start()

    def _delete_worker(self, name, delete_folder=False):
        self._busy_start()
        buffer = io.StringIO()
        removed = False
        try:
            with contextlib.redirect_stdout(buffer):
                removed = self.wizard.remove_share(name, delete_folder)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.root.after(0, lambda: self._delete_done(name, removed, delete_folder, buffer.getvalue()))

    def _delete_done(self, name, removed, delete_folder, log_output):
        self._busy_stop()
        if log_output.strip():
            self._append_log(log_output)
        if removed:
            messagebox.showinfo(
                "Removed", f"Removed share: {name}" + (" (folder deleted too)" if delete_folder else "")
            )
        else:
            messagebox.showerror("Failed", f"Could not remove share '{name}' — see log.")
        self._refresh_all_lists()

    def _offer_qr_codes(self, share_name, users, confirm=True):
        # users: [{"username": ..., "password": ...}, ...] - only ever
        # offered right when a password was just set (share creation / add
        # user), since NASsie never persists plaintext passwords and so has
        # no way to regenerate this later for an existing user. confirm=False
        # skips the "want to see it?" ask - for the dedicated Show QR
        # button, where clicking it already is that confirmation.
        if confirm and not messagebox.askyesno(
            "QR Code",
            "Show a QR code for easy external configuration of this share?\n\n"
            "The code contains this user's password in plain sight - only display it "
            "somewhere private."
        ):
            return
        remaining = list(users)
        while remaining:
            if len(remaining) == 1:
                user = remaining[0]
            else:
                dialog = ChoiceDialog(
                    self.root, "Choose user", "Show QR code for which user?",
                    [u["username"] for u in remaining], ok_label="Show"
                )
                if not dialog.result:
                    return
                user = next(u for u in remaining if u["username"] == dialog.result)
            payload = self.wizard.build_locknas_qr_payload(share_name, user["username"], user["password"])
            QrCodeDialog(self.root, share_name, user["username"], payload)
            remaining = [u for u in remaining if u is not user]
            if remaining and not messagebox.askyesno("QR Code", "Show another user's QR code?"):
                return


def main():
    GUIWizard().run()


if __name__ == "__main__":
    main()
