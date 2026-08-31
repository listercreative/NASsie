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
             "New Share", "Click here to get started.",
             "share_dialog_opened"),
            (lambda: self._active_window, lambda: self._active_window.name_entry,
             "Share Name",
             "Give your share a name and continue - you'll get a chance to pick the folder "
             "next, or just accept the suggested one.",
             "share_created"),

            (lambda: gui.root, lambda: gui._manage_users_btn,
             "Manage Users", "Open this to manage user accounts.",
             "user_mgmt_opened"),
            (lambda: self._active_window, lambda: self._active_window._new_user_toolbar_btn,
             "New User", "Click here to create a standalone account.",
             "user_dialog_opened"),
            (lambda: self._active_window, lambda: self._active_window.username_entry,
             "Username and Password", "Type a username and password, then confirm.",
             "user_created"),

            (lambda: gui.root, lambda: gui.shares_list,
             "Shares", "Select your share to reveal its actions.",
             "share_selected"),
            (lambda: gui.root, lambda: gui._share_action_bar.bar,
             "Attach User", "Click the attach icon that appears here.",
             "attach_dialog_opened"),
            (lambda: self._active_window, lambda: self._active_window.username_entry,
             "Username", "Choose the account to attach, then confirm.",
             "user_attached"),
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
        widget = widget_fn()

        self._highlight = _HighlightBox(container)
        self._highlight.place_around(widget)

        self._callout = _Callout(container, title, text, self.index + 1, len(self.steps), on_skip=self.stop)
        self._callout.place_near(widget)
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
        # container to raise/lower the callout with it, so it only sits
        # above other apps' windows while NASsie is the focused one,
        # rather than the global "-topmost" this used to rely on.
        def reposition(event=None):
            if self._callout is None:
                return
            try:
                self._highlight.place_around(widget)
                self._callout.place_near(widget)
            except tk.TclError:
                pass

        def raise_callout(event=None):
            if self._callout is not None:
                try:
                    self._callout.lift()
                except tk.TclError:
                    pass

        def lower_callout(event=None):
            if self._callout is not None:
                try:
                    self._callout.lower()
                except tk.TclError:
                    pass

        self._tracking = [
            (container, "<Configure>", container.bind("<Configure>", reposition, add="+")),
            (container, "<FocusIn>", container.bind("<FocusIn>", raise_callout, add="+")),
            (container, "<FocusOut>", container.bind("<FocusOut>", lower_callout, add="+")),
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
        widget = self.gui.shares_list
        self.root.update_idletasks()
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
