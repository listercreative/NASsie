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

    def place_below(self, other):
        # Like place_near(), but anchored on another TOPLEVEL window
        # (e.g. the real native tk_messageBox found by GuiTour.
        # attach_callout_to_next_dialog()) rather than a widget within
        # NASsie's own tree - no containing window to fall back to below/
        # above if there's no room next to it, since `other` effectively
        # IS that containing window here.
        self.update_idletasks()
        other.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        ox = other.winfo_rootx()
        oy = other.winfo_rooty()
        ow = other.winfo_width()
        oh = other.winfo_height()

        margin = 14
        below_y = oy + oh + margin
        y = below_y if below_y + h <= screen_h else max(oy - margin - h, 0)
        x = ox + (ow - w) // 2
        x = max(0, min(x, screen_w - w))
        y = max(0, min(y, screen_h - h))
        self.geometry(f"+{x}+{y}")
        self.lift()

    def place_center(self, container):
        # Fallback for a step with nothing of NASsie's own to point at
        # yet - e.g. the moment right before a native tk_messageBox
        # confirming a share/user was created actually exists (see
        # GuiTour.attach_callout_to_next_dialog(), which repositions to
        # place_below() the real dialog once it's found - tk_messageBox
        # centers over its OWN parent too, so simply centering here as
        # well, as this used to do unconditionally, landed the callout
        # ON TOP of the dialog instead of alongside it).
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        container.update_idletasks()
        cx = container.winfo_rootx()
        cy = container.winfo_rooty()
        cw = container.winfo_width()
        ch = container.winfo_height()

        x = cx + (cw - w) // 2
        y = cy + (ch - h) // 2
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
        # The real native tk_messageBox a widget=None step's callout has
        # been repositioned below, once attach_callout_to_next_dialog()
        # finds it - None until then, and reset on every step change.
        # reposition()/show_callout() in _track_container() check this so
        # a container <Configure>/<FocusIn> firing AFTER that doesn't
        # snap the callout back to place_center()'s guess.
        self._native_dialog_ref = None

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
            # place_center()). Container is gui.root, not _active_window -
            # the CreateShareDialog that opened it is already destroyed by
            # this point (same as every step after it).
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
             "Username and Password", "Type a username and password, then confirm.",
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
        # centers the callout over the container instead - see
        # _Callout.place_center().
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
            self._callout.place_center(container)
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
            # Shared by reposition() and show_callout() - and consulting
            # _native_dialog_ref, not just widget, is what keeps a step
            # already reattached to a real native dialog (see
            # attach_callout_to_next_dialog()) from snapping back to
            # place_center()'s guess the next time either fires.
            if widget is not None:
                self._highlight.place_around(widget)
                self._callout.place_near(widget)
            elif self._native_dialog_ref is not None and self._native_dialog_ref.winfo_exists():
                self._callout.place_below(self._native_dialog_ref)
            else:
                self._callout.place_center(container)

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
            if self._callout is not None:
                try:
                    self._callout.withdraw()
                except tk.TclError:
                    pass

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

    def attach_callout_to_next_dialog(self, parent):
        # Call right after showing a widget=None step (a native
        # tk_messageBox is about to appear, centered over `parent` the
        # same way place_center() guessed) and right BEFORE invoking the
        # blocking messagebox.showX() call that creates it - repositions
        # the already-showing callout below the REAL dialog once it
        # exists, rather than trying to precompute where "below" will
        # land from its (unknown in advance, message- and theme-
        # dependent) size. tk_messageBox has no Python widget handle of
        # its own, so this finds it the only way available: diffing
        # `parent`'s children before/after. Safe to call even if the
        # tour isn't running or this step isn't a widget=None one -
        # simply finds nothing and does nothing.
        if self._callout is None:
            return
        before = set(parent.winfo_children())

        def find_and_attach(attempts_left=15):
            if self._callout is None:
                return
            new = [w for w in parent.winfo_children() if w not in before and isinstance(w, tk.Toplevel)]
            if new:
                self._native_dialog_ref = new[0]
                try:
                    self._callout.place_below(new[0])
                except tk.TclError:
                    pass
            elif attempts_left > 0:
                parent.after(20, lambda: find_and_attach(attempts_left - 1))

        parent.after(20, find_and_attach)

    def stop(self):
        self._teardown_current()

    def _teardown_current(self):
        self._untrack_container()
        self._native_dialog_ref = None
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
