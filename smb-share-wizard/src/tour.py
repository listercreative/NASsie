import os
import platform
import tkinter as tk
from tkinter import ttk

# Matches the app icon's teal (the serpent body/water) - a light tint of the
# same hue for the callout background, rather than an unrelated color, so
# the tour reads as part of NASsie instead of a bolted-on overlay.
_HIGHLIGHT_COLOR = "#0e92ab"
_CALLOUT_BG = "#eaf6f8"
_BORDER_THICKNESS = 3


def _bring_to_front(win):
    # Same "-topmost toggle" trick gui.py's own _bring_window_to_front()
    # uses (duplicated rather than imported - gui.py imports FROM this
    # module, not the other way around): some window managers (GNOME/
    # Mutter under Wayland, confirmed in this codebase) don't reliably
    # honor a plain lift()/focus_force() for a window that wasn't just
    # freshly mapped by the WM itself. Without this, each step's own
    # container could end up left behind whatever the PREVIOUS step (a
    # dialog, a native messagebox) had brought to front instead - the
    # user finding the main NASsie window buried behind other apps after
    # a step closed, not just its callout.
    try:
        win.attributes("-topmost", True)
        win.attributes("-topmost", False)
        win.lift()
        win.focus_force()
    except tk.TclError:
        pass


def _real_home():
    # Root via sudo - notably the postinst-launched wizard, which always
    # runs as root regardless of who ran `apt install` - has HOME=/root.
    # For most users that IS their first-ever look at NASsie, so the
    # marker has to land in the real invoking user's home instead: written
    # against /root, it'd not just miss recording that user's actual first
    # look, it'd permanently hide the tour from every later real launch
    # too, since has_seen_tour() would then only ever check root's own
    # copy. Same signal core.py's SMBWizard._real_home() uses.
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            import pwd
            return pwd.getpwnam(sudo_user).pw_dir
        except (KeyError, ImportError):
            pass
    return os.path.expanduser("~")


def _first_run_marker_path():
    if platform.system() == "Windows" and os.environ.get("APPDATA"):
        base = os.path.join(os.environ["APPDATA"], "NASsie")
    else:
        base = os.path.join(_real_home(), ".config", "nassie")
    return os.path.join(base, "tour_seen")


def has_seen_tour():
    return os.path.exists(_first_run_marker_path())


def mark_tour_seen():
    path = _first_run_marker_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    with open(path, "w"):
        pass
    _chown_to_real_user(directory, path)


def _chown_to_real_user(*paths):
    # Only meaningful for the same root-via-sudo case _real_home() handles
    # - without this, a directory/file created (as root) inside another
    # user's home would end up root-owned, leaving that user unable to
    # write anything else of their own into ~/.config/nassie later.
    if os.name != "posix" or os.geteuid() != 0:
        return
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user or sudo_user == "root":
        return
    try:
        import pwd
        pw = pwd.getpwnam(sudo_user)
    except KeyError:
        return
    for p in paths:
        try:
            os.chown(p, pw.pw_uid, pw.pw_gid)
        except OSError:
            pass


class _TreeRegion:
    """Adapts one or more rows of a ttk.Treeview to the winfo_* interface
    _HighlightBox/_Callout need, so a tour step can point at the specific
    row(s) that actually matter instead of the whole (often much taller,
    mostly-empty) tree widget - pointing at gui.shares_list itself for
    the "Shares" step put "below/above the widget" ~400px from the actual
    share+user rows it was meant to be next to, occasionally landing back
    on top of them instead of clear of them."""

    def __init__(self, tree, item_ids):
        self.tree = tree
        self.item_ids = [i for i in item_ids if i]

    def update_idletasks(self):
        self.tree.update_idletasks()

    def winfo_toplevel(self):
        return self.tree.winfo_toplevel()

    def _bbox(self):
        boxes = [self.tree.bbox(i) for i in self.item_ids]
        boxes = [b for b in boxes if b]
        if not boxes:
            # Nothing to bound - a row scrolled out of view, or (the tour
            # was interrupted/reordered) none of the ids exist anymore -
            # fall back to the tree's own full bounds rather than erroring.
            return (0, 0, self.tree.winfo_width(), self.tree.winfo_height())
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        return (x0, y0, x1 - x0, y1 - y0)

    def winfo_rootx(self):
        return self.tree.winfo_rootx() + self._bbox()[0]

    def winfo_rooty(self):
        return self.tree.winfo_rooty() + self._bbox()[1]

    def winfo_width(self):
        return self._bbox()[2]

    def winfo_height(self):
        return self._bbox()[3]


class _HighlightBox:
    """Four thin frames forming a rectangle outline around a widget -
    placed directly on the GUI's own root window (via place()) rather
    than as separate overrideredirect Toplevels. Toplevels bypass the
    window manager entirely, which on Linux WMs with virtual desktops
    (GNOME/Mutter included) means they aren't hidden/shown per-workspace
    the way real windows are - they just render at a fixed screen
    position on whatever workspace happens to be active, drifting onto
    an unrelated window if the GUI itself ended up on a different one.
    Being real child widgets of root ties this to the window by
    construction: it moves, raises, minimizes, and switches workspaces
    exactly as the window does, with nothing to get out of sync."""

    def __init__(self, root):
        self.root = root
        self.bars = [tk.Frame(root, bg=_HIGHLIGHT_COLOR, bd=0, highlightthickness=0) for _ in range(4)]

    def place_around(self, widget):
        widget.update_idletasks()
        pad = 4
        # Relative to root's own top-left corner, not the screen -
        # place() positions children within their container, while
        # winfo_rootx/rooty return absolute screen coordinates.
        x = widget.winfo_rootx() - self.root.winfo_rootx() - pad
        y = widget.winfo_rooty() - self.root.winfo_rooty() - pad
        w = widget.winfo_width() + pad * 2
        h = widget.winfo_height() + pad * 2
        t = _BORDER_THICKNESS
        top, bottom, left, right = self.bars
        top.place(x=x, y=y, width=w, height=t)
        bottom.place(x=x, y=y + h - t, width=w, height=t)
        left.place(x=x, y=y, width=t, height=h)
        right.place(x=x + w - t, y=y, width=t, height=h)
        for bar in self.bars:
            bar.lift()

    def destroy(self):
        for bar in self.bars:
            try:
                bar.destroy()
            except tk.TclError:
                # Its container (a dialog the tour was pointing into) may
                # already be gone - Tk destroys children along with their
                # parent, so this is just cleaning up a reference to
                # something that's already been torn down.
                pass


class _Callout(tk.Toplevel):
    # UNLIKE _HighlightBox, this is a real floating overrideredirect
    # window, positioned in absolute screen coordinates - not a place()
    # child of whatever window it's pointing into. That was the original
    # design (see _HighlightBox's docstring for why: a child widget
    # follows its window across workspace switches for free, a Toplevel
    # doesn't), but a place() child is clipped to its parent's own
    # bounds, so once a tour step points into a window SHORTER than the
    # callout itself (a small compact dialog - see "Create Share"'s name
    # page, or "New User"), there is no position inside that parent where
    # the callout fits without covering the very field it's explaining.
    # Screen coordinates have no such ceiling. Unlike _HighlightBox, this
    # doesn't get the "moves with its window for free" property, so
    # GuiTour re-runs place_near() on the container's <Configure> event to
    # track it explicitly instead - see GuiTour._track_container(). No
    # Next/Back buttons - see GuiTour's docstring for why each step
    # advances itself once its real action actually happens, rather than
    # being paced by clicks through a narrated slideshow. Skip is the one
    # manual escape hatch throughout (relabeled "Close" for the final,
    # non-gated step).
    def __init__(self, parent_window, title, text, step_num, step_total, on_skip, skip_label="Skip Tour"):
        super().__init__(parent_window)
        self.transient(parent_window)
        self.overrideredirect(True)
        # No "-topmost": that pins it above EVERY window on the desktop,
        # not just NASsie's own - switch to an unrelated app and the
        # callout stayed floating on top of it too, since an
        # overrideredirect window isn't WM-managed and nothing else was
        # ever telling it to get out of the way. GuiTour's focus-in/out
        # tracking on the container (also set up in _track_container())
        # raises/lowers this instead, so it only stays above other
        # windows while NASsie itself is the focused app - the same as
        # any of NASsie's own dialogs behave.
        self.configure(bg=_HIGHLIGHT_COLOR, padx=2, pady=2)

        inner = tk.Frame(self, bg=_CALLOUT_BG)
        inner.pack(fill="both", expand=True)

        ttk.Label(
            inner, text=title, font=("TkDefaultFont", 11, "bold"), background=_CALLOUT_BG
        ).pack(anchor="w", padx=10, pady=(10, 2))
        ttk.Label(
            inner, text=text, background=_CALLOUT_BG, wraplength=260, justify="left"
        ).pack(anchor="w", padx=10, pady=(0, 8))

        btn_row = tk.Frame(inner, bg=_CALLOUT_BG)
        btn_row.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Label(btn_row, text=f"Step {step_num} of {step_total}", background=_CALLOUT_BG).pack(side="left")
        ttk.Button(btn_row, text=skip_label, command=on_skip).pack(side="right")

    def place_near(self, widget):
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        # Anchored on the widget itself by default - a bubble that always
        # jumped to the edge of its whole containing window (a prior
        # version of this method) reads as disconnected from what it's
        # actually explaining once that window is bigger than the bubble
        # (e.g. the ~500px gap between the "New Share" button, near the
        # main window's TOP, and a callout pinned to that window's
        # bottom edge). Below/above the WIDGET only falls back to the
        # window's edge for the case that whole-window anchoring was
        # originally solving: a compact dialog (the "Username and
        # Password" step, for one) where several fields sit stacked close
        # together and there's no room around the specific widget within
        # its own window without covering one of the others.
        wx = widget.winfo_rootx()
        wy = widget.winfo_rooty()
        ww = widget.winfo_width()
        wh = widget.winfo_height()

        container = widget.winfo_toplevel()
        # GuiTour re-invokes this outside the step's own initial setup -
        # on the container's <Configure> event, to track it being dragged
        # (see GuiTour._track_container()) - so unlike widget above (which
        # is always freshly laid out by the _show_step() call that owns
        # this invocation), container's cached winfo_* values here can't
        # be assumed fresh: without this, a container geometry read still
        # mid-settle (observed right after a dialog closes and focus/
        # geometry events are still landing) could hand back stale rootx/
        # rooty/height - a 0 in particular reliably drove every branch
        # below into the same degenerate (0, 0) corner.
        container.update_idletasks()
        cx = container.winfo_rootx()
        cy = container.winfo_rooty()
        # winfo_height(), not winfo_reqheight(): the main window is
        # explicitly floored to a minimum size larger than its packed
        # content's natural request (see GUIWizard's sizing block in
        # gui.py), so reqheight() under-reports it - that put "below the
        # window" comfortably inside its actual bottom edge instead.
        # winfo_height() is the real, currently-mapped size regardless of
        # why it ended up that size.
        cbottom = cy + container.winfo_height()

        margin = 14
        below_widget_y = wy + wh + margin
        above_widget_y = wy - margin - h
        if below_widget_y + h <= min(cbottom, screen_h):
            x, y = wx, below_widget_y
        elif above_widget_y >= max(cy, 0):
            x, y = wx, above_widget_y
        else:
            # No room around the widget itself within its own window -
            # the compact-dialog case - so clear the whole window instead.
            below_container_y = cbottom + margin
            y = below_container_y if below_container_y + h <= screen_h else max(cy - margin - h, 0)
            x = cx
        x = max(0, min(x, screen_w - w))
        y = max(0, min(y, screen_h - h))
        self.geometry(f"+{x}+{y}")
        self.lift()

    def place_outside_container(self, container):
        # For a step with nothing of NASsie's own to point at - e.g. the
        # native tk_messageBox confirming a share/user was created, which
        # is a plain Tcl dialog with no Python widget handle to attach a
        # highlight to.
        #
        # An earlier version of this tried to actually find that real
        # dialog window (diffing container's children before/after
        # opening it) and center below IT specifically - abandoned, not
        # simplified away for its own sake: winfo_children() silently
        # drops any child it has no Python-side wrapper for (see its own
        # try/except KeyError), and a native tk_messageBox is exactly
        # that - a real Tcl-level Toplevel the `tk_messageBox` command
        # creates directly, never constructed through any Python
        # Toplevel(...) call. Confirmed live: even the RAW Tcl `winfo
        # children` call (bypassing Python's wrapper entirely) reported
        # no children while a real messagebox was open, so there was no
        # reliable way to locate it at all - the callout was silently
        # stuck at its very first guess forever, which is what let it
        # end up overlapping the real dialog in practice.
        #
        # This sidesteps needing to find it at all: tk_messageBox always
        # centers over `container` (same as this callout would if simply
        # centered) - so placing OUTSIDE container's own bounds instead
        # (below its bottom edge, or above if that doesn't fit) can never
        # overlap anything centered inside them, without needing to know
        # the dialog's actual size or position.
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        # update(), not just update_idletasks() - idle tasks are only
        # Tk's OWN queued work; the container's winfo_rootx/rooty in
        # particular reflect wherever the X SERVER last told Tk it
        # actually is; catching up on that needs a real round trip
        # (update()), and right after another window it was just
        # brought back in front of closed/withdrew (the exact moment
        # this runs) is precisely when that hasn't landed yet -
        # confirmed live, intermittently, as a callout jammed at (0, 0)
        # with no later event to ever correct it.
        container.update()
        cw, ch = container.winfo_width(), container.winfo_height()
        if cw <= 1 or ch <= 1:
            self.after(20, lambda: self.place_outside_container(container))
            return
        cx = container.winfo_rootx()
        cy = container.winfo_rooty()
        cbottom = cy + ch

        margin = 14
        below_y = cbottom + margin
        y = below_y if below_y + h <= screen_h else max(cy - margin - h, 0)
        x = cx + (cw - w) // 2
        x = max(0, min(x, screen_w - w))
        y = max(0, min(y, screen_h - h))
        self.geometry(f"+{x}+{y}")
        self.lift()


class GuiTour:
    """A short, skippable tour that advances itself as the user actually
    completes each step's real action (open a dialog, create a share,
    create a user, attach it) - no "Next" button. GUIWizard calls
    on_event() the moment the matching thing genuinely happens (see
    GUIWizard._notify_tour(), and the app=/window= notifications from
    CreateShareDialog, AddUserDialog, and UserManagementWindow), so the
    first thing anyone does with NASsie is the exact clicks they'd use
    every time after, not a click through a separate narrated slideshow
    standing in for it. Crucially, this follows the user INTO whatever
    dialog just opened rather than sitting still on the button that
    opened it: each step's highlight/callout are children of whichever
    window that step points into (see _HighlightBox/_Callout, which don't
    actually care which window they're attached to), so the walkthrough
    moves from the main window, into Create Share, back to the main
    window, into Manage Users, into its own New User dialog, and so on.
    Deliberately covers both New User (create + attach in one step) and
    Attach User (an existing account) as two distinct actions, since that
    split is the whole point of separating them - worth learning both
    once, up front."""

    def __init__(self, gui):
        self.gui = gui
        self.root = gui.root
        self.steps = self._build_steps()
        self.index = 0
        self._highlight = None
        self._callout = None
        self._wait_event = None
        # (bound_widget, event_name, funcid) for the current step's
        # container tracking - see _track_container()/_untrack_container().
        self._tracking = []
        # Whichever dialog/window the CURRENT (or most recently opened)
        # step's action happened in - starts out None (nothing but the
        # main window exists yet); updated by on_event()'s window= arg
        # whenever a step's wait_event hands one back.
        self._active_window = None

    def _newest_share_region(self):
        # The most recently created share (and its just-attached user, if
        # any) - the last top-level row in the tree, plus its children.
        # See _TreeRegion's docstring for why this points at the actual
        # row(s) instead of gui.shares_list itself.
        tree = self.gui.shares_list
        children = tree.get_children("")
        if not children:
            return tree
        share_item = children[-1]
        return _TreeRegion(tree, [share_item] + list(tree.get_children(share_item)))

    def _build_steps(self):
        # (container_fn, widget_fn, title, text, wait_event) - container_fn
        # is which window to attach the highlight/callout to for this step
        # (gui.root, or self._active_window once a dialog has opened);
        # widget_fn is the specific widget within it to point at. Both are
        # called lazily, at the moment each step is shown, since a step
        # pointing into a dialog can't resolve gui root's widget until
        # that dialog actually exists. wait_event is the string
        # GUIWizard._notify_tour() (or a dialog's own app._notify_tour()
        # call) passes once this step's real action has actually happened;
        # see on_event().
        gui = self.gui
        # Each title names the actual item being pointed at (its own
        # button/field label or tooltip), not the action being asked for -
        # e.g. "Username and Password", not "Name the user".
        return [
            (lambda: gui.root, lambda: gui._new_share_btn,
             "New Share", "Click here to create your first share user.",
             "share_dialog_opened"),
            (lambda: self._active_window, lambda: self._active_window.name_entry,
             "Share Name", "Give your share a name and continue.",
             "share_name_confirmed"),
            (lambda: self._active_window, lambda: self._active_window.path_entry,
             "Folder",
             "Pick a folder for this share, or leave the suggested default and click "
             "Create Share to continue.",
             "share_created"),
            # No widget (None) - the "Done" dialog that follows is a plain
            # tk_messageBox, not one of NASsie's own, so there's no Python
            # widget handle to attach a highlight to (see _Callout.
            # place_outside_container()). Container is gui.root, not
            # _active_window - the CreateShareDialog that opened it is
            # already destroyed by this point (same as every step after it).
            (lambda: gui.root, None,
             "Confirmation", "Click OK to confirm.",
             "share_apply_confirmed"),

            (lambda: gui.root, lambda: gui._manage_users_btn,
             "Manage Users", "Open this to manage user accounts.",
             "user_mgmt_opened"),
            (lambda: self._active_window, lambda: self._active_window._new_user_toolbar_btn,
             "New User", "Click here to create a new share user.",
             "user_dialog_opened"),
            (lambda: self._active_window, lambda: self._active_window.username_entry,
             "Username and Password", "Type a username and password, then click OK.",
             "user_created"),
            (lambda: gui.root, None,
             "Confirmation", "Click OK to confirm.",
             "user_apply_confirmed"),
            # gui._user_mgmt_window directly, not self._active_window -
            # active_window is still pointing at the (already-destroyed)
            # AddUserDialog from the "Username and Password" step; nothing
            # since has updated it, since neither "user_created" nor
            # "user_apply_confirmed" hands a window= back.
            (lambda: gui._user_mgmt_window, lambda: gui._user_mgmt_window._close_toolbar_btn,
             "Close", "Close this window to return to the main NASsie window.",
             "user_mgmt_closed"),

            (lambda: gui.root, lambda: self._newest_share_region(),
             "Shares", "Select your share to reveal its actions.",
             "share_selected"),
            (lambda: gui.root, lambda: gui._share_action_bar.bar,
             "Attach User",
             "These icons let you add another user, attach an existing one, remove "
             "access, change access level, or delete the share. Click the attach icon "
             "(\U0001F517), then pick a user from the dropdown that appears here to continue.",
             "user_attached"),
            (lambda: gui.root, None,
             "Confirmation", "Click OK to confirm.",
             "attach_apply_confirmed"),
        ]

    def start(self):
        self.index = 0
        self._show_step()

    def _show_step(self):
        self._teardown_current()
        container_fn, widget_fn, title, text, wait_event = self.steps[self.index]
        container = container_fn()
        if container is None:
            # The window this step depends on isn't around (the tour was
            # interrupted, or something closed out of order) - bail out
            # quietly rather than point at nothing.
            self.stop()
            return
        container.update_idletasks()
        _bring_to_front(container)
        # None (a step with nothing of NASsie's own to point at - the
        # native "Done" confirmation dialog after a share/user is created,
        # which is a plain Tcl tk_messageBox with no Python widget handle
        # to attach a highlight to) skips the highlight box entirely and
        # places the callout outside the container's own bounds instead -
        # see _Callout.place_outside_container().
        widget = widget_fn() if widget_fn is not None else None

        if widget is not None:
            self._highlight = _HighlightBox(container)
            self._highlight.place_around(widget)
        else:
            self._highlight = None

        self._callout = _Callout(container, title, text, self.index + 1, len(self.steps), on_skip=self.stop)
        if widget is not None:
            self._callout.place_near(widget)
        else:
            self._callout.place_outside_container(container)
            # One extra self-correction shortly after, specifically for
            # this widget=None case - confirmed live (intermittently,
            # timing-dependent - a race, not a one-off) landing at a
            # stale (0, 0) with no later event to ever fix it, right
            # after a step transition that both destroys a dialog AND
            # withdraws that dialog's own OWNER window in the same beat
            # (e.g. AddUserDialog closing while UserManagementWindow
            # withdraws right under it) - evidently sometimes enough WM
            # churn that even container.update()'s round trip inside
            # place_outside_container() itself isn't a hard guarantee.
            # This costs nothing when the first placement was already
            # right - it just recomputes the same answer.
            index_at_schedule = self.index
            def _self_correct(step_index=index_at_schedule):
                if self._callout is not None and self.index == step_index:
                    try:
                        self._callout.place_outside_container(container)
                    except tk.TclError:
                        pass
            container.after(120, _self_correct)
        self._wait_event = wait_event
        self._track_container(container, widget)

    def _track_container(self, container, widget):
        # _HighlightBox is a real child widget of container, so it already
        # moves for free when the window is dragged - place() coordinates
        # are relative to the parent's own client area, not the screen.
        # _Callout is a separate Toplevel in absolute screen coordinates
        # (see its docstring for why), so nothing repositions it on its
        # own - re-run place_near() whenever the container actually moves
        # or resizes. Same idea for stacking: bind focus in/out on the
        # container to hide/show the callout with it, so it only appears
        # while NASsie is the focused app.
        #
        # This actually UNMAPS the window (withdraw/deiconify), not just a
        # stacking-order request (lower()/lift(), tried first) - under
        # Wayland (XWayland here; see XDG_SESSION_TYPE), the compositor
        # treats an overrideredirect window as an "unmanaged" popup surface
        # and doesn't reliably honor raw X restack requests for it the way
        # a native X11 WM would, so lower() was getting called (confirmed)
        # without the callout actually disappearing behind whatever the
        # user clicked over to. An unmapped window isn't a stacking
        # heuristic - it's simply not drawn - so this holds regardless of
        # compositor.
        def place_callout():
            # Shared by reposition() and show_callout().
            if widget is not None:
                self._highlight.place_around(widget)
                self._callout.place_near(widget)
            else:
                self._callout.place_outside_container(container)

        def reposition(event=None):
            if self._callout is None:
                return
            try:
                place_callout()
            except tk.TclError:
                pass

        def show_callout(event=None):
            if self._callout is not None:
                try:
                    self._callout.deiconify()
                    place_callout()
                    self._callout.lift()
                except tk.TclError:
                    pass

        def hide_callout(event=None):
            if self._callout is None:
                return
            # Deferred, and re-checked once focus has actually settled -
            # a container's own <FocusOut> fires just as readily when
            # focus moves to one of NASSIE'S OWN child windows (a native
            # messagebox.showinfo() this step's own container just
            # opened, in particular - confirmed live: the "Share Creation
            # Succeeded" dialog's own appearance was hiding this exact
            # step's callout the instant it showed up) as when it moves
            # to an unrelated external app, and there's nothing in the
            # event itself to tell those apart. Tk's focus_get() DOES:
            # it returns the focused widget only while focus is
            # somewhere within THIS application, and None once it's
            # moved to a different one entirely - checking that instead
            # of hiding unconditionally is what keeps this step's own
            # dialogs from hiding its own explanation of them.
            def _maybe_hide():
                if self._callout is None:
                    return
                try:
                    # focus_get() resolves Tk's raw focus path back to a
                    # Python widget object - and can itself raise
                    # KeyError, not just TclError, when that path is an
                    # internal ttk implementation widget with no such
                    # object (confirmed live: a ttk.Combobox's own
                    # dropdown listbox, right as a selection commits from
                    # it). Treated the same as "still focused somewhere
                    # in this app" (don't hide) rather than as a reason
                    # to hide - failing toward "stays visible" is the
                    # safe direction here, the same as the bug this whole
                    # check exists to fix.
                    focused = container.focus_get()
                except (tk.TclError, KeyError):
                    return
                if focused is not None:
                    return
                try:
                    self._callout.withdraw()
                except tk.TclError:
                    pass
            container.after(50, _maybe_hide)

        self._tracking = [
            (container, "<Configure>", container.bind("<Configure>", reposition, add="+")),
            (container, "<FocusIn>", container.bind("<FocusIn>", show_callout, add="+")),
            (container, "<FocusOut>", container.bind("<FocusOut>", hide_callout, add="+")),
        ]

    def _untrack_container(self):
        for widget, event, funcid in self._tracking:
            try:
                widget.unbind(event, funcid)
            except tk.TclError:
                # Same reasoning as _HighlightBox.destroy() - the window
                # it was bound to may already be gone.
                pass
        self._tracking = []

    def on_event(self, event, window=None):
        # Called by GUIWizard right when the action a visible step is
        # waiting on actually happens - ignored otherwise (including while
        # no tour is running, or the event doesn't match what the CURRENT
        # step cares about). window, when given, is the dialog that just
        # opened because of that action - becomes self._active_window, so
        # the NEXT step (which typically points into it) resolves correctly.
        if self._callout is None or event != self._wait_event:
            return
        if window is not None:
            self._active_window = window
        self.index += 1
        if self.index >= len(self.steps):
            self._show_finish()
        else:
            self._show_step()

    def _show_finish(self):
        # The one step with no further action to gate on - a manual close
        # is the right call here, since the walkthrough is genuinely done.
        # Always back on the main window by this point (attaching a user
        # closes its dialog), so gui.root/shares_list, not _active_window.
        self._teardown_current()
        widget = self._newest_share_region()
        self.root.update_idletasks()
        _bring_to_front(self.root)
        self._highlight = _HighlightBox(self.root)
        self._highlight.place_around(widget)
        self._callout = _Callout(
            self.root, "You're all set!",
            "Your share now has a user attached to it and is ready to connect to.",
            len(self.steps), len(self.steps), on_skip=self.stop, skip_label="Close",
        )
        self._callout.place_near(widget)
        self._wait_event = None
        self._track_container(self.root, widget)

    def stop(self):
        self._teardown_current()

    def _teardown_current(self):
        self._untrack_container()
        highlight_root = self._highlight.root if self._highlight else None
        if self._highlight:
            self._highlight.destroy()
            self._highlight = None
        if self._callout:
            try:
                self._callout.destroy()
            except tk.TclError:
                # Same reasoning as _HighlightBox.destroy() - its window
                # may have already closed out from under the tour.
                pass
            self._callout = None
        if highlight_root is not None:
            try:
                # Destroying a place()'d overlay Frame doesn't always get
                # its old screen region repainted immediately under every
                # compositor - forcing one here (idle tasks first, so the
                # geometry manager has actually noticed the removal) is
                # cheap insurance against a stale visual frame of the
                # highlight box outliving the widget it belonged to.
                highlight_root.update_idletasks()
                highlight_root.update()
            except tk.TclError:
                pass
