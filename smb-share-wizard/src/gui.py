import contextlib
import io
import os
import platform
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
from tour import GuiTour, tour_state, tour_progress_index, mark_tour_completed
import nassie_ttk
import window_corners


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
            # Restored to whatever it was BEFORE this call, not
            # unconditionally forced off - every NASsie window is
            # permanently "-topmost" now (see _bring_window_to_front()),
            # so blindly turning it off here would silently undo that the
            # moment any messagebox/askstring call involving that window
            # returns.
            was_topmost = False
            if parent is not None:
                try:
                    was_topmost = bool(parent.attributes("-topmost"))
                    parent.attributes("-topmost", True)
                except tk.TclError:
                    parent = None
            try:
                return fn(*args, **kwargs)
            finally:
                if parent is not None and not was_topmost:
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
    #
    # Ends up "-topmost" TRUE, permanently - not toggled back off (a
    # prior version reset it to False here, matching Toplevel's normal
    # one-off "come to front" behavior). Explicitly reverted: every
    # NASsie window needs to stay above other applications on the
    # desktop the whole time it's open, not just flash to the front the
    # instant it's created/shown, or switching away to a different app
    # even briefly buries it - the SAME reason the tour callout itself
    # is permanently topmost (see _Callout's own comment in tour.py).
    # The initial False->True->False->True sequence (not just setting
    # True once) is still needed for the RAISE itself: toggling forces
    # a z-order change some window managers (seen here under GNOME/
    # Wayland via Xwayland) don't reliably apply for a plain lift() on
    # a window that wasn't just freshly mapped by the WM itself - a
    # background process raising its own already-open window gets
    # silently ignored by focus-stealing prevention otherwise. update()
    # between the toggles is a synchronous round trip, not a deferred
    # win.after() (a still-earlier version used that, and two DIFFERENT
    # windows raised this way in quick succession - confirmed live:
    # _create_user_done()'s own call on root, immediately followed by
    # the tour's next step raising UserManagementWindow - raced each
    # other, since whichever deferred reset hadn't fired yet stayed on
    # top even though the OTHER window was raised more recently).
    win.deiconify()
    win.lift()
    win.attributes("-topmost", False)
    win.update()
    win.attributes("-topmost", True)
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
        self.tip = tk.Toplevel(self.widget)
        # Withdrawn immediately, before the label below is even packed -
        # same fix, same reasoning as tour.py's _Callout: Tk maps a
        # Toplevel the instant it exists, at whatever size it has BEFORE
        # its content is packed and measured, so left visible this showed
        # up as one real, flashed frame of an empty/wrong-sized box before
        # snapping to the correct size around the label's actual text.
        # Only shown (deiconify(), below) once real placement is applied.
        self.tip.withdraw()
        self.tip.wm_overrideredirect(True)
        ttk.Label(
            self.tip, text=self.text, background="#ffffe0", relief="solid", borderwidth=1, padding=(4, 2),
        ).pack()
        self.tip.update_idletasks()
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip.wm_geometry(f"+{x}+{y}")
        self.tip.deiconify()
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


class _Toast(tk.Toplevel):
    """A transient, non-modal "it worked" notice (share/user created,
    attached, detached, deleted, removed - see GUIWizard._show_toast()).
    These used to be tk.messagebox.showinfo() popups - modal, so each one
    stopped the whole app cold just to acknowledge something that had
    already finished succeeding. This floats in the parent window's own
    bottom-right corner instead, never grabs focus or a modal grab, and
    dismisses itself on its own after _TIMEOUT_MS - the ✕ (or just
    waiting) are the only two ways to close it, since there's no decision
    left for a real OK button to make.

    on_close (if given) fires exactly once, whichever of those two paths
    actually closes it - GUIWizard uses this to still gate the tour's own
    "Confirmation" steps on the user actually acknowledging (or outlasting)
    the toast, same as the blocking messagebox used to.

    anchor (defaults to parent) is the widget this spans - the FULL
    width of it, not just centered at its own compact natural size, and
    positioned near its bottom edge. GUIWizard._show_toast() passes the
    shares list specifically ("the main pane, using the width of both
    columns" - requested live), not root's own full width, which would
    otherwise drift the toast off its actual content whenever a side
    panel is open and root is wider than just the shares list."""
    _TIMEOUT_MS = 4000
    _MARGIN = 16
    # Only ever used as a fallback, when self.anchor hasn't been laid out
    # to a real width yet (winfo_width() still reporting 1, the Tk
    # not-yet-mapped default) - see _place()'s own guard.
    _FALLBACK_WIDTH = 280

    def __init__(self, parent, title, text, on_close=None, anchor=None):
        super().__init__(parent)
        self.parent = parent
        self.anchor = anchor if anchor is not None else parent
        self._on_close = on_close
        self._dismissed = False
        # Withdrawn immediately - same reasoning as _Tooltip/_Callout: Tk
        # maps a Toplevel the instant it exists, before its own content
        # below is ever packed/measured, which would otherwise flash one
        # frame at the wrong size before _place() ever runs.
        self.withdraw()
        self.overrideredirect(True)
        # Pinned above every window on the desktop, not just NASsie's own -
        # same tradeoff _Callout's own identical attribute makes (see its
        # docstring): a toast that could end up buried behind whatever's
        # in front the instant it appears would go entirely unseen.
        self.attributes("-topmost", True)
        self.configure(bg="#0e92ab", padx=1, pady=1)

        inner = tk.Frame(self, bg=_ADD_ROW_BG)
        inner.pack(fill="both", expand=True)

        header = tk.Frame(inner, bg=_ADD_ROW_BG)
        header.pack(fill="x", padx=(10, 6), pady=(8, 0))
        if title:
            ttk.Label(
                header, text=title, font=("TkDefaultFont", 10, "bold"), background=_ADD_ROW_BG,
            ).pack(side="left")
        close = tk.Label(
            header, text="✕", bg=_ADD_ROW_BG, fg="#555555", font=("TkDefaultFont", 9), cursor="hand2",
        )
        close.pack(side="right")
        close.bind("<Button-1>", self._dismiss)

        # fill="x" (not just anchor="w") - this needs to actually stretch
        # to the window's own forced width (see _place()), not sit at
        # whatever narrower width its own text naturally needs. Kept on
        # self so _place() can retarget wraplength to match, since the
        # window's real final width isn't known until then.
        self.text_label = ttk.Label(
            inner, text=text, background=_ADD_ROW_BG, justify="left",
        )
        self.text_label.pack(fill="x", anchor="w", padx=10, pady=(2, 10))

        self.update_idletasks()
        self._place()
        self.deiconify()
        self._timer = self.after(self._TIMEOUT_MS, self._dismiss)

    def _place(self):
        # Spans self.anchor's own FULL width (the shares list, both its
        # columns - see this class's own docstring), inset by _MARGIN on
        # each side - not just centered at its own compact natural size -
        # positioned near ITS bottom edge, not the screen, matching
        # _Tooltip/_Callout's own "positioned relative to the thing it's
        # about" convention rather than always landing in the same
        # physical screen corner regardless of where NASsie itself is.
        aw = self.anchor.winfo_width()
        ah = self.anchor.winfo_height()
        width = max(1, (aw if aw > 1 else self._FALLBACK_WIDTH) - 2 * self._MARGIN)
        # Re-wrap the text to the real target width (minus this label's
        # own left/right padding) now that it's known, then remeasure -
        # a wraplength fixed at construction time (this class's own
        # earlier version) couldn't adapt to the anchor's actual width.
        self.text_label.configure(wraplength=max(1, width - 20))
        self.update_idletasks()
        height = self.winfo_reqheight()
        x = self.anchor.winfo_rootx() + self._MARGIN
        y = self.anchor.winfo_rooty() + ah - height - self._MARGIN
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    def _dismiss(self, event=None):
        if self._dismissed:
            return
        self._dismissed = True
        try:
            self.after_cancel(self._timer)
        except (ValueError, tk.TclError):
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass
        if self._on_close:
            self._on_close()


def _deselect_on_blank_click(tree, event):
    # Clicking BELOW the last row (or otherwise on the tree's own empty
    # background) is a real, common way to say "I'm done with that row" -
    # ttk.Treeview's own built-in click handling only ever acts when a
    # row IS under the cursor, so without this, the previously-selected
    # row (and its own floating _RowActionBar) just stayed selected and
    # highlighted indefinitely after clicking away from it (requested
    # live, for both the shares list and the Users panel's own list).
    # identify_row() returns "" for exactly this "no row here" case.
    if not tree.identify_row(event.y):
        tree.selection_remove(*tree.selection())


def _icon_button(parent, icon, tooltip, command, width=3, shadow=False, **kwargs):
    # A plain "+" (or any single glyph) at the default button font size
    # reads as thin/washed-out next to full-color emoji icons - a larger
    # size (see the "Icon.TButton" style) gives it comparable visual
    # weight without needing to fall back to a colored-pill emoji glyph
    # just for "add".
    if shadow:
        # nassie_ttk's flat theme draws buttons with no border/relief at
        # all (its own flat, modern look), which reads fine for a busy
        # toolbar but left a dialog's one or two decision buttons (Cancel/
        # OK) looking like plain white boxes with nothing to distinguish
        # them as clickable (reported live). ttk buttons in this theme
        # are image-rendered, not relief/borderwidth-driven, so
        # style.configure(relief=...) wouldn't do anything - this fakes a
        # drop shadow with plain geometry instead: a slightly darker
        # Frame BEHIND the button, peeking out along the bottom/right
        # edge because the button inside it is packed flush to the
        # top-left instead of centered. Solid color, no theme/image
        # dependency, so it's guaranteed to actually render regardless of
        # what the current ttk theme does with real button styling.
        card = tk.Frame(parent, background="#adadad")
        btn = ttk.Button(
            card, text=icon, command=command, width=width, style="Icon.TButton", **kwargs
        )
        btn.pack(padx=(0, 2), pady=(0, 2))
        _Tooltip(btn, tooltip)
        # card itself has no -state option (it's a plain tk.Frame, not a
        # ttk widget) - .button is how a caller that needs to reconfigure
        # the real button later (CreateShareDialog.create_button, see its
        # own comment) reaches it instead.
        card.button = btn
        return card
    btn = ttk.Button(parent, text=icon, command=command, width=width, style="Icon.TButton", **kwargs)
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
        # Scrolling doesn't fire either of the above, but it also doesn't
        # change WHICH row is selected or that row's own state - only
        # reposition() (place() the ALREADY-built buttons at the row's
        # new on-screen position), not a full update() (destroy every
        # button and rebuild from scratch), which visibly flickered on
        # every scroll notch/scrollbar-drag tick (reported live) for no
        # reason: the exact same buttons were about to be rebuilt right
        # back anyway.
        tree.bind("<MouseWheel>", lambda e: tree.after_idle(self.reposition), add="+")
        tree.bind("<Button-4>", lambda e: tree.after_idle(self.reposition), add="+")
        tree.bind("<Button-5>", lambda e: tree.after_idle(self.reposition), add="+")
        if scrollbar is not None:
            scrollbar.bind("<B1-Motion>", lambda e: tree.after_idle(self.reposition), add="+")
            scrollbar.bind("<ButtonRelease-1>", lambda e: tree.after_idle(self.reposition), add="+")

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
        self._place(bbox)

    def reposition(self):
        # No destroy/rebuild - see __init__'s own comment for why. A
        # no-op (not an error) if nothing's currently selected/built -
        # scroll events fire this unconditionally regardless of selection.
        if not self.bar.winfo_children():
            return
        selection = self.tree.selection()
        if not selection:
            self.bar.place_forget()
            return
        bbox = self.tree.bbox(selection[0])
        if not bbox:
            # Scrolled out of view - hidden, not destroyed, so scrolling
            # it back into view can go straight to placing it again with
            # no rebuild either.
            self.bar.place_forget()
            return
        self._place(bbox)

    def _place(self, bbox):
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
    order every time.

    pinned_first, if given, is a callable returning the id of a row that
    must always stay at index 0 regardless of sorting (the add-row - see
    _populate_shares_list()) - re-applied after EVERY sort(), not just
    reapply(), since sort() is what a real column-heading click runs
    directly; only re-pinning in reapply() left a sort genuinely reachable
    by clicking a heading (confirmed live: sorting by Share Name knocked
    the add-row to wherever "➕" happened to collate to instead of leaving
    it first)."""
    def __init__(self, tree, columns, key_fn, pinned_first=None, top_level_tag="stripe_row"):
        # columns: [(column_id, heading_label), ...] - column_id "#0" is
        # the tree's own hierarchy column. key_fn(item_id, column_id) -> str
        self.tree = tree
        self.columns = columns
        self.key_fn = key_fn
        self.pinned_first = pinned_first
        # Which tag TOP-LEVEL rows alternate with - "stripe_row" (gray,
        # the default) for a flat list with no children (the Users
        # panel's own users_list), "share_stripe" (the add-row's own
        # teal/blue) for shares_list, whose top-level rows are shares,
        # not users - see _restripe()'s own comment for why these two
        # need to be visually distinct colors, not just two instances of
        # the same one.
        self.top_level_tag = top_level_tag
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
        self._repin()

    def reapply(self):
        if self.sort_col is not None:
            self.sort(self.sort_col, toggle=False)
        else:
            self._repin()

    def _repin(self):
        if self.pinned_first is None:
            return
        item_id = self.pinned_first()
        if item_id and self.tree.exists(item_id):
            self.tree.move(item_id, "", 0)
        self._restripe(item_id)

    def _restripe(self, pinned_id):
        # Two INDEPENDENT alternations, not one continuous count down the
        # whole visible order (an earlier version of this) - requested
        # live, with a concrete example ("test1 should be blue... test 2
        # [should be] white... and so on") once a share with zero users
        # (breaking a single flat count's own parity) made that version's
        # actual result stop matching what a share-only count would give:
        #
        # - Top-level (share) rows alternate on their OWN position among
        #   OTHER SHARES only, via self.top_level_tag - how many users
        #   any of them happen to have never shifts a later share's own
        #   color. Starts on the DEFAULT background (not the tag), since
        #   the pinned add-row right above the first one already reads as
        #   its own distinct color - two tinted rows back to back
        #   wouldn't read as alternating at all.
        # - Each share's own user rows alternate on their position WITHIN
        #   that one share specifically, via "stripe_row" (gray) - reset
        #   to a fresh count for every share, not carried over from the
        #   last one. Starts ON "stripe_row" (gray) this time, not the
        #   default background - unlike the top-level count, there's no
        #   pinned row directly above the first user row already using
        #   that same color to avoid butting up against.
        #
        # Both based on get_children()'s CURRENT (post-move/post-sort)
        # order, not insertion order, so the pattern stays correct after
        # a column-heading sort reorders everything, not just on first
        # load. Assigned once here regardless of whether a share is
        # currently expanded or not - expand/collapse is a pure
        # visibility toggle in ttk.Treeview, it doesn't change which rows
        # exist, so a child row already carries the right tag the moment
        # it's revealed rather than needing its own recompute on every
        # <<TreeviewOpen>>.
        top_index = 0
        for item in self.tree.get_children(""):
            if item == pinned_id:
                continue
            self.tree.item(item, tags=(self.top_level_tag,) if top_index % 2 == 1 else ())
            top_index += 1
            child_index = 0
            for child in self.tree.get_children(item):
                self.tree.item(child, tags=("stripe_row",) if child_index % 2 == 0 else ())
                child_index += 1


# Matches tour.py's own _CALLOUT_BG - same light teal tint the tour's own
# callout bubble uses, reused here for visual consistency across the app's
# other "here's something to notice" surfaces.
_ADD_ROW_BG = "#eaf6f8"

# A plain neutral gray for the "stripe_row" tag specifically - NOT
# _ADD_ROW_BG, even though an earlier version of this used the exact same
# teal for both. That coincidentally matches nassie_ttk's own Treeview
# -background selected style (light.tcl/dark.tcl: {selected "#eaf6f8"}),
# so a selected row with no stripe of its own (an even-index share row,
# say) rendered the identical color a stripe_row-tagged row does - read
# as "the alternating pattern is broken here" (reported live, with a
# concrete example: a share row selected mid-sequence looked exactly
# like its own striped neighbor instead of looking selected). Gray reads
# unambiguously as "just a stripe" against that same blue "selected"
# highlight, and can't be confused with it as _ADD_ROW_BG was.
_STRIPE_BG = "#f2f2f2"

# The visual gap between a side panel and the shares list - pack()'s
# padx for the Users panel (_pack_users_panel()), place()'s own width
# math for the Log panel (_place_log_steady()/_place_log_glide()) -
# shared with GUIWizard.__init__'s own panel-width measurement so the
# two can never drift apart. They have to agree exactly:
# _animate_root_width() grows the window by the measured width to keep
# everything already on screen in place (see its own docstring), and if
# that measurement silently left the gutter out, the window would grow
# a few pixels short and the shares list would visibly creep instead of
# staying put (confirmed live - the whole point of this constant).
_PANEL_GUTTER = 8


class _AddRowFeedback:
    """Hover + click feedback, AND full-row centering, for a Treeview's
    pinned "add row" (New Share / New User - see _SortableTree's
    pinned_first). It's a real button in disguise, but a bare Treeview
    row gives none of a button's usual affordances on its own: no cursor
    change on hover, no visible click response (nassie_ttk's Treeview
    -background selected style, see light.tcl/dark.tcl, happens to be
    this exact same teal (_ADD_ROW_BG) - selecting the row doesn't even
    look different), and no way to center a lone "➕" glyph across every
    column at once - a tag's anchor="center" only centers within the
    tree's own #0 column, which for a multi-column tree (the shares
    list's Share Name + Path) left the glyph noticeably off-center
    relative to the WHOLE row, not centered on it - reported live
    ("justify center... including both columns").

    Solves all three by floating a real Label on top of the row instead
    of relying on the Treeview's own text/tag rendering for it - a
    place()d child of the tree itself (same technique _RowActionBar uses
    to float its own icon buttons over a row), sized and positioned to
    the row's FULL bbox (tree.bbox(item) with no column argument covers
    every visible column combined, confirmed live, not just #0) on every
    refresh/resize/scroll. Callers must give the row itself blank #0 text
    (see _add_share_row_id/_add_user_row_id's own insert() calls) - this
    label is what actually shows "➕" now, and it fully covers the
    underlying cell.

    Covering the row like this also means the Treeview's own built-in
    click-to-select handling never actually sees a click land there
    anymore (Tk delivers the event to this label, the topmost widget
    under the pointer, not the tree underneath it) - _on_release() below
    replaces that by calling tree.selection_set() directly, which fires
    the exact same <<TreeviewSelect>> a native click would have, so the
    existing _on_shares_list_select()/_on_users_list_select() handlers
    open the real dialog exactly as before.

    row_id_fn() must return the CURRENT add-row's item id - it's deleted
    and reinserted from scratch on every refresh (see
    _add_share_row_id/_add_user_row_id), so this can't just cache it
    once up front."""
    _HOVER_BG = "#d7eef2"
    _PRESSED_BG = "#c2e6ec"

    def __init__(self, tree, row_id_fn):
        self.tree = tree
        self.row_id_fn = row_id_fn
        # Fallback only - the icon Label below fully covers the row's own
        # bbox once reposition() has run once, so this tag's own
        # background is only ever actually visible for one frame, before
        # that first placement lands.
        tree.tag_configure("add_row", background=_ADD_ROW_BG)
        self.icon = tk.Label(
            tree, text="➕", bg=_ADD_ROW_BG, fg="#0e92ab", font=("TkDefaultFont", 11), cursor="hand2",
        )
        self.icon.bind("<Enter>", self._on_enter, add="+")
        self.icon.bind("<Leave>", self._on_leave, add="+")
        self.icon.bind("<ButtonPress-1>", self._on_press, add="+")
        self.icon.bind("<ButtonRelease-1>", self._on_release, add="+")
        # Same reasoning as _RowActionBar's own identical bindings -
        # scrolling/resizing moves or hides the row without firing
        # <<TreeviewSelect>> or anything else this could otherwise hook,
        # so this has to explicitly reposition (or hide) on each of them
        # to stay glued to the row's own current bbox.
        tree.bind("<Configure>", lambda e: self.reposition(), add="+")
        tree.bind("<MouseWheel>", lambda e: tree.after_idle(self.reposition), add="+")
        tree.bind("<Button-4>", lambda e: tree.after_idle(self.reposition), add="+")
        tree.bind("<Button-5>", lambda e: tree.after_idle(self.reposition), add="+")

    def reposition(self):
        row_id = self.row_id_fn()
        if not row_id or not self.tree.exists(row_id):
            self.icon.place_forget()
            return
        bbox = self.tree.bbox(row_id)
        if not bbox:
            # Scrolled out of view (or the tree hasn't finished laying
            # out yet right after a refresh) - nothing to overlay right
            # now.
            self.icon.place_forget()
            return
        x, y, w, h = bbox
        self.icon.place(x=x, y=y, width=w, height=h)
        self.icon.lift()

    def _on_enter(self, event=None):
        self.icon.configure(bg=self._HOVER_BG)

    def _on_leave(self, event=None):
        self.icon.configure(bg=_ADD_ROW_BG)

    def _on_press(self, event=None):
        self.icon.configure(bg=self._PRESSED_BG)
        self.icon.update_idletasks()

    def _on_release(self, event):
        # event.x/y are relative to this label itself - inside its own
        # current bounds means a genuine click (mouse released without
        # first dragging off the icon), not a press-then-drag-away.
        if 0 <= event.x < self.icon.winfo_width() and 0 <= event.y < self.icon.winfo_height():
            row_id = self.row_id_fn()
            if row_id and self.tree.exists(row_id):
                self.tree.selection_set(row_id)
            self.icon.configure(bg=self._HOVER_BG)
        else:
            self.icon.configure(bg=_ADD_ROW_BG)

    def reset(self):
        # Called at the top of every refresh(), right before the add-row
        # is deleted and reinserted - self.icon persists across refreshes
        # (unlike the row itself), so a press/hover shade left over from
        # the click that triggered this very refresh would otherwise
        # carry straight into the freshly rebuilt row instead of starting
        # idle. reposition() is the caller's job, once the new row
        # actually exists (see _add_share_row_id/_add_user_row_id's own
        # insert() call sites) - this can't do it yet, the old id this
        # still refers to is about to be deleted.
        self.icon.configure(bg=_ADD_ROW_BG)


class _ToggleButton:
    """A toolbar icon button that stays visually "pressed" (a solid fill)
    for as long as its panel is open, instead of the momentary
    press-then-release look a plain button gives - see
    GUIWizard._toggle_users_panel()/_toggle_log_panel(). Plain tk.Label-
    based rather than a ttk style, for the same reason _icon_button
    (shadow=True) is: nassie_ttk's buttons are image-rendered, so a ttk
    style swap has no guaranteed visible effect to build a reliable
    toggle look on."""
    # A dark blue-grey, not the app's own teal accent (used for the
    # add-row/stripe_row tint elsewhere) - reported live as reading too
    # close to the Users button's own 👤 icon color when pressed.
    _PRESSED_BG = "#37474f"
    _SIZE = 34

    def __init__(self, parent, icon, tooltip, command, idle_bg):
        self.idle_bg = idle_bg
        self.pressed = False
        # Fixed pixel size, not just font+padding - different emoji
        # glyphs (👤 vs 📜) render at different natural sizes even at the
        # same font/padding, which left the two toggle buttons visibly
        # different heights (reported live). pack_propagate(False) keeps
        # this frame at exactly _SIZE regardless of what its child needs,
        # and the Label fills it completely, so both buttons end up
        # pixel-identical no matter which glyph is inside.
        # borderwidth+relief give this an actual raised/sunken 3D edge,
        # not just a flat color swap - a color shade alone doesn't read
        # as "pressed" (reported live: "just looks like it's shaded a
        # different color"). raised (idle) vs sunken (pressed) is the
        # standard Tk button look; relief is drawn in the frame's OWN
        # border region, outside where the label (packed fill=both,
        # expand=True, i.e. confined to the frame's interior) ever
        # paints, so it stays visible under the label with no extra
        # padding needed.
        self.frame = tk.Frame(
            parent, width=self._SIZE, height=self._SIZE, bg=idle_bg, relief="raised", borderwidth=1,
        )
        self.frame.pack_propagate(False)
        self.label = tk.Label(
            self.frame, text=icon, font=("TkDefaultFont", 12), bg=idle_bg, fg="#333333", cursor="hand2",
        )
        self.label.pack(fill="both", expand=True)
        for widget in (self.frame, self.label):
            widget.bind("<Button-1>", lambda e: command())
        _Tooltip(self.label, tooltip)

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)

    def set_pressed(self, pressed):
        self.pressed = pressed
        bg = self._PRESSED_BG if pressed else self.idle_bg
        fg = "white" if pressed else "#333333"
        self.frame.configure(bg=bg, relief="sunken" if pressed else "raised")
        self.label.configure(bg=bg, fg=fg)


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
        # Same handler as the Cancel button - the tour's own "open this,
        # then close it" steps (see tour.py) need to know it was actually
        # dismissed either way, not just via the button.
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Username:").grid(row=0, column=0, sticky="e", padx=8, pady=6)
        self.username_var = tk.StringVar()
        username_vcmd = (self.register(self._validate_username_input), "%P")
        self.username_entry = ttk.Entry(
            body, textvariable=self.username_var, validate="key", validatecommand=username_vcmd,
        )
        self.username_entry.grid(row=0, column=1, padx=8, pady=6)

        # Gridded directly in body, not a further-nested Frame with its
        # own independent grid - a nested frame's column widths are
        # negotiated separately from body's, which left "Confirm
        # Password:" (wider than "Username:") pushing its entry out of
        # alignment with the username field above it.
        ttk.Label(body, text="Password:").grid(row=1, column=0, sticky="e", padx=8, pady=6)
        self.password_entry = ttk.Entry(body, show="*")
        self.password_entry.grid(row=1, column=1, padx=8, pady=6)
        ttk.Label(body, text="Confirm Password:").grid(row=2, column=0, sticky="e", padx=8, pady=6)
        self.confirm_entry = ttk.Entry(body, show="*")
        self.confirm_entry.grid(row=2, column=1, padx=8, pady=6)

        # Only relevant when this user is being granted access to a share -
        # not shown for standalone user creation, which has no share (and
        # so no access level) to set at all.
        self.show_access_level = show_access_level
        self.read_only_var = tk.BooleanVar(value=False)
        next_row = 3
        if show_access_level:
            ttk.Checkbutton(
                body, text="Read-only access", variable=self.read_only_var
            ).grid(row=3, column=0, columnspan=2, pady=(0, 6))
            next_row = 4

        # Cancel first (ends up on the left), OK second (ends up on the
        # right, as the primary/default action) - see CreateShareDialog's
        # _build_name_page for the same convention and why.
        btn_frame = ttk.Frame(body)
        btn_frame.grid(row=next_row, column=0, columnspan=2, pady=10)
        # Kept as an attribute (unlike every other dialog's Cancel
        # button) so the tour's own "New User" step (see tour.py) can
        # point a highlight box directly at it once this dialog opens.
        self.cancel_button = _icon_button(btn_frame, "✖", "Cancel", self._on_cancel, shadow=True)
        self.cancel_button.pack(side="left", padx=4)
        _icon_button(btn_frame, "✔", "OK", self._on_ok, shadow=True).pack(side="left", padx=4)

        self.username_entry.focus_set()
        _center_over_parent(self, parent)
        _bring_window_to_front(self)
        if self._app is not None:
            # Deferred, not fired synchronously right here - the tour's
            # own "Cancel" step (see tour.py) responds to this by
            # creating a NEW Toplevel (the callout) parented to THIS
            # dialog, right in the middle of this dialog's own
            # __init__, before grab_set()/wait_window() have even run -
            # confirmed live, that reentrant construction left the
            # callout stuck at a degenerate 1x1 placement that never
            # settled. after(0, ...) runs it once wait_window()'s own
            # event loop is pumping normally instead, after this dialog
            # is actually fully constructed and mapped.
            self.after(0, lambda: self._app._notify_tour("user_dialog_opened", window=self))
        self.grab_set()
        self.wait_window(self)

    def _on_cancel(self):
        if self._app is not None:
            # See GUIWizard._tour_blocks_closing()'s docstring - lets the
            # dedicated "Cancel" step's own Cancel click through as
            # normal, blocks it everywhere else in the tour (e.g. midway
            # through "Username and Password") so it can't strand the
            # tour pointed at a field on a dialog that no longer exists.
            if self._app._tour_blocks_closing(self, "user_dialog_cancelled"):
                return
            self._app._notify_tour("user_dialog_cancelled", window=self)
        self.destroy()

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
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text=prompt).grid(row=0, column=0, columnspan=2, padx=8, pady=6)
        self.choice_var = tk.StringVar(value=choices[0])
        combo = ttk.Combobox(body, textvariable=self.choice_var, values=choices, state="readonly")
        combo.grid(row=1, column=0, columnspan=2, padx=8, pady=6)

        # Cancel first (left), primary action second (right) - see
        # AddUserDialog's identical btn_frame for the same convention.
        btn_frame = ttk.Frame(body)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)
        _icon_button(btn_frame, "✖", "Cancel", self.destroy, shadow=True).pack(side="left", padx=4)
        _icon_button(btn_frame, "✔", ok_label, self._on_ok, shadow=True).pack(side="left", padx=4)

        _center_over_parent(self, parent)
        _bring_window_to_front(self)
        self.grab_set()
        self.wait_window(self)

    def _on_ok(self):
        self.result = self.choice_var.get()
        self.destroy()


class PasswordPromptDialog(tk.Toplevel):
    """Asks for one password, styled to match every other NASsie dialog
    (✔/✖ icon buttons - see AddUserDialog/ChoiceDialog) instead of the
    stdlib tkinter.simpledialog.askstring()'s plain OK/Cancel text
    buttons - reused everywhere a current or new password needs asking
    for (QR code generation, password resets, granting access to an
    existing account, ...), which were the only prompts left still using
    that plain stdlib look instead of NASsie's own (requested live:
    "we've also used check marks and x everywhere else"). result is the
    entered password, or None if cancelled - same contract as
    simpledialog.askstring() itself, so this drops straight in wherever
    that was used before.

    on_shown (optional) fires with this dialog once it's fully built and
    positioned, right before the modal grab - the one caller that needs
    it (QR code's own tour step, see tour.py) uses it to notify the tour
    this dialog just opened, same as CreateShareDialog/AddUserDialog do
    from inside their own __init__ - but as an opt-in callback here
    instead of hardcoded into this class, since the other three callers
    of this same dialog have nothing to do with that specific tour step."""
    def __init__(self, parent, title, prompt, on_shown=None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text=prompt, wraplength=280, justify="left").grid(
            row=0, column=0, columnspan=2, padx=8, pady=(8, 4), sticky="w"
        )
        self.password_var = tk.StringVar()
        entry = ttk.Entry(body, textvariable=self.password_var, show="*", width=32)
        entry.grid(row=1, column=0, columnspan=2, padx=8, pady=4)
        entry.focus_set()
        # Return-to-confirm - a password field is the one place in this
        # dialog where typing then reaching for the mouse just to click
        # the checkmark reads as an unnecessary extra step.
        entry.bind("<Return>", lambda e: self._on_ok())

        # Cancel first (left), primary action second (right) - see
        # AddUserDialog's identical btn_frame for the same convention.
        btn_frame = ttk.Frame(body)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)
        # Kept on self (not local-only) - see AddUserDialog's identical
        # self.cancel_button for why: the tour's own "click Cancel" step
        # (see tour.py) needs a real widget handle to point at, the last
        # control at the BOTTOM of this stacked prompt/entry/buttons
        # layout.
        self.cancel_button = _icon_button(btn_frame, "✖", "Cancel", self.destroy, shadow=True)
        self.cancel_button.pack(side="left", padx=4)
        _icon_button(btn_frame, "✔", "OK", self._on_ok, shadow=True).pack(side="left", padx=4)

        _center_over_parent(self, parent)
        _bring_window_to_front(self)
        if on_shown is not None:
            on_shown(self)
        self.grab_set()
        self.wait_window(self)

    def _on_ok(self):
        self.result = self.password_var.get()
        self.destroy()


class QrCodeDialog(tk.Toplevel):
    """Shows a LockNAS bridge QR code for one just-created (or just-granted)
    user. Only ever constructible with a payload already in hand - NASsie
    doesn't persist plaintext passwords, so this can't be regenerated later
    for an existing user without changing their password again first (see
    QR_PASSWORD_RESET_NOTE)."""
    def __init__(self, parent, share_name, username, payload):
        super().__init__(parent)
        if platform.system() == "Linux":
            self.withdraw()
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
        _center_over_parent(self, parent)
        _bring_window_to_front(self)
        self.grab_set()
        self.wait_window(self)


class CreateShareDialog(tk.Toplevel):
    """Reached via the shares list's "+" row (row #1 - a real Treeview
    row, see _build_shares_page()'s add_row tag) - a second share is
    uncommon enough that this doesn't need permanent space in the main
    window. Users are added afterward,
    from the shares list itself (New/Attach User) - not here; there's no
    reason share creation and granting access need to be the same step.

    A real popup, not an inline expansion of the "+" row itself - an
    earlier version of this session tried the row-expands-in-place
    approach and it read wrong in practice ("you've rebuilt the form
    inside of this row," reported live) - the row's only job is to be a
    nicer-looking trigger for this dialog than a toolbar button was."""
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
        # to-left, so OK (the primary/default action) ends up rightmost
        # with Cancel just to its left.
        action_frame = ttk.Frame(self.name_page)
        action_frame.pack(fill="x", padx=8, pady=(4, 8))
        _icon_button(action_frame, "✔", "OK", self._confirm_name, shadow=True).pack(side="right")
        _icon_button(action_frame, "✖", "Cancel", self._on_close, shadow=True).pack(side="right", padx=(0, 4))

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
        self.path_entry = ttk.Entry(form, width=32, state="readonly", cursor="arrow")
        self.path_entry.grid(row=0, column=1, sticky="w", pady=4)
        _icon_button(form, "📂", "Browse", self._browse_path).grid(row=0, column=2, padx=4)

        # Back stays isolated on the left (a navigation action, not part
        # of the confirm/cancel decision) - Cancel and Create Share
        # (the primary/default action) are grouped together on the
        # right, same reasoning and pack order as _build_name_page's.
        action_frame = ttk.Frame(self.path_page)
        action_frame.pack(fill="x", padx=8, pady=(4, 8))
        _icon_button(action_frame, "◀", "Back", self._show_name_page, shadow=True).pack(side="left")
        # shadow=True returns the shadow-casting Frame, not the ttk.Button
        # itself - .button reaches the real one, needed here since
        # _apply_done()/its own start disable this via .configure(state=
        # ...) after the fact (every OTHER shadowed button in this dialog
        # is fire-and-forget, never touched again post-creation).
        self.create_button = _icon_button(action_frame, "✔", "OK", self._on_create_share, shadow=True)
        self.create_button.pack(side="right")
        _icon_button(action_frame, "✖", "Cancel", self._on_close, shadow=True).pack(side="right", padx=(0, 4))

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
        # No step here ever expects Cancel/WM-close as the right move
        # (unlike AddUserDialog's dedicated "Cancel" step) - own_close_
        # event=None can never match a real wait_event, so this blocks
        # unconditionally for as long as the tour is actively on a step
        # inside this dialog. See GUIWizard._tour_blocks_closing().
        if self.app._tour_blocks_closing(self, None):
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
        self.create_button.button.configure(state="disabled")

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
            # Release this dialog's modal grab before the follow-on toast
            # rather than nesting a new grab underneath it.
            self.destroy()
            self.app._show_toast(
                "Share Creation",
                f"Share '{self.wizard.share_name}' created.\n\n"
                "Add users from the shares list (New User / Attach User) whenever you're ready.",
            )
            self.app._notify_tour("share_created")
        else:
            self.create_button.button.configure(state="normal")
            messagebox.showerror(
                "Share Creation Failed",
                "Configuration attempt failed.",
                parent=self,
            )


class LogPanel:
    """Raw stdout output from every action (share/user create, delete,
    grant/revoke access, ...) collects here - a docked panel on the main
    window's left edge (toggled via the toolbar's Log button), not a
    separate popup window - see this session's redesign away from popups
    (GUIWizard._toggle_log_panel()) for why. Built once and left alive for
    the app's whole lifetime; toggling only place()s/place_forget()s
    self.frame (see GUIWizard._place_log_glide()/_place_log_steady() -
    the left side needs place(), not pack(), unlike the Users panel on
    the right), so the accumulated log text survives across show/hide
    same as the old popup version did across its own show/withdraw."""
    def __init__(self, root):
        self.frame = ttk.Frame(root)
        self.log_text = ScrolledText(self.frame, state="disabled", width=48)
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

    def append(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")



class UserManagementPanel:
    """Account-level user management, decoupled from any specific share -
    Create User, Change Password, Delete User. A docked panel on the main
    window's right edge (toggled via the toolbar's Users button), not a
    separate popup window - see this session's redesign away from popups
    (GUIWizard._toggle_users_panel()) for why. Built once and left alive
    for the app's whole lifetime, same as LogPanel; toggling only packs/
    unpacks self.frame (see GUIWizard._pack_users_panel() - the right
    side can use plain pack(), unlike the Log panel on the left)."""
    def __init__(self, app, parent):
        self.app = app
        self.wizard = app.wizard
        self.frame = ttk.Frame(parent)

        # No "Manage Users" title above this anymore, and no top padding
        # on body either (below) - both dropped together so this panel's
        # own tree starts at the exact same y as the shares list's tree
        # (_shares_body's tree_frame, packed with zero padding of its
        # own - see _build_shares_page()) instead of sitting lower by
        # whatever the title label plus its own top inset used to cost.
        #
        # Kept on self (not local-only) so other code can reach into
        # this panel's own body - e.g. the tour pointing a step at a
        # specific field inside it.
        self.body = body = ttk.Frame(self.frame)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Set for real in refresh() (the only place the actual row gets
        # (re)created) - see _add_share_row_id's identical reasoning in
        # _build_shares_page().
        self._add_user_row_id = None

        tree_frame = ttk.Frame(body)
        tree_frame.pack(fill="both", expand=True)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.users_list = ttk.Treeview(tree_frame, show="tree headings", height=14)
        self._sort = _SortableTree(
            self.users_list, [("#0", "Username")], key_fn=lambda k, c: self.users_list.item(k, "text"),
            pinned_first=lambda: self._add_user_row_id,
            # This panel's own rows are ACCOUNTS, the same kind of
            # top-level thing shares_list's own share rows are - not
            # ATTACHED users (a share's own nested rows there), which is
            # what gray/"stripe_row" is reserved for now (requested
            # live). "share_stripe" (the add-row's own teal) matches the
            # shares list's identical top-level alternation instead.
            top_level_tag="share_stripe",
        )
        self.users_list.column("#0", width=220, stretch=True)
        self.users_list.grid(row=0, column=0, sticky="nsew")
        # "New User" is row #1 of this same list (see refresh()'s own
        # insert of it), not a separate widget above it - matches the
        # shares list's own "New Share" row exactly (see
        # _build_shares_page()'s identical tag). _AddRowFeedback owns the
        # tag's colors from here on (idle/hover/pressed) - no separate
        # tag_configure needed alongside it.
        self._add_row_feedback = _AddRowFeedback(self.users_list, lambda: self._add_user_row_id)
        # Alternates every OTHER account row with the add-row's own teal,
        # starting one row below the add-row (see
        # _SortableTree._restripe()) - matches the shares list's own
        # top-level alternation ("share_stripe" - see that tag's
        # identical use there). No "stripe_row" (gray) tag_configure here
        # any more - this list's own rows are never nested under
        # anything, so that tag (reserved for a share's own ATTACHED
        # user rows specifically) would never actually get applied here.
        self.users_list.tag_configure("share_stripe", background=_ADD_ROW_BG)
        vscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.users_list.yview)
        vscroll.grid(row=0, column=1, sticky="ns")
        self.users_list.configure(yscrollcommand=vscroll.set)

        # Floating, row-anchored action buttons instead of a fixed side
        # panel - see _RowActionBar's docstring. Constructed (and so
        # bound to <<TreeviewSelect>>) BEFORE _on_users_list_select below
        # - see _build_share_action_bar's identical comment for why that
        # order doesn't matter (its own guard is what hides the bar for
        # the add-row, not binding order).
        self._action_bar = _RowActionBar(self.users_list, vscroll, self._build_action_bar)
        self.users_list.bind("<<TreeviewSelect>>", self._on_users_list_select, add="+")
        self.users_list.bind("<Button-1>", lambda e: _deselect_on_blank_click(self.users_list, e), add="+")

    def _on_users_list_select(self, event=None):
        # Selecting the add-row (see refresh()) opens the real dialog
        # instead of treating it like a normal row, then immediately
        # clears the selection - see _on_shares_list_select's identical
        # reasoning.
        if self._add_user_row_id in self.users_list.selection():
            self.users_list.selection_remove(self._add_user_row_id)
            self._create_new_user()

    def _create_new_user(self):
        existing = {u["username"] for u in self.wizard.list_users()}
        dialog = AddUserDialog(
            self.app.root, existing_usernames=existing, show_access_level=False, app=self.app
        )
        if not dialog.result:
            return
        username = dialog.result["username"]
        password = dialog.result["password"]
        threading.Thread(target=self.app._create_user_worker, args=(username, password), daemon=True).start()

    def _build_action_bar(self, container, item):
        # The add-row (see refresh()) is a real Treeview row so
        # _RowActionBar's own <<TreeviewSelect>> binding calls this for
        # it too, at least once before _on_users_list_select clears the
        # selection - nothing here applies to it.
        if item == self._add_user_row_id:
            return False
        _icon_button(container, "🔑", "Change Password", self._change_password).pack(
            side="left", fill="y", padx=1
        )
        _icon_button(container, "🗑", "Delete User", self._delete_user).pack(side="left", fill="y", padx=1)
        return True

    def refresh(self):
        selected = self._selected_username()
        users = self.wizard.list_users()
        self._add_row_feedback.reset()
        for item in self.users_list.get_children():
            self.users_list.delete(item)
        # Row #1, always - see _add_share_row_id's identical reasoning in
        # _populate_shares_list().
        # Blank text - _AddRowFeedback's own floating icon Label shows the
        # "➕", not this cell (see its own docstring for why).
        self._add_user_row_id = self.users_list.insert("", 0, text="", tags=("add_row",))
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
        # Re-pins the add-row to row #1 itself (see _SortableTree's own
        # pinned_first) regardless of whatever sort is currently active.
        self._sort.reapply()
        self._action_bar.update()
        # Only now - the row this needs to overlay was just deleted and
        # reinserted above under a brand new id (reset() couldn't do this
        # itself; see its own comment).
        self._add_row_feedback.reposition()

    def _selected_username(self):
        selection = self.users_list.selection()
        return self.users_list.item(selection[0], "text") if selection else None

    def _change_password(self):
        self._change_password_flow(confirm_qr=True)

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

        if not messagebox.askokcancel("Change Password", QR_PASSWORD_RESET_NOTE, parent=self.app.root):
            return

        if len(shares) == 1:
            share_name = shares[0]
        else:
            dialog = ChoiceDialog(
                self.app.root, "Choose share", "Change password (and show a QR code) for which share?",
                shares, ok_label="Next",
            )
            if not dialog.result:
                return
            share_name = dialog.result

        password = PasswordPromptDialog(
            self.app.root, "New password", f"New password for '{username}' (replaces their current one):",
        ).result
        if not password:
            return

        # Preserve the user's current access level on this share - a
        # password change shouldn't silently flip them back to read-write.
        share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
        share_user = next((u for u in (share or {}).get("users", []) if u["username"] == username), None)
        read_only = share_user.get("read_only", False) if share_user else False

        threading.Thread(
            target=self.app._grant_access_worker,
            args=(share_name, username, password, read_only, confirm_qr),
            daemon=True,
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
            # freely remove. Detaching from shares is still fine;
            # deleting the account itself is not NASsie's call to make.
            messagebox.showinfo(
                "Delete User",
                f"'{username}' is an existing computer account, not one NASsie created - NASsie won't "
                "delete it. Detach it from its shares instead, or delete the account itself from "
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
        self._attaching_labels = {}
        # Counts real toggles while the tour's own "Permission" step (see
        # tour.py) is showing - that step wants the user to see BOTH
        # states (read-only and read-write) before moving on, which
        # means two clicks, not one - see _change_access_done().
        self._tour_permission_clicks = 0

        # className sets WM_CLASS, which is what taskbars/docks/app-switchers
        # use to match this running window back to nassie.desktop (and thus
        # its Icon=) - without it Tk defaults to the generic class "Tk" and
        # the tray/taskbar icon can end up generic even though the titlebar
        # icon (set below) looks right.
        self.root = tk.Tk(className="NASsie")
        self.root.title("NASsie")
        # NASsie's own recolored fork of the Sun Valley ttk theme (see
        # nassie_ttk/__init__.py) - applied before the Icon.TButton/
        # Treeview style tweaks below so those layer on top of it rather
        # than getting overwritten by the theme switch.
        nassie_ttk.set_theme("light")
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
        # 32 was tuned for the old default theme's button image size -
        # nassie_ttk's own Icon.TButton needs 34px to render without
        # clipping (confirmed live: winfo_reqheight() reports 34, not the
        # 32 this used to match) - a couple of pixels of headroom beyond
        # that so it's not sitting exactly on the edge again the next
        # time either changes slightly.
        ttk.Style(self.root).configure("Treeview", rowheight=36)
        # nassie_ttk gives a readonly TEntry (CreateShareDialog's own
        # path_entry - the only one in the app) the exact same white
        # fieldbackground as an editable one, unlike the old default
        # theme, which visibly grayed it out - readonly here means "set
        # via Browse, not typed" (see path_entry's own state="readonly"
        # comment), and looking identical to an editable field gave no
        # visual cue of that. Global on TEntry rather than a dedicated
        # style since path_entry is the only readonly one that exists.
        ttk.Style(self.root).map(
            "TEntry",
            fieldbackground=[("readonly", "#e5e5e5")],
            foreground=[("readonly", "#666666")],
        )
        # Tk has no built-in "center on screen" - left alone, the window
        # manager decides placement, which is commonly the top-left corner
        # rather than anywhere near the middle of the display.
        self._load_icon_image()
        self._set_window_icon()
        self._build_header()
        self._build_shares_page()

        # The Users panel is docked inside self._content_row itself
        # (packed side="right" - unaffected by anything below). The Log
        # panel is NOT - it's a sibling of self._content_row inside
        # self._main_area instead, since it needs place() (see
        # _place_log_steady()/_place_log_glide()), not pack() - see
        # _toggle_users_panel()/_toggle_log_panel() and their own class
        # docstrings for why these are panels, not separate windows.
        #
        # Users is the pack()-based, always-smooth side (matching what
        # Log used to be) and Log is the place()-based, jump+scrim side
        # (matching what Users used to be) - swapped from the original
        # left/right assignment per explicit request, since the
        # place()-based side is measurably heavier for the WM to
        # animate regardless of which panel it's carrying (confirmed
        # live, repeatedly - see git history) - making the FAR more
        # frequently used panel (Users) the side that's cheap, and the
        # rarely-opened one (Log) the side that pays that cost, was a
        # direct, explicit tradeoff, not a technical requirement.
        self._log_panel = LogPanel(self._main_area)
        self._user_mgmt_panel = UserManagementPanel(self, self._content_row)
        # A plain, childless frame placed on top of the Log panel during
        # its open/close animation - see _toggle_log_panel()'s own
        # comment for why this, not a per-frame root resize, is what
        # actually animates now. Themed (ttk, not tk) so it matches
        # whatever's normally visible there with no color to hand-tune.
        self._log_scrim = ttk.Frame(self._main_area)
        self._users_panel_open = False
        self._log_panel_open = False
        # Measured once, unpacked - see _toggle_users_panel()/
        # _toggle_log_panel() for why the window has to grow/shrink by
        # exactly this much rather than a guessed constant.
        self.root.update_idletasks()
        self._users_panel_width = self._user_mgmt_panel.frame.winfo_reqwidth() + _PANEL_GUTTER
        self._log_panel_width = self._log_panel.frame.winfo_reqwidth() + _PANEL_GUTTER
        self._place_log_steady()
        # None until the first time _animate_root_width() actually needs
        # to reposition root (grow_left=True - the Users panel) - see
        # its own docstring for what this ends up measuring and why it
        # can only be measured against a real, already-mapped, already-
        # decorations-stripped root, not calibrated eagerly here.
        self._wm_reposition_drift = None

        self._refresh_all_lists()

        # Clears whichever row is currently selected (in either list) once
        # the user clicks away to a totally different application -
        # requested live, same idea as _deselect_on_blank_click() but for
        # leaving the window entirely rather than clicking its own empty
        # background. <FocusOut> alone can't tell those apart from
        # focus moving to one of NASsie's OWN dialogs (AddUserDialog,
        # CreateShareDialog, ...), which ALSO fires it on root the moment
        # one of them opens and steals focus - deselecting then would undo
        # a selection those dialogs are actively acting on. focus_get()
        # right after distinguishes the two: it returns whichever widget
        # currently holds focus among ALL of this application's own
        # windows (Tk tracks that across every Toplevel in the same
        # interpreter, not just root), so it comes back non-None whenever
        # focus only moved to one of those, and None only once it's left
        # every NASsie window there is.
        self.root.bind("<FocusOut>", self._on_root_focus_out, add="+")

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
        share_bar_w = self._measure_action_bar_width(("📖", "📷", "➖", "+👤", "🔗", "🗑"))
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
        # Cached with no panels open (this is the first geometry() call
        # root ever gets) - _animate_root_width() uses this as its own
        # floor, so closing both panels can never shrink the window
        # below its true no-panel baseline no matter what rounding a
        # multi-step animation introduces along the way.
        self._base_width = width
        # Without a floor, shrinking the window below what the list+actions
        # layout actually needs squishes it into an overlapping, unreadable
        # mess instead of just clipping/scrolling - the window is otherwise
        # freely resizable (no resizable(False) call), so this is the only
        # thing stopping that.
        self.root.minsize(width, height)
        # Native decorations (WM titlebar on Linux, DWM on Windows, AppKit
        # on macOS) already round the window everywhere BUT this - see
        # window_corners.py for why the bottom two corners specifically
        # need it and every other platform doesn't.
        self._reapply_corners = window_corners.apply(self.root)
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
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=12, pady=(12, 0))

        # The logo alone, no "NASsie" text label alongside it - the window's
        # own titlebar already carries the name (see __init__'s root.title()),
        # so repeating it here was pure redundancy. place(), not
        # pack(side="left", expand=True) (an earlier version of this) - a
        # packed sibling recenters within whatever cavity is LEFT once
        # something else claims space in the same frame, so the busy
        # bar's own side="right" packing in _busy_start() below visibly
        # shifted the logo left every time it appeared/disappeared
        # (reported live). place(relx=0.5, anchor="n") centers against
        # this frame's own full, constant width instead - the busy bar
        # (a plain sibling place() coexists fine with, unlike a second
        # pack() geometry manager on the same parent) can come and go
        # with zero effect on where the logo sits horizontally.
        icon_label = None
        if self._icon_image:
            scale = max(1, self._icon_image.width() // 56)
            self._header_icon_image = self._icon_image.subsample(scale, scale)
            icon_label = ttk.Label(header, image=self._header_icon_image)

        # Indeterminate progress bar doubling as a busy spinner - packed
        # only while at least one background action is running (see
        # _busy_start/_busy_stop), not a fixed part of the layout, so it
        # doesn't take up space or draw the eye when nothing is happening.
        self._busy_bar = ttk.Progressbar(header, mode="indeterminate", length=100)
        self._busy_count = 0

        # Fixes header's own height, so the logo doesn't shift VERTICALLY
        # either - place()'d children (the logo, above) don't contribute
        # to a frame's own natural size the way pack()'d ones do, and the
        # busy bar is shorter than the logo, so without this, header's
        # own height would just follow whichever of the two happens to be
        # packed at the time (nothing, when idle - collapsing header to
        # near zero) rather than staying constant. Measured against both
        # candidates directly instead of a guessed constant - packs the
        # busy bar just long enough to read its own real height, then
        # unpacks it again; _busy_start() below repacks it for real once
        # a background action actually starts.
        header.update_idletasks()
        icon_h = icon_label.winfo_reqheight() if icon_label else 0
        self._busy_bar.pack(side="right", padx=(0, 10))
        header.update_idletasks()
        busy_h = self._busy_bar.winfo_reqheight()
        self._busy_bar.pack_forget()
        header.configure(height=max(icon_h, busy_h))
        header.pack_propagate(False)

        if icon_label is not None:
            icon_label.place(relx=0.5, y=0, anchor="n")

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

    def _show_toast(self, title, text, parent=None, on_close=None):
        # Single slot, not a stack - a new toast (e.g. a second action
        # finishing while the first one's still showing) replaces
        # whatever's currently up rather than piling notices on top of
        # each other. _dismiss() is safe to call on an already-dismissed
        # toast (it no-ops), and firing its on_close early here is
        # deliberate, not just a side effect - see _Toast's own docstring
        # for why the tour relies on this callback to gate its
        # "Confirmation" steps; leaving a stale one to fire on its own
        # timeout later would double-advance the tour out from under
        # whatever step is showing by then.
        existing = getattr(self, "_active_toast", None)
        if existing is not None:
            existing._dismiss()
        # anchor=self.shares_list, always - every one of these actions
        # (share/user created, attached, detached, deleted, removed) is
        # about the main pane's own content regardless of which window
        # `parent` happens to be, and centering on root's own full width
        # instead would drift off-center from the actual shares list
        # whenever a side panel is open and root is wider than it.
        self._active_toast = _Toast(
            parent or self.root, title, text, on_close=on_close, anchor=self.shares_list,
        )

    def _notify_tour(self, event, window=None):
        # The active GuiTour (if any) advances itself on real actions
        # happening rather than a "Next" button, and follows the user into
        # whichever dialog just opened - see GuiTour.on_event().
        tour = getattr(self, "_tour", None)
        if tour:
            tour.on_event(event, window=window)

    def _tour_waiting_on(self, event):
        # True only while the tour is actually showing a step whose
        # wait_event is this one - used to guard Delete Share/Detach so
        # the tour's own "click this to see what it does" steps (see
        # tour.py) can never actually perform the destructive action
        # regardless of what gets clicked, not just rely on the user
        # correctly hitting Cancel/No.
        tour = getattr(self, "_tour", None)
        return bool(tour and tour._callout is not None and tour._wait_event == event)

    def _tour_blocks_closing(self, dialog, own_close_event):
        # True while the tour is actively pointed at THIS exact dialog and
        # wants something OTHER than closing it right now - e.g. it's on
        # the "Username and Password" step (wait_event="user_created"),
        # not the dedicated "Cancel" step (wait_event=
        # "user_dialog_cancelled", own_close_event for AddUserDialog).
        # Without this, Cancel/the WM close button/Alt+F4 could always
        # close the dialog anyway even though the tour's on_event() never
        # matched and so never advanced - leaving the tour's callout
        # stuck pointing at a field on an already-destroyed dialog, with
        # no way forward except Skip Tour. Reported live as wanting the
        # guide to be "almost undefeatable" - this is the general version
        # of the same idea _tour_waiting_on() already applies to Delete
        # Share/Detach, aimed at premature exits instead of destructive
        # clicks.
        #
        # own_close_event itself is always let through - some dialogs
        # have a step where closing/cancelling IS the expected action
        # (AddUserDialog's "Cancel" step); most (CreateShareDialog,
        # UserManagementWindow) have no such step at all, so pass an
        # event string that never appears in their own step list.
        tour = getattr(self, "_tour", None)
        return bool(
            tour and tour._callout is not None and tour._active_window is dialog
            and tour._wait_event != own_close_event
        )

    def _tour_flash_name_error(self, message):
        tour = getattr(self, "_tour", None)
        if tour:
            tour.show_name_error(message)

    def _offer_tour_resume(self):
        # Picks back up at (close to) the furthest step actually reached
        # last time (tour_progress_index()), not necessarily literally -
        # a step that only resolves against a dialog from the OLD process
        # can't be resumed exactly, so GuiTour.start()'s own
        # _resolve_resume_index() steps back to the nearest one that can
        # (see its comment). Still real progress kept either way, not a
        # full restart from step one every time - the whole reason this
        # tracks a step index at all, not just a bare "started" flag.
        if messagebox.askyesno(
            "Resume Tour",
            "It looks like the guided tour didn't finish last time.\n\n"
            "Continue where you left off?",
        ):
            self._start_tour(resume_index=tour_progress_index())
        else:
            mark_tour_completed()

    def _start_tour(self, resume_index=0):
        # Rebuilt each time rather than cached - a stale in-memory GuiTour
        # from earlier this same process would otherwise carry its own
        # leftover index/state into a fresh run instead of starting clean
        # at exactly resume_index.
        self._tour = GuiTour(self)
        self._tour.start(resume_index=resume_index)

    def _build_shares_page(self):
        # The only page - see the shares/users restructuring discussion:
        # shares as expandable parent rows, each attached user nested
        # underneath as a child row. Most of the old "which share?" picker
        # dialogs (change access level, change password, ...) disappear
        # entirely this way, since the share is already known from
        # whichever row is selected.
        #
        # Toolbar now only holds the Users/Log toggle buttons (far right/
        # left) - "New Share" moved into the shares list itself, as row
        # #1 of the Treeview (see the "add_row" tag/self._add_share_row_id
        # below - a real row of the list, not a separate widget floating
        # above it, per what was actually asked for after an earlier
        # separate-strip version didn't land right), and "Manage Users"/
        # "View Log" stopped opening separate popup windows entirely -
        # both are docked panels toggled from here now instead.
        toolbar_bg = ttk.Style(self.root).lookup("TFrame", "background") or "#f3f3f3"
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill="x", padx=8, pady=(8, 0))
        self._users_toggle_btn = _ToggleButton(
            toolbar, "👤", "Manage Users", self._toggle_users_panel, toolbar_bg,
        )
        self._users_toggle_btn.pack(side="right")
        self._log_toggle_btn = _ToggleButton(
            # 📋 (clipboard) reads as "notes/tasks", not "log" - 📜
            # (scroll) is the more common "history/log" convention.
            toolbar, "📜", "View Log", self._toggle_log_panel, toolbar_bg,
        )
        # Not packed - the Log panel is the one that has to sit on the
        # left (see _toggle_log_panel()'s own docstring for why that
        # side can't avoid moving the window), which requested-live
        # confirmed is a visible glitch on this system with no
        # available fix. Hiding the only way to reach it rather than
        # shipping a feature with a known, unfixable rough edge. The
        # button, self._log_panel, and _toggle_log_panel() itself are
        # all still fully intact - .pack(side="left") is the one line
        # to restore if this ever needs to come back (e.g. this glitch
        # may be Linux/Mutter-specific - untested on Windows/macOS).
        # Log CAPTURE itself (self._log_panel.append(), called
        # throughout for every action) is untouched either way - only
        # the ability to actually open and look at it is gone.

        # _main_area holds two PLACE()-managed children (not packed) -
        # self._content_row (the shares list + Users panel, unchanged
        # internally) and, once built below, the Log panel - see
        # _place_log_steady()/_place_log_glide()'s own docstrings for
        # why the Log panel needs place() instead of pack() here, and
        # git history for the two pack()-based designs (a lockstep
        # per-frame configure(width=...), then a "no x movement at all"
        # version) that came before it and were both reported live as
        # visibly wrong - back when Users, not Log, was on this side.
        self._main_area = ttk.Frame(self.root)
        self._main_area.pack(fill="both", expand=True, padx=8, pady=8)

        self._content_row = ttk.Frame(self._main_area)
        self._content_row.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)

        self._shares_body = ttk.Frame(self._content_row)
        # Set for real in _populate_shares_list() (the only place the
        # actual row gets (re)created) - None until the first refresh so
        # early <<TreeviewSelect>>/action-bar callbacks have something
        # sane to compare against instead of an AttributeError.
        self._add_share_row_id = None

        tree_frame = ttk.Frame(self._shares_body)
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
            pinned_first=lambda: self._add_share_row_id,
            # Top-level rows here are SHARES, not users - "share_stripe"
            # (the add-row's own teal), not "stripe_row" (gray, used for
            # each share's own nested user rows instead - see
            # _restripe()'s own comment for the full two-tier scheme).
            top_level_tag="share_stripe",
        )
        self.shares_list.column("#0", width=220, stretch=False)
        self.shares_list.column("path", width=320, stretch=True)
        self.shares_list.grid(row=0, column=0, sticky="nsew")
        # "New Share" is row #1 of this same list, not a separate widget
        # above it (see _populate_shares_list()'s own insert of it, and
        # add_row_id's docstring there) - styled via a real Treeview tag
        # so it's the exact same row height/font as every other row,
        # just tinted to read as an action rather than data.
        # _AddRowFeedback owns the tag's colors from here on
        # (idle/hover/pressed) - matches the users list's identical use.
        self._add_row_feedback = _AddRowFeedback(self.shares_list, lambda: self._add_share_row_id)
        # Two independent alternations now, not one shared tag - see
        # _SortableTree._restripe()'s own comment for the full scheme.
        # "share_stripe" (the add-row's own teal) alternates each OTHER
        # share row; "stripe_row" (gray) alternates each share's own
        # nested user rows, on their position within just that share.
        self.shares_list.tag_configure("share_stripe", background=_ADD_ROW_BG)
        self.shares_list.tag_configure("stripe_row", background=_STRIPE_BG)

        shares_vscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.shares_list.yview)
        shares_vscroll.grid(row=0, column=1, sticky="ns")
        shares_hscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.shares_list.xview)
        shares_hscroll.grid(row=1, column=0, sticky="ew")
        self.shares_list.configure(yscrollcommand=shares_vscroll.set, xscrollcommand=shares_hscroll.set)

        # Floating, row-anchored action buttons instead of a fixed side
        # panel - see _RowActionBar's docstring. Constructed (and so
        # bound to <<TreeviewSelect>>) BEFORE _on_shares_list_select
        # below, so _build_share_action_bar sees the add-row selected
        # too, at least once - its own guard (see there) is what actually
        # keeps the bar hidden for it, not binding order.
        self._share_action_bar = _RowActionBar(self.shares_list, shares_vscroll, self._build_share_action_bar)
        self.shares_list.bind("<<TreeviewSelect>>", self._on_shares_list_select, add="+")
        self.shares_list.bind("<Button-1>", lambda e: _deselect_on_blank_click(self.shares_list, e), add="+")

        self._shares_body.pack(side="left", fill="both", expand=True)

    def _on_shares_list_select(self, event=None):
        # Selecting the add-row (see _populate_shares_list()) opens the
        # real dialog instead of treating it like a normal row - then
        # immediately clears the selection, both so it doesn't sit there
        # looking "selected" and so _selected_share_and_user() (used all
        # over for "act on whatever's currently selected") never has to
        # know this row exists at all.
        if self._add_share_row_id in self.shares_list.selection():
            self.shares_list.selection_remove(self._add_share_row_id)
            CreateShareDialog(self)
            return
        self._notify_tour("share_selected")

    def _log_reservation(self):
        # How much of self._main_area's width is CURRENTLY committed to
        # the Log panel (its own width plus the gutter after it) when
        # nothing is actively animating - 0 when closed, the full cached
        # width when open. Used to freeze that footprint in place while
        # the Users panel (unrelated) is toggled - see
        # _place_log_steady()'s own docstring.
        return self._log_panel_width if self._log_panel_open else 0

    def _place_log_steady(self):
        # The STEADY-STATE placement (nothing currently mid-glide) for
        # self._content_row and the Log panel, both PLACE()-managed
        # children of self._main_area (see its own comment in __init__
        # for why place(), not pack()). content_row TRACKS
        # self._main_area's live width here (relwidth=1.0), offset by
        # whatever the Log panel currently reserves - this is what lets
        # _toggle_users_panel()'s own _animate_root_width() calls keep
        # working exactly as before: growing/shrinking root grows/
        # shrinks self._main_area (plain pack fill=both/expand=True,
        # untouched), and content_row automatically follows that via
        # place()'s own native relwidth tracking - no separate call
        # needed for THAT, same as it never has been. The Log panel
        # itself is pinned to self._main_area's left edge at whatever
        # its current settled width is (0 or its full natural width),
        # NOT tracking self._main_area live - it only needs to move
        # during ITS OWN glide, handled by _place_log_glide() instead.
        reservation = self._log_reservation()
        self._log_panel.frame.place(
            relx=0, x=0, rely=0, relheight=1.0,
            relwidth=0, width=max(0, reservation - _PANEL_GUTTER),
        )
        self._content_row.place(relx=0, x=reservation, rely=0, relheight=1.0, relwidth=1.0, width=-reservation)

    def _place_log_glide(self, base_width):
        # The GLIDE placement, active only for the duration of a Log
        # panel open/close animation - base_width is self._main_area's
        # width with the Log panel fully CLOSED, captured once right
        # before the glide starts (see _toggle_log_panel()), NOT
        # re-measured per frame.
        #
        # content_row's WIDTH is pinned to that one fixed number
        # (relwidth=0, width=base_width) - it does NOT track
        # self._main_area's width during this glide, on purpose: it's
        # main_area's width itself that's growing/shrinking (via
        # _animate_root_width() below), and if content_row also grew
        # with it, its own on-screen size interval would change every
        # frame, right where the Log panel needs to be. Instead
        # content_row's X (relx=1.0, x=-base_width - i.e. main_area's
        # CURRENT width minus base_width, whatever that is at any given
        # instant) is what tracks the glide, shifting it right by
        # exactly however much main_area has grown past base_width -
        # keeping content_row's own ON-SCREEN SIZE AND POSITION
        # completely still, since window_x (see _animate_root_width's
        # grow_left) shrinks by exactly the same amount this grows by.
        # The Log panel mirrors this: pinned to x=0 (main_area's own
        # left edge, which moves with window_x - that's what makes it
        # look like it's opening on the left) with a width that tracks
        # main_area's growth past base_width (minus the gutter).
        #
        # Both of these are native place() relx/relwidth bindings - Tk
        # recomputes them AS PART OF processing self._main_area's own
        # resize (itself an automatic, single-step consequence of
        # root's geometry() call - see _main_area's plain pack(fill,
        # expand) in __init__), not as a second, separately-timed
        # widget operation the way an explicit per-frame
        # configure(width=...) was - see git history for why THAT
        # caused a real, reported oscillation. There is nothing further
        # this method (or anything else) needs to call once per frame.
        self._log_panel.frame.place(
            relx=0, x=0, rely=0, relheight=1.0, relwidth=1.0, width=-(base_width + _PANEL_GUTTER),
        )
        self._content_row.place(relx=1.0, x=-base_width, rely=0, relheight=1.0, relwidth=0, width=base_width)

    def _pack_users_panel(self):
        # side="right" always claims space from the current right edge
        # of whatever's left in content_row regardless of pack() call
        # order relative to shares_body's own (expand=True) - confirmed
        # live. The Log panel needs place(), not pack(), for the
        # opposite (left) side instead - see _place_log_steady()/
        # _place_log_glide().
        self._user_mgmt_panel.frame.pack(side="right", fill="y", padx=(_PANEL_GUTTER, 0))

    def _animate_root_width(self, delta_width, grow_left=False, on_complete=None, steps=10):
        # Steps the window from its current width to the target width
        # over several real mainloop turns (via after()) instead of one
        # instant geometry() jump - see the earlier version of this
        # method (git history) for why an instant jump flashes at all.
        # steps=1 collapses this to a single, immediate geometry() call
        # instead (still going through the same code path, so the
        # grow_left/minsize/drift-compensation logic below stays
        # identical either way) - see _toggle_log_panel(), which now
        # calls it that way. Confirmed live, repeatedly: moving root's
        # OWN position (grow_left) is real work for the WM - repainting
        # the window's entire content at a new screen location, not just
        # resizing it - and stayed visibly heavier than a same-sized
        # pure resize (which _toggle_users_panel() below has always
        # used, untouched, still at steps=10) no matter how the app-
        # level code moving it was restructured (lockstep per-frame
        # configure(), then place()'s own native tracking, then forcing
        # a flush every step - none of it changed the result, and none
        # of it depended on which specific panel was doing the moving -
        # see GUIWizard.__init__'s own comment on why Log, not Users,
        # is the one that pays this cost now). Rather than pay that
        # per-frame cost 10 times over a glide, _toggle_log_panel() now
        # pays it exactly once - the window jumps straight to its
        # target - and does the actual visible "the panel is opening"
        # animation entirely afterward, via a scrim that only ever
        # touches place(), never root's own geometry at all.
        #
        # grow_left: the Log panel needs the window's LEFT edge to move
        # (opening "to the left"), while self._content_row's own
        # on-screen size/position stays fixed - achieved by moving
        # root's x by the exact same amount its width changes, in the
        # SAME single geometry() call (one Tk/X11 operation, width and
        # position together, not two separately-timed ones).
        # GUIWizard._place_log_glide() sets up a place() binding,
        # before this is ever called, that tracks self._main_area's
        # (and so root's) resize automatically - the same native
        # mechanism that's always made the Users panel below work with
        # nothing but a single geometry() call - so moving root's x and
        # width together in ONE call, right here, is what keeps
        # content_row in place.
        self.root.update_idletasks()
        old_width = self.root.winfo_width()
        old_x = self.root.winfo_x()
        old_y = self.root.winfo_y()
        height = self.root.winfo_height()
        target_width = max(self._base_width, min(old_width + delta_width, self.root.winfo_screenwidth()))
        actual_delta = target_width - old_width
        if actual_delta == 0:
            self.root.minsize(target_width, height)
            if on_complete:
                on_complete()
            return
        if actual_delta < 0:
            # Shrinking - drop the floor to the target BEFORE the first
            # step, not the last. minsize() is a hard floor the WM
            # enforces; a previous grow (of either panel) left it set to
            # the CURRENT (larger) width. Deferring the update to the
            # final step - fine for growing, since every intermediate
            # width during a grow is already >= the old, still-valid
            # floor - silently clamps every intermediate SHRINK request
            # back up to that stale floor instead: confirmed live via
            # instrumented trace, closing right after an opening showed
            # every single intermediate step reporting the OLD (fully
            # open) width, with the window only actually moving on the
            # very last step, all at once - not a jump, an entire
            # animation collapsed into its own last frame.
            self.root.minsize(target_width, height)

        duration_ms = 160
        step_delay = max(1, duration_ms // steps)
        widths = [old_width + round(actual_delta * i / steps) for i in range(1, steps)] + [target_width]
        xs = [max(0, old_x - (w - old_width)) for w in widths] if grow_left else None

        def step(i):
            w = widths[i]
            if grow_left:
                if self._wm_reposition_drift is None:
                    # First time ever repositioning root - measure
                    # Mutter's own quirk here rather than guessing it up
                    # front: this exact window, explicitly repositioned
                    # via geometry(), consistently landed a fixed number
                    # of pixels below wherever it was actually asked to
                    # go (confirmed live, back when root's decorations
                    # were being stripped via Motif hints - root now
                    # keeps its native titlebar, see git history, so this
                    # may measure 0 from here on, but it's cheap and
                    # self-calibrating either way, so it's left in place
                    # rather than assumed away). Measuring this eagerly
                    # at startup didn't work: a withdrawn root, and a
                    # standalone probe window, both measured a drift of
                    # 0 - it has to be measured against an actual
                    # repositioning of root itself, the first time one
                    # happens. Costs one extra round trip, but only
                    # once, ever, across the program's whole lifetime.
                    self.root.geometry(f"{w}x{height}+{xs[i]}+{old_y}")
                    self.root.update_idletasks()
                    self._wm_reposition_drift = self.root.winfo_y() - old_y
                    if self._wm_reposition_drift:
                        self.root.geometry(f"+{xs[i]}+{old_y - self._wm_reposition_drift}")
                else:
                    self.root.geometry(f"{w}x{height}+{xs[i]}+{old_y - self._wm_reposition_drift}")
            else:
                self.root.geometry(f"{w}x{height}")
            if i == steps - 1:
                self.root.minsize(w, height)
                if on_complete:
                    on_complete()
            else:
                self.root.after(step_delay, lambda: step(i + 1))

        step(0)

    def _animate_log_scrim(self, opening, on_complete=None):
        # Pure place()-level animation - no root.geometry() calls at all
        # (see _animate_root_width()'s own comment on steps=1 for why
        # root only ever jumps straight to its target now, before this
        # ever runs). self._log_scrim is a plain, childless themed frame
        # placed on top of the Log panel's own area, covering
        # progressively less of it (opening) or more (closing). Its
        # LEFT edge is what moves - the side nearest the toggle button
        # that triggered this and self._main_area's own left edge - so
        # opening reads as sliding out from there toward
        # self._content_row, closing as retracting the same way in
        # reverse.
        panel_width = self._log_panel_width - _PANEL_GUTTER
        steps = 10
        duration_ms = 160
        step_delay = max(1, duration_ms // steps)

        def step(i):
            revealed = i / steps if opening else 1 - i / steps
            covered_width = max(0, round(panel_width * (1 - revealed)))
            if covered_width <= 0:
                self._log_scrim.place_forget()
            else:
                self._log_scrim.place(x=panel_width - covered_width, y=0, width=covered_width, relheight=1.0)
                self._log_scrim.lift()
            if i < steps:
                self.root.after(step_delay, lambda: step(i + 1))
            elif on_complete:
                on_complete()

        step(0)

    def _toggle_users_panel(self):
        # The cheap, pack()-based side (see GUIWizard.__init__'s own
        # comment on why Users, not Log, was moved here) - identical in
        # shape to what this method looked like before the swap, just
        # targeting self._user_mgmt_panel/self._users_panel_width/
        # self._users_panel_open instead of the Log panel's equivalents.
        if self._users_panel_open:
            # See _tour_blocks_closing()'s docstring - lets the dedicated
            # "Close" step's own click-to-close-the-panel through as
            # normal (its own wait_event, "user_mgmt_closed"), blocks it
            # on every earlier step the panel is active for (e.g. "New
            # User", still waiting on the inline add-row it opens).
            if self._tour_blocks_closing(self._user_mgmt_panel, "user_mgmt_closed"):
                return
            self._users_panel_open = False
            self._users_toggle_btn.set_pressed(False)
            def _done():
                self._user_mgmt_panel.frame.pack_forget()
                self._notify_tour("user_mgmt_closed")
                # Root just settled at its new (narrower) width - see
                # window_corners.apply()'s own comment on why <Configure>
                # alone can't be trusted to leave the bottom corners
                # correctly shaped once a resize like this one finishes.
                self._reapply_corners()
            self._animate_root_width(-self._users_panel_width, on_complete=_done)
        else:
            self._users_panel_open = True
            self._users_toggle_btn.set_pressed(True)
            self._pack_users_panel()
            self._user_mgmt_panel.refresh()
            def _done():
                self._notify_tour("user_mgmt_opened", window=self._user_mgmt_panel)
                self._reapply_corners()
            self._animate_root_width(self._users_panel_width, on_complete=_done)

    def _toggle_log_panel(self):
        # The place()-based, jump+scrim side (see GUIWizard.__init__'s
        # own comment on why Log, not Users, was moved here) - identical
        # in shape to what _toggle_users_panel() looked like before the
        # swap, just targeting self._log_panel/self._log_panel_width/
        # self._log_panel_open instead of the Users panel's equivalents.
        # No tour step ever points at this one, so no _tour_blocks_closing
        # guard needed, and no .refresh() call - LogPanel.append() keeps
        # its content current on its own, unlike UserManagementPanel's
        # Treeview.
        self.root.update_idletasks()
        base_width = self._main_area.winfo_width() - self._log_reservation()
        if self._log_panel_open:
            self._log_panel_open = False
            self._log_toggle_btn.set_pressed(False)
            # The scrim covers the panel FIRST, entirely via place() -
            # no root.geometry() calls at all, see _animate_log_scrim()
            # - and only once it's fully covered (nothing left on
            # screen to disturb) does root actually shrink, in ONE
            # single jump (steps=1) rather than an animated glide - see
            # _animate_root_width()'s own comment on why. That jump
            # happens entirely behind the now-opaque scrim, so it's
            # never visible either way.
            def _covered():
                self._place_log_glide(base_width)
                def _jumped():
                    self._place_log_steady()
                    self._log_scrim.place_forget()
                    # See _toggle_users_panel()'s identical call for why.
                    self._reapply_corners()
                self._animate_root_width(-self._log_panel_width, grow_left=True, on_complete=_jumped, steps=1)
            self._animate_log_scrim(opening=False, on_complete=_covered)
        else:
            self._log_panel_open = True
            self._log_toggle_btn.set_pressed(True)
            panel_width = self._log_panel_width - _PANEL_GUTTER
            # The scrim covers the panel's full FINAL area BEFORE root
            # even jumps - using panel_width, the cached target (known
            # in advance, no need to wait and measure it after the
            # jump) - so the panel's real content (always packed, never
            # hidden - see LogPanel.__init__) is never visible even for
            # one frame before the scrim is there to cover it.
            self._log_scrim.place(x=0, y=0, width=panel_width, relheight=1.0)
            self._log_scrim.lift()
            self._place_log_glide(base_width)
            def _jumped():
                self._place_log_steady()
                self._animate_log_scrim(opening=True)
                # See _toggle_users_panel()'s identical call for why.
                self._reapply_corners()
            self._animate_root_width(self._log_panel_width, grow_left=True, on_complete=_jumped, steps=1)

    def _build_share_action_bar(self, container, item):
        # The add-row (see _populate_shares_list()) is a real Treeview
        # row so _RowActionBar's own <<TreeviewSelect>> binding calls
        # this for it too, at least once before _on_shares_list_select
        # clears the selection - nothing here applies to it.
        if item == self._add_share_row_id:
            return False
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
        if username:
            # A user row's own actions are scoped to that user's access to
            # this share, not the share itself - New User/Attach
            # User/Delete Share belong to the share row (see below), not
            # here.
            # Order: permission toggle, QR code, detach - read/write
            # first (the state someone checks most often), detach last
            # (the destructive one, furthest from an accidental click).
            #
            # The icon itself doubles as the access-level badge (open book
            # = read-only, memo = read-write) as well as the toggle
            # control - clicking it flips the level immediately, no
            # confirmation dialog (see _change_access_level_for_
            # selection()). The row's own label (see
            # _populate_shares_list()) carries the same information for
            # when this bar isn't showing at all (nothing selected).
            share_name, _ = self._selected_share_and_user()
            share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
            share_user = next((u for u in (share or {}).get("users", []) if u["username"] == username), None)
            read_only = bool(share_user and share_user.get("read_only"))
            icon, tip = ("📖", "Read-only - click to make read-write") if read_only else (
                "📝", "Read-write - click to make read-only"
            )
            _icon_button(
                container, icon, tip, self._change_access_level_for_selection
            ).pack(side="left", fill="y", padx=1)
            # No dedicated "QR code" emoji exists in Unicode (checked
            # against the full character database, nothing named QR/
            # barcode anywhere in it) - a camera stands in for the actual
            # action (point your phone's camera at it) instead of trying
            # to depict the code itself.
            _icon_button(container, "📷", "Show QR Code", self._show_qr_for_selection).pack(
                side="left", fill="y", padx=1
            )
            _icon_button(
                container, "➖", "Detach User", self._unattach_selected_user
            ).pack(side="left", fill="y", padx=1)
        else:
            _icon_button(container, "+👤", "New User", self._new_user_for_selected_share).pack(
                side="left", fill="y", padx=1
            )
            _icon_button(
                container, "🔗", "Attach User", self._attach_user_to_selected_share
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
        labels = [self._attaching_labels.get(u, u) for u in self._attaching_candidates]
        label_to_username = {self._attaching_labels.get(u, u): u for u in self._attaching_candidates}
        combo = ttk.Combobox(container, textvariable=var, values=labels, state="readonly")
        combo.pack(side="left", fill="both", expand=True, padx=1)
        combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self._commit_inline_attach(label_to_username.get(var.get(), var.get())),
        )
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
        self._attaching_labels = {}
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

    def _on_root_focus_out(self, event=None):
        # See __init__'s own comment on this binding for why focus_get()
        # is what actually distinguishes "clicked away to another
        # application" from "focus moved to one of NASsie's own dialogs".
        try:
            if self.root.focus_get() is not None:
                return
            self.shares_list.selection_remove(*self.shares_list.selection())
            self._user_mgmt_panel.users_list.selection_remove(*self._user_mgmt_panel.users_list.selection())
        except tk.TclError:
            # Root (or one of these lists) mid-teardown - this can fire
            # during final window close, nothing left worth clearing.
            pass

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
            self._user_mgmt_panel.refresh()

        self.root.after(0, apply)

    def _append_log(self, text):
        self._log_panel.append(text)

    def _failure_text(self, summary, log_output):
        # Surfaces the actual captured command output/error inline,
        # rather than just pointing at the Log panel - it's hidden by
        # default (a docked panel the user has to explicitly toggle open,
        # see LogPanel's own docstring), so a bare "see log" left the
        # real reason (e.g. "Permission denied" - the command wasn't run
        # with raised/elevated permissions) invisible unless the user
        # already knew to go open that panel first.
        detail = log_output.strip()
        return summary if not detail else f"{summary}\n\n{detail}"

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

        self._add_row_feedback.reset()
        for item in self.shares_list.get_children():
            self.shares_list.delete(item)

        # Row #1, always - inserted fresh on every refresh (simplest way
        # to guarantee it survives the delete-everything above, and
        # _shares_sort.reapply() below can never sort it out of first
        # place either - see the explicit move() after it).
        # Blank text - _AddRowFeedback's own floating icon Label shows the
        # "➕", not this cell (see its own docstring for why).
        self._add_share_row_id = self.shares_list.insert(
            "", 0, text="", values=("", ""), tags=("add_row",),
        )

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

        # Re-pins the add-row to row #1 itself (see _SortableTree's own
        # pinned_first) regardless of whatever sort is currently active.
        self._shares_sort.reapply()
        self._share_action_bar.update()
        # Only now - the row this needs to overlay was just deleted and
        # reinserted above under a brand new id (reset() couldn't do this
        # itself; see its own comment).
        self._add_row_feedback.reposition()

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
        # UserManagementWindow.refresh() hides any account NASsie didn't
        # create and that has no share access yet, so as not to clutter
        # sharing-focused UI with unrelated logins on the machine - but
        # this picker still needs to offer them (attaching a genuine
        # pre-existing account is a supported, deliberate case - see
        # existing_account_grant_message()), so label instead of hiding:
        # otherwise they showed up here identically to a NASsie-created
        # user with no way to tell them apart.
        self._attaching_labels = {
            u["username"]: u["username"] if u.get("managed") else f'{u["username"]} (existing account)'
            for u in candidates
        }
        self._share_action_bar.update()
        # After update() above, not before - the combobox tour.py's own
        # "Attach User" dropdown step points at (_build_inline_attach's
        # combo, which _build_share_action_bar's item == self._attaching_
        # item branch just built) has to actually exist for that step's
        # own place_near() to have something real to position against.
        self._notify_tour("attach_dropdown_opened")

    def _commit_inline_attach(self, username):
        share_name = self._attaching_share
        candidate_users = self._attaching_candidate_users
        self._attaching_item = None
        self._attaching_share = None
        self._attaching_candidates = []
        self._attaching_candidate_users = {}
        self._attaching_labels = {}
        # Reverts the bar from the inline combobox back to the row's
        # normal icon buttons right away - previously only done on the
        # cancel path below, leaving the (by now inert) combobox+Cancel
        # showing for the entire in-flight grant_access_worker() call on
        # the success path, instead of the real buttons underneath it.
        # Harmless most of the time (the next _refresh_all_lists() papers
        # over it once the worker finishes) but a real gap right after
        # this call returns - e.g. the tour's own "Delete Share" step
        # (see tour.py), reached via the "user_attached" event
        # _grant_access_done() fires once this same attach succeeds,
        # needs the row's real 3rd button to exist THEN, not just
        # eventually once refresh catches up.
        self._share_action_bar.update()
        if not username:
            return

        # A password already exists for this account if either: it's
        # attached to at least one OTHER share already (Samba, and worse
        # macOS, store one password per account, not one per share - see
        # core._add_user_to_share_linux's comment for the full reasoning)
        # OR it's a NASsie-managed account, since create_user()/add_user()
        # (the "New User" account-only flow, with no share attached yet)
        # sets the account's real Samba password at creation time, not
        # attach time - "not attached to any share yet" is not the same
        # as "has no password yet" for a managed account. Only a genuine
        # pre-existing, unmanaged computer account NASsie never touched
        # before has no known credentials at all, and only then is a new
        # password actually required here.
        existing_user = candidate_users.get(username)
        already_has_password = bool(existing_user) and (
            existing_user.get("managed", False) or existing_user.get("shares")
        )
        password = None
        if not already_has_password:
            self._notify_tour("attach_password_needed")
            password = PasswordPromptDialog(
                self.root, "Password", f"Set a password for '{username}' on this share:",
            ).result
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
            messagebox.showinfo("Detach", "Select a user under a share first.")
            return
        # Same reasoning as _delete_selected_share's identical guard -
        # the tour walks the user through clicking this on the exact
        # user it just guided them through attaching, so the real
        # revoke-access flow below is skipped entirely while that step
        # is showing, not just discouraged.
        if self._tour_waiting_on("user_detach_dialog_opened"):
            self._notify_tour("user_detach_dialog_opened")
            messagebox.showinfo(
                "Detach",
                f"This removes {username}'s access to '{share_name}'. Skipped here so your "
                "example share keeps its attached user.",
                parent=self.root,
            )
            self._notify_tour("user_detach_dialog_cancelled")
            return
        if not messagebox.askyesno(
            "Detach", f"Remove {username}'s access to '{share_name}'?"
        ):
            return
        threading.Thread(
            target=self._revoke_access_worker, args=(share_name, username), daemon=True
        ).start()

    def _show_qr_for_selection(self):
        # Same flow as UserManagementWindow._show_qr() - asks for the
        # CURRENT password and verifies it live (see
        # SMBWizard.verify_password()) rather than resetting it, so
        # showing a QR code again doesn't invalidate whatever other
        # devices already connected with the old one - but simpler here:
        # this row already IS a specific share+user, so there's no
        # multi-share ChoiceDialog step to worry about.
        share_name, username = self._selected_share_and_user()
        if not share_name or not username:
            messagebox.showinfo("Show QR Code", "Select a user under a share first.")
            return
        password = PasswordPromptDialog(
            self.root, "Show QR Code", f"Enter '{username}'s current password to show their QR code:",
            on_shown=lambda dlg: self._notify_tour("qr_dialog_opened", window=dlg),
        ).result
        if not password:
            # Harmless no-op outside the tour - see on_event()'s own
            # guard - but during the tour's own "QR Code" step this IS
            # the expected way to close it (see tour.py), no password
            # needed just to see what the button does.
            self._notify_tour("qr_prompt_cancelled")
            return
        if not self.wizard.verify_password(username, password, share_name):
            if messagebox.askyesno(
                "Incorrect password",
                "That doesn't match this account's current password.\n\n"
                "Forgot it? Reset the password instead to generate a new QR code.",
            ):
                self._reset_password_for_selection(share_name, username)
            return
        self._offer_qr_codes(share_name, [{"username": username, "password": password}], confirm=False)

    def _reset_password_for_selection(self, share_name, username):
        # Same guard and reasoning as UserManagementWindow.
        # _change_password_flow() - never reset a real Windows account's
        # actual sign-in password just because NASsie was handed access
        # to grant. Reuses _grant_access_worker (root context - this row
        # is already inside GUIWizard's own share tree, not
        # UserManagementWindow), same as every other "set/replace this
        # share's password" action.
        user = next((u for u in self.wizard.list_users() if u["username"] == username), None)
        if not (user and user.get("managed", False)) and self.wizard.system == "Windows":
            messagebox.showinfo(
                "Change Password",
                f"'{username}' is an existing Windows account, not one NASsie created - changing its "
                "password here would also change what they sign in with, so NASsie won't do that. "
                "Change their password from Windows' own account settings instead.",
            )
            return
        if not messagebox.askokcancel("Change Password", QR_PASSWORD_RESET_NOTE, parent=self.root):
            return
        password = PasswordPromptDialog(
            self.root, "New password", f"New password for '{username}' (replaces their current one):",
        ).result
        if not password:
            return
        share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
        share_user = next((u for u in (share or {}).get("users", []) if u["username"] == username), None)
        read_only = share_user.get("read_only", False) if share_user else False
        # confirm_qr=False - reached only from the "forgot it, reset
        # instead" fallback inside _show_qr_for_selection(), so clicking
        # Show QR Code already was the confirmation; see
        # _offer_qr_codes()'s own reasoning for the identical case there.
        threading.Thread(
            target=self._grant_access_worker,
            args=(share_name, username, password, read_only, False), daemon=True,
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
        # CURRENT level (open-book/memo icon, see
        # _build_share_action_bar)
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
        if changed:
            # The tour's own "Permission" step (see tour.py) wants the
            # user to see BOTH states - read-only and read-write - before
            # moving on, which takes two real toggles, not one. Only
            # counts while that step is actually the current one
            # (_tour_waiting_on), so this can't leave a stale count
            # behind for a later tour run, or fire early from ordinary
            # (non-tour) use.
            if self._tour_waiting_on("access_level_changed"):
                self._tour_permission_clicks += 1
                if self._tour_permission_clicks >= 2:
                    self._tour_permission_clicks = 0
                    self._notify_tour("access_level_changed")
            else:
                self._tour_permission_clicks = 0
        else:
            # No success popup (see _change_access_level_for_selection's
            # comment - the toggle button/badge already show the result),
            # but a FAILURE still needs to interrupt: the row silently
            # not changing could otherwise look identical to "there was
            # nothing to change."
            messagebox.showerror(
                "Failed", self._failure_text(f"Could not change {username}'s access level.", log_output)
            )
        self._refresh_all_lists()

    def _grant_access_worker(self, share_name, username, password, read_only=False, confirm_qr=True, parent_window=None):
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
            lambda: self._grant_access_done(
                share_name, username, password, added, buffer.getvalue(), confirm_qr, parent_window
            ),
        )

    def _grant_access_done(self, share_name, username, password, added, log_output, confirm_qr=True, parent_window=None):
        self._busy_stop()
        if log_output.strip():
            self._append_log(log_output)
        # Shared by the main share tree's Attach User flow (root context)
        # AND UserManagementWindow's Change Password flow (see
        # _change_password_flow, which passes parent_window=self) - the
        # caller says which window it actually happened in, so the
        # confirmation lands (and re-raises) against that one instead of
        # always root, which previously popped the confirmation up behind
        # UserManagementWindow whenever this ran from there (both windows
        # are permanently "-topmost" - see _bring_window_to_front - so
        # whichever one this targets is the one left on top).
        target = parent_window or self.root
        if added:
            self._notify_tour("user_attached")
            self._show_toast("Added", f"Added '{username}' to share '{share_name}'.", parent=target)
            # password is None for an existing, unmanaged Windows account
            # NASsie deliberately left untouched (see
            # _add_user_to_share_windows) - there's no password to encode,
            # and it must never be guessed at or invented for the QR code.
            if password is not None:
                self._offer_qr_codes(share_name, [{"username": username, "password": password}], confirm=confirm_qr)
        else:
            messagebox.showerror(
                "Failed",
                self._failure_text(f"Could not add '{username}' to share '{share_name}'.", log_output),
                parent=target,
            )
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
            self._show_toast("Detached", f"Removed {username}'s access to '{share_name}'.")
        else:
            messagebox.showerror("Failed", self._failure_text("Could not remove access.", log_output))
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
            self._show_toast("Deleted", f"Deleted user '{username}'.", parent=self.root)
        else:
            messagebox.showerror(
                "Failed", self._failure_text(f"Could not delete user '{username}'.", log_output), parent=self.root
            )
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
            self._show_toast("User Creation", f"'{username}' has been created.", parent=self.root)
            self._notify_tour("user_created")
        else:
            messagebox.showerror(
                "Failed", self._failure_text(f"Could not set up user '{username}'.", log_output), parent=self.root
            )
        self._refresh_all_lists()

    def _delete_selected_share(self):
        name, _ = self._selected_share_and_user()
        if not name:
            return
        # The tour walks the user through clicking this button on the
        # exact share it just guided them through creating - the real
        # confirm-and-delete flow below is skipped entirely (not just
        # discouraged) while that step is showing, so there's no path
        # from "clicking around during the tour" to actually losing it,
        # regardless of what gets clicked next.
        if self._tour_waiting_on("share_delete_dialog_opened"):
            self._notify_tour("share_delete_dialog_opened")
            messagebox.showinfo(
                "Delete Share",
                f"This removes '{name}' from Samba (and can optionally delete its folder too). "
                "Skipped here so you keep the share you just made.",
                parent=self.root,
            )
            self._notify_tour("share_delete_dialog_cancelled")
            return
        if not messagebox.askyesno(
            "Remove share", f"Remove share '{name}'? The share will be deleted, but your data is untouched."
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
            self._show_toast(
                "Removed", f"Removed share: {name}" + (" (folder deleted too)" if delete_folder else "")
            )
        else:
            messagebox.showerror("Failed", self._failure_text(f"Could not remove share '{name}'.", log_output))
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
