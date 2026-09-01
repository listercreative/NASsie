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

# Sentinel a step's widget_fn returns to mean "highlight the whole
# container window" (see the "Close" step - it points at
# UserManagementWindow's own real close control, its WM titlebar, which
# has nothing of NASsie's own to point at) rather than None (no highlight
# at all - e.g. the "Confirmation" steps) or a specific widget.
_WHOLE_WINDOW = object()


def _bring_to_front(win, steal_focus=True):
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
    #
    # Ends up "-topmost" TRUE, permanently - not toggled back off (an
    # earlier version reset it to False here, matching gui.py's
    # _bring_window_to_front() at the time). Explicitly reverted: every
    # NASsie window needs to stay above other applications on the
    # desktop for as long as it's open, not just flash to the front once
    # - see gui.py's _bring_window_to_front() for the full reasoning
    # (same fix, same rationale, duplicated here rather than imported
    # since gui.py imports FROM this module, not the other way around).
    #
    # The False->update()->True sequence, not just setting True once, is
    # still needed even though the end state is the same either way: Tk
    # only re-evaluates stacking on an actual VALUE CHANGE of the
    # attribute, so setting True on a window that's ALREADY True (every
    # NASsie window, permanently, after its first raise) would silently
    # no-op and never actually re-stack it above whichever OTHER
    # already-topmost NASsie window is currently in front - dropping to
    # False first (and update()'s synchronous round trip actually
    # landing that, not a deferred win.after() - see gui.py's comment
    # for the cross-window race that caused) forces the following True
    # to register as a real transition again.
    #
    # steal_focus=False skips focus_force() - used by the periodic
    # self-reassertion in GuiTour._schedule_reassert(), where forcing
    # KEYBOARD focus onto the container every 1.5s would yank it away
    # from whatever field the user is actively typing into (the share
    # name, a password, ...) far more disruptively than the visibility
    # problem this is working around. Raising the window without
    # grabbing focus is still enough to keep it visually in front.
    try:
        win.deiconify()
        win.lift()
        win.attributes("-topmost", False)
        win.update()
        win.attributes("-topmost", True)
        if steal_focus:
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
    # too, since tour_state() would then only ever check root's own
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


def tour_state():
    # "new": never started - auto-start it, the normal first-run case.
    # "interrupted": started but neither finished nor explicitly skipped
    # (the file exists but its content isn't "completed") - the app
    # closed mid-tour (crash, force-quit, or just clicking NASsie's own
    # window close button) before mark_tour_completed() ever ran. Worth
    # distinguishing from "new" so GUIWizard can OFFER to pick it back up
    # instead of either silently never showing it again (this used to
    # write "seen" the instant the tour started, before the user had done
    # anything - a crash one step in meant never seeing it again) or
    # unconditionally restarting it every single launch until it happens
    # to be finished (which would get old fast for anyone who skips it
    # more than once on purpose).
    # "completed": finished, or explicitly skipped - never show again.
    path = _first_run_marker_path()
    if not os.path.exists(path):
        return "new"
    try:
        with open(path) as f:
            content = f.read().strip()
    except OSError:
        return "new"
    return "completed" if content == "completed" else "interrupted"


def mark_tour_started():
    _write_tour_state("started")


def mark_tour_completed():
    _write_tour_state("completed")


def _write_tour_state(state):
    path = _first_run_marker_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    with open(path, "w") as f:
        f.write(state)
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

    def place_around_container(self):
        # Highlights the WHOLE window this box's bars are children of
        # (self.root itself), not a widget within it - for a step asking
        # to close a window that has nothing of NASsie's own left to
        # highlight otherwise (the "Close" tour step: UserManagementWindow
        # already has a real, working close control - its own WM titlebar
        # "X" - so there's no reason to add a redundant in-content button
        # just to have something to point at instead).
        #
        # Inset from the window's own edges, not padded OUTWARD around
        # them the way place_around() pads around a normal (smaller,
        # centered-within-its-window) widget - these bars are ordinary
        # place()'d CHILD widgets of self.root, so anything positioned
        # outside self.root's own bounds is simply clipped away by X, not
        # rendered at all.
        self.root.update_idletasks()
        inset = 3
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        t = _BORDER_THICKNESS
        top, bottom, left, right = self.bars
        top.place(x=inset, y=inset, width=w - inset * 2, height=t)
        bottom.place(x=inset, y=h - inset - t, width=w - inset * 2, height=t)
        left.place(x=inset, y=inset, width=t, height=h - inset * 2)
        right.place(x=w - inset - t, y=inset, width=t, height=h - inset * 2)
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
        # step_num=None omits the "Step X of Y" label entirely - used for
        # the finish step, where a step count reads as odd right after
        # "you're all set" (there's nothing left to count toward).
        super().__init__(parent_window)
        # Withdrawn immediately, before anything below is even built -
        # Tk maps a Toplevel as soon as it exists, at whatever size its
        # widgets currently request (unwrapped text, no wraplength
        # applied yet, before any of the content below is packed) - left
        # visible, that showed up as one real, visible frame of the
        # callout at the wrong (too large) size before place_near()/
        # place_outside_container() ever got a chance to compute and
        # apply its actual size and position. deiconify() happens once,
        # in GuiTour._show_step()/_show_finish(), only after that first
        # real placement has already been applied.
        self.withdraw()
        self.transient(parent_window)
        self.overrideredirect(True)
        # "-topmost", left on permanently (not toggled based on focus,
        # which an earlier version of this did - explicitly reverted:
        # hiding the callout when NASsie loses focus read as it randomly
        # vanishing, which was worse than staying visible). This pins it
        # above every window on the desktop, including other apps, but
        # that's the deliberately chosen tradeoff over disappearing - and
        # unlike the stacking-order tricks elsewhere in this file
        # (needed because an overrideredirect window isn't managed by
        # this Wayland/XWayland setup's WM at all), "-topmost" is a
        # native, reliable window attribute on Windows too, so this
        # behaves the same on both platforms.
        self.attributes("-topmost", True)
        self.configure(bg=_HIGHLIGHT_COLOR, padx=2, pady=2)

        inner = tk.Frame(self, bg=_CALLOUT_BG)
        inner.pack(fill="both", expand=True)

        ttk.Label(
            inner, text=title, font=("TkDefaultFont", 11, "bold"), background=_CALLOUT_BG
        ).pack(anchor="w", padx=10, pady=(10, 2))
        # Kept (not just set once) - see set_text(), used to flash a
        # validation error's own explanation into an already-showing
        # step's callout without tearing down and rebuilding the whole
        # window just to change its message.
        self.text_label = ttk.Label(
            inner, text=text, background=_CALLOUT_BG, wraplength=260, justify="left"
        )
        self.text_label.pack(anchor="w", padx=10, pady=(0, 8))

        btn_row = tk.Frame(inner, bg=_CALLOUT_BG)
        btn_row.pack(fill="x", padx=10, pady=(0, 10))

        if step_num is not None:
            ttk.Label(btn_row, text=f"Step {step_num} of {step_total}", background=_CALLOUT_BG).pack(side="left")
        ttk.Button(btn_row, text=skip_label, command=on_skip).pack(side="right")

    def set_text(self, text):
        self.text_label.configure(text=text)

    def _measure(self):
        # TWO idle-task passes, not one: a ttk.Label with wraplength set
        # (self.text_label above) can need a second pass to actually
        # settle - the first one, confirmed live, can still report the
        # width/height it would need laid out on ONE unwrapped line,
        # before the wrap this callout's own wraplength=260 forces is
        # accounted for. That's exactly the "one large frame before
        # sizing itself down" flash this class's withdraw()-until-placed
        # scheme (see __init__) was built to prevent - it stops the
        # WINDOW from showing before placement, but placement itself
        # still has to be reading the real final size, or revealing it
        # afterward just shows that same wrong size instead of hiding it
        # for a frame. Only actually surfaced on longer, more-wrapped
        # text (the "Close" and finish-step bodies) - short text happens
        # to settle in one pass, which is why this went unnoticed until
        # those got longer.
        self.update_idletasks()
        self.update_idletasks()
        return self.winfo_reqwidth(), self.winfo_reqheight()

    def place_near(self, widget):
        w, h = self._measure()

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

    def place_below_titlebar(self, container):
        # For a step whose whole point is the window's own native close
        # button (top-right corner, minimize/maximize/close) - that's WM/
        # OS chrome, outside container's own client-area coordinates, so
        # it can't be highlighted or pointed at directly the way a normal
        # widget can (see _HighlightBox.place_around_container()'s
        # docstring for the same limit on the highlight box). Anchoring
        # the callout to the window's top-right corner instead of below
        # its bottom edge (the generic place_near() whole-window
        # fallback) at least puts it as close to those controls as Tk's
        # own coordinate space allows, rather than ~a full window-height
        # away from what it's actually talking about.
        w, h = self._measure()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        container.update_idletasks()
        cx = container.winfo_rootx()
        cy = container.winfo_rooty()
        cw = container.winfo_width()
        margin = 8
        x = cx + cw - w
        y = cy + margin
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
        w, h = self._measure()
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

        margin = 24
        # Below the CENTER (where a centered dialog actually sits), not
        # the container's own bottom edge - "below the whole window" put
        # this as much as ~500px from where the user is actually looking
        # on a tall window (reported live: read as disconnected from the
        # app entirely, "far too low"). The messages this callout
        # accompanies (see the docstring above - it's only ever the
        # native "Done" confirmation after a share/user/attach action) run
        # up to a few lines - e.g. the apply-done message pairs a status
        # line with a follow-up hint, close to 200px tall on its own
        # before the title bar and button. Confirmed live overlapping a
        # real dialog at the previous, tighter estimate (110/14) - sized
        # up with real headroom rather than shaving it to the minimum
        # that happened to work for the shortest message, since there's
        # no reliable way to get this callout's own exact size (see
        # above) and an extra gap costs far less than an overlap.
        estimated_dialog_half_height = 160
        below_y = cy + ch // 2 + estimated_dialog_half_height + margin
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
             "New Share", "Click here to create your first share.",
             "share_dialog_opened"),
            (lambda: self._active_window, lambda: self._active_window.name_entry,
             "Share Name", "Give your share a name and click OK.",
             "share_name_confirmed"),
            (lambda: self._active_window, lambda: self._active_window.path_entry,
             "Folder",
             "Pick a folder for this share, or leave the suggested default and click "
             "OK to continue.",
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
            # _WHOLE_WINDOW, not a specific widget - UserManagementWindow
            # already has a real, working close control (its own WM
            # titlebar "X"), which is outside NASsie's own window content
            # and so physically can't be highlighted directly (see
            # _HighlightBox.place_around_container()'s docstring) -
            # highlighting the whole window instead still makes
            # unambiguous which one the text means.
            (lambda: gui._user_mgmt_window, lambda: _WHOLE_WINDOW,
             "Close", "Use the ✕ in this window's top-right corner to close it and return to the main "
             "NASsie window.",
             "user_mgmt_closed"),

            (lambda: gui.root, lambda: self._newest_share_region(),
             "Shares", "Select your share to reveal its actions.",
             "share_selected"),
            (lambda: gui.root, lambda: gui._share_action_bar.bar,
             "Delete Share",
             "Create a new user (+\U0001F464), Attach User (\U0001F517), delete share (\U0001F5D1). "
             "Press Attach User then pick the user that you just created to assign it to this share.",
             "user_attached"),
            # No widget (None) - same reasoning as the other
            # "Confirmation" steps: this is the plain Tcl tk_messageBox
            # after a successful attach, no Python widget handle to
            # attach a highlight to. There's no separate password step
            # here - the account just created via "New User" already got
            # its Samba password at creation time (see
            # GUIWizard._commit_inline_attach()'s comment), so attaching
            # it to this first share never actually prompts for one.
            (lambda: gui.root, None,
             "Confirmation", "Click OK to confirm.",
             "attach_apply_confirmed"),
        ]

    def start(self):
        mark_tour_started()
        self.index = 0
        # Several steps' own dialogs (CreateShareDialog, AddUserDialog,
        # QrCodeDialog, ChoiceDialog, plus stdlib messagebox/simpledialog)
        # call grab_set() - a LOCAL grab restricted to this application,
        # which is exactly what makes the Skip Tour button on the
        # callout (a separate Toplevel) unclickable while any of them is
        # open (reported live: stuck on the Password prompt with no way
        # out). bind_all attaches to the "all" bindtag, which every
        # widget in the app carries regardless of which one currently
        # holds the grab, so this reaches the user even then - confirmed
        # against tkinter.simpledialog.Dialog's own Escape-to-cancel
        # binding, which doesn't return "break" and so doesn't swallow
        # this. Scoped to the tour's own lifetime, not left as a global
        # app-wide Escape handler.
        self.root.bind_all("<Escape>", lambda e: self.stop())
        self._show_step()

    def _show_step(self):
        self._teardown_current()
        container_fn, widget_fn, title, text, wait_event = self.steps[self.index]
        container = container_fn()
        if container is None:
            # The window this step depends on isn't around (the tour was
            # interrupted, or something closed out of order) - bail out
            # quietly rather than point at nothing. completed=False: this
            # wasn't a deliberate Skip, it's genuinely unfinished - see
            # tour_state()'s docstring for why that distinction matters.
            self.stop(completed=False)
            return
        container.update_idletasks()
        _bring_to_front(container)
        # None (a step with nothing of NASsie's own to point at - the
        # native "Done" confirmation dialog after a share/user is created,
        # which is a plain Tcl tk_messageBox with no Python widget handle
        # to attach a highlight to) skips the highlight box entirely and
        # places the callout outside the container's own bounds instead -
        # see _Callout.place_outside_container(). _WHOLE_WINDOW highlights
        # the container itself instead of a specific widget within it -
        # see _HighlightBox.place_around_container().
        widget = widget_fn() if widget_fn is not None else None

        if widget is _WHOLE_WINDOW:
            self._highlight = _HighlightBox(container)
            self._highlight.place_around_container()
        elif widget is not None:
            self._highlight = _HighlightBox(container)
            self._highlight.place_around(widget)
        else:
            self._highlight = None

        self._callout = _Callout(container, title, text, self.index + 1, len(self.steps), on_skip=self.stop)
        if widget is _WHOLE_WINDOW:
            self._callout.place_below_titlebar(container)
        elif widget is not None:
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
        # Revealed only now, after the real placement above - see the
        # withdraw() in _Callout.__init__ for why.
        self._callout.deiconify()
        self._wait_event = wait_event
        self._track_container(container, widget)

    def _track_container(self, container, widget):
        # _HighlightBox is a real child widget of container, so it already
        # moves for free when the window is dragged - place() coordinates
        # are relative to the parent's own client area, not the screen.
        # _Callout is a separate Toplevel in absolute screen coordinates
        # (see its docstring for why), so nothing repositions it on its
        # own - re-run place_near() whenever the container actually moves
        # or resizes.
        #
        # No focus in/out tracking here - a prior version hid/showed the
        # callout with NASsie's own focus (withdraw()/deiconify()), which
        # made it vanish the instant you clicked anywhere outside the
        # dialog it was pointing into, including the main NASsie window
        # itself. That read as it randomly disappearing, not as helpful
        # restraint - explicitly reverted in favor of just staying
        # visible always (see _Callout's own "-topmost" comment).
        def reposition(event=None):
            if self._callout is None:
                return
            try:
                if widget is _WHOLE_WINDOW:
                    self._highlight.place_around_container()
                    self._callout.place_below_titlebar(container)
                elif widget is not None:
                    self._highlight.place_around(widget)
                    self._callout.place_near(widget)
                else:
                    self._callout.place_outside_container(container)
            except tk.TclError:
                pass

        self._tracking = [
            (container, "<Configure>", container.bind("<Configure>", reposition, add="+")),
        ]
        self._schedule_reassert(container)

    def _schedule_reassert(self, container):
        # Practical workaround for a real platform limitation, not a
        # proper fix - there isn't one available here: this session's
        # window manager (GNOME/Mutter, Wayland via XWayland-rootless)
        # doesn't actually apply "-topmost"/_NET_WM_STATE_ABOVE requests
        # at all - confirmed directly against the real X11 window
        # property with xprop, not just by trusting Tk's own report of
        # what it did (Tk claims success; the property is never set).
        # There's no STATIC flag that reliably keeps a normal, fully-
        # decorated window (unlike the tour callout - see its own
        # "-topmost" comment for why THAT one doesn't have this problem)
        # above other applications once set, on this WM. Repeatedly
        # re-asserting it instead - every 1.5s, for as long as this
        # step's container is still the current one - means it self-
        # heals shortly after losing front position rather than staying
        # lost, which is the best available fallback here.
        my_callout = self._callout

        def _reassert():
            if self._callout is not my_callout:
                # This step has moved on (a new one showing, or the tour
                # stopped entirely) - let the loop end rather than keep
                # raising a container nothing points into anymore.
                return
            try:
                _bring_to_front(container, steal_focus=False)
            except tk.TclError:
                return
            container.after(1500, _reassert)

        container.after(1500, _reassert)

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

    def show_name_error(self, message):
        # Called from CreateShareDialog._confirm_name() right when its own
        # "Invalid name" messagebox fires - only meaningfully acts if the
        # tour is actually showing the "Share Name" step right now
        # (checked via wait_event, the step's own unique signal - see
        # on_event()'s docstring), so it's safe to call unconditionally
        # regardless of whether the tour is even running.
        if self._callout is None or self._wait_event != "share_name_confirmed":
            return
        self._callout.set_text(f"That name didn't work — {message} Fix it and click OK again.")
        # Re-run this step's own widget_fn, not a cached reference - the
        # dialog is still the same one, but the text changing size means
        # the callout itself may have changed size too.
        widget_fn = self.steps[self.index][1]
        try:
            self._callout.place_near(widget_fn())
        except tk.TclError:
            pass

    def _show_finish(self):
        # The one step with no further action to gate on - a manual close
        # is the right call here, since the walkthrough is genuinely done.
        # Always back on the main window by this point (attaching a user
        # closes its dialog), so gui.root/shares_list, not _active_window.
        #
        # Marked completed right here, not deferred until Close is
        # actually clicked - every real action the tour asks for is done
        # by this point, so a force-quit ON this step shouldn't count as
        # "interrupted" (see tour_state()) any more than clicking Close
        # itself would.
        mark_tour_completed()
        self._teardown_current()
        widget = self._newest_share_region()
        self.root.update_idletasks()
        _bring_to_front(self.root)
        self._highlight = _HighlightBox(self.root)
        self._highlight.place_around(widget)
        self._callout = _Callout(
            self.root, "You're all set!",
            "You now have a live SMB share with an attached user. If a QR code prompt is open behind "
            "this, choose Yes to get an easy way to connect from another device.",
            None, len(self.steps), on_skip=self.stop, skip_label="Close",
        )
        self._callout.place_near(widget)
        # Revealed only now, after the real placement above - see the
        # withdraw() in _Callout.__init__ for why.
        self._callout.deiconify()
        self._wait_event = None
        self._track_container(self.root, widget)

    def stop(self, completed=True):
        # completed=True is the right default for the common caller (the
        # callout's own Skip/Close button) - a deliberate dismissal is as
        # good as finishing it, same reasoning as _show_finish's. The one
        # caller that passes False is _show_step()'s own container-
        # vanished bailout, which isn't a deliberate choice at all.
        if completed:
            mark_tour_completed()
        self.root.unbind_all("<Escape>")
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
