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

class _PointAtOnly:
    """Wraps a widget for a step where the widget itself should still get
    the usual highlight box, but _Callout.place_near(widget)'s "no room
    around the widget itself, fall back to outside the whole container"
    logic can't be trusted - see the "Cancel" step, which points at
    AddUserDialog's own Cancel button, the last control at the BOTTOM of
    a stacked Username/Password/Confirm Password/checkbox form.
    place_near()'s room check only compares against the CONTAINER's own
    top/bottom edges, not any sibling widgets in between - for a button
    that low in a tall stack, "the space between the container's top and
    the button" is almost entirely the OTHER fields, not open room, so
    the check passed even though placing the callout there covered half
    the form (confirmed live, in a screenshot). Callers wrap the widget
    in this to skip place_near() for the callout (using
    place_outside_container() instead, the same guaranteed-clear-of-the-
    dialog placement the native-messagebox "Confirmation" steps use)
    while still highlighting the real widget normally."""
    def __init__(self, widget):
        self.widget = widget


class _HighlightWholeDialog:
    """Wraps a widget for a step where the CALLOUT should still point near
    the widget as usual (place_near(widget) - the step is about that one
    field, and the callout text says so), but the HIGHLIGHT BOX should
    trace the whole dialog's border instead of tightly hugging just that
    field - reported live as looking wrong: a small field-only highlight
    read as pointing at one input in isolation rather than "this whole
    popup is what the callout below is about," for steps on a small modal
    dialog (CreateShareDialog, AddUserDialog) where the callout's own text
    already makes clear which field to use."""
    def __init__(self, widget):
        self.widget = widget


# Estimated half-height (px) of whatever this step has nothing of NASsie's
# own to point at - see _Callout.place_outside_container()'s own comment.
_DIALOG_HALF_HEIGHT = 90


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


def mark_tour_started(index=0):
    # index - the current step (see GuiTour.index) - rides along in the
    # same "started" marker as "started:<index>", rather than a separate
    # file, so there's only ever one source of truth for "is a tour in
    # flight, and if so where". Read back by tour_progress_index() on the
    # next launch (see GUIWizard._offer_tour_resume()) to pick the
    # walkthrough back up close to where it left off instead of forcing a
    # full restart from step one.
    _write_tour_state(f"started:{index}")


def mark_tour_completed():
    _write_tour_state("completed")


def tour_progress_index():
    # The furthest step index a previous, interrupted run reached (see
    # mark_tour_started()'s own comment) - 0 if there's no marker, it's
    # already "completed", or it predates this field ever existing (a
    # bare "started" with no ":<index>", from an older NASsie version -
    # falls back to a plain restart from step one, same as before this
    # existed, rather than erroring on it).
    path = _first_run_marker_path()
    try:
        with open(path) as f:
            content = f.read().strip()
    except OSError:
        return 0
    if not content.startswith("started:"):
        return 0
    try:
        return max(0, int(content.split(":", 1)[1]))
    except ValueError:
        return 0


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
        # tree.bbox(i) doesn't just return '' for an item id that no
        # longer exists in this tree (a resumed step referencing a row
        # from a previous, now-gone process, or one this fresh process
        # simply hasn't created yet) - it raises TclError outright, unlike
        # most other winfo_*-style Tk queries. The comment below already
        # documented "none of the ids exist anymore" as an expected,
        # handled case, but the plain list comprehension this used to be
        # let that exception escape before the `if b` filter ever got a
        # chance to run, taking down the whole step (see _step_resolves()'s
        # own comment on where that ends up: a silently swallowed
        # exception in a windowed/no-console build, with nothing ever
        # visibly starting for the user).
        boxes = []
        for i in self.item_ids:
            try:
                b = self.tree.bbox(i)
            except tk.TclError:
                continue
            if b:
                boxes.append(b)
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
        # (self.root - the dialog itself for _HighlightWholeDialog steps),
        # not a single widget within it.
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
    # being paced by clicks through a narrated slideshow. (An earlier
    # version of this session tried adding them - abandoned: several
    # steps' own dialogs hold a real Tk grab, which blocks mouse clicks on
    # this separate Toplevel entirely, and no reliable app-level
    # workaround was ever found for that. See GUIWizard._offer_tour_resume()
    # instead - closing NASsie mid-tour and reopening it picks the tour
    # back up close to where it left off, which covers the same underlying
    # need without requiring in-session back/forward navigation at all.)
    # Skip is the one manual escape hatch throughout (relabeled "Close"
    # for the final, non-gated step).
    def __init__(self, parent_window, title, text, on_skip, skip_label="Skip Tour"):
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
        # A one-off "-topmost" only guarantees this stays above every
        # NON-topmost window - among the (several) OTHER windows in this
        # app that are ALSO topmost (every dialog, via
        # _bring_window_to_front() - and gui.py's own _Toast), stacking
        # order between topmost windows still just follows whichever one
        # was raised MOST RECENTLY. A fresh dialog or toast opened while
        # a tour step is showing would win that and bury the callout
        # underneath it - reported live ("Skip Tour... gets covered by
        # other popups"). Re-lifting this on a recurring timer (see
        # _relift() below) reclaims the front shortly after, rather than
        # trying to hook every single place in gui.py that raises a
        # window.
        self._relift_job = None
        self.bind("<Destroy>", self._cancel_relift, add="+")
        self._relift()

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

        ttk.Button(btn_row, text=skip_label, command=on_skip).pack(side="right")

    def _relift(self):
        try:
            self.lift()
        except tk.TclError:
            # Already destroyed - _cancel_relift() should have stopped
            # this loop already, but a pending after() callback can still
            # be in flight the instant destroy() runs.
            return
        self._relift_job = self.after(500, self._relift)

    def pause_relift(self):
        # For the ONE case this recurring lift() actively fights instead
        # of helps: GuiTour._confirm_and_stop() opens its own confirmation
        # dialog (see _ConfirmDialog) from a button ON this very callout,
        # and ALSO sets that dialog "-topmost" (needed against every
        # OTHER always-on-top NASsie window - see _ConfirmDialog's own
        # docstring). Between two topmost windows, stacking order still
        # just follows whichever was raised most recently - Tk keeps
        # servicing other after() callbacks during that dialog's own
        # wait_window() loop, so left running, this timer would
        # eventually re-win and bury the very dialog it opened. Callers
        # must pair this with resume_relift() once the dialog closes.
        self._cancel_relift()

    def resume_relift(self):
        if self._relift_job is None:
            self._relift()

    def _cancel_relift(self, event=None):
        if self._relift_job is not None:
            try:
                self.after_cancel(self._relift_job)
            except tk.TclError:
                pass
            self._relift_job = None

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
        # "Room below/above the WIDGET" used to mean just "doesn't cross
        # the CONTAINER's own top/bottom edge" - true even when a sibling
        # field sits in that space, not open room at all. Confirmed live:
        # the "Username and Password" step (username_entry, at the TOP of
        # a multi-field dialog) placed itself "below the widget" and
        # covered the Password/Confirm Password entries sitting right
        # underneath it (both siblings of username_entry, gridded on the
        # same dialog), once the dialog's own padding (nassie_ttk's,
        # different from the old default theme's) shifted the numbers
        # enough to newly trigger this.
        #
        # A proximity-to-container-edge heuristic (an earlier version of
        # this fix) overcorrected: it also blocked the "New Share"/"Manage
        # Users" toolbar buttons from placing below themselves, since
        # they sit near the TOP of the tall main window, not the bottom -
        # even though nothing is actually in the way there (their only
        # sibling is each other, side by side in the same toolbar row).
        # That forced them into the whole-window fallback instead, ~500px
        # from the button - exactly the disconnected-bubble problem this
        # method's own docstring above describes. Checking for an actual
        # SIBLING (a widget sharing widget's own parent - the level a
        # dialog's stacked fields or a toolbar's buttons live at) in the
        # candidate rect targets the real conflict directly instead of
        # guessing from position.
        def _sibling_in_rect(y0, y1):
            # widget isn't always a real Tk widget - _TreeRegion (see its
            # own docstring) adapts one or more Treeview ROWS to this same
            # winfo_* interface for the "Shares" step, and has no .master/
            # sibling concept at all (confirmed live: AttributeError,
            # '_TreeRegion' object has no attribute 'master', the first
            # time this ran against a real installed build). Nothing
            # meaningful to check there - the geometry-fit condition above
            # is on its own exactly what this used to be before siblings
            # were checked at all, which was already fine for a tree
            # region (there's no adjacent sibling FIELD to cover, just
            # more of the same tree).
            master = getattr(widget, "master", None)
            if master is None:
                return False
            for sib in master.winfo_children():
                if sib is widget or not sib.winfo_ismapped():
                    continue
                sx0 = sib.winfo_rootx()
                sy0 = sib.winfo_rooty()
                sx1 = sx0 + sib.winfo_width()
                sy1 = sy0 + sib.winfo_height()
                if sx0 < wx + w and sx1 > wx and sy0 < y1 and sy1 > y0:
                    return True
            return False

        if (below_widget_y + h <= min(cbottom, screen_h)
                and not _sibling_in_rect(below_widget_y, below_widget_y + h)):
            x, y = wx, below_widget_y
        elif (above_widget_y >= max(cy, 0)
                and not _sibling_in_rect(above_widget_y, above_widget_y + h)):
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

    def place_outside_container(self, container, estimated_dialog_half_height=90):
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
            self.after(
                20, lambda: self.place_outside_container(container, estimated_dialog_half_height)
            )
            return
        cx = container.winfo_rootx()
        cy = container.winfo_rooty()

        margin = 24
        # Below the CENTER (where a centered dialog actually sits), not
        # the container's own bottom edge - "below the whole window" put
        # this as much as ~500px from where the user is actually looking
        # on a tall window (reported live: read as disconnected from the
        # app entirely, "far too low"). estimated_dialog_half_height has
        # no reliable way to be exact (see above - there's no way to
        # measure the real dialog), so callers pass a value sized to
        # THEIR OWN message: most of these are one short line ("Added
        # 'x' to share 'y'.", "'x' has been created.") - the default here
        # - but the share-creation apply-done message pairs a status line
        # with a follow-up hint, close to 200px tall on its own before
        # the title bar and button, and passes a taller estimate
        # explicitly (see _build_steps()). Confirmed live both ways this
        # can go wrong: too tight overlapped a real dialog; sized up
        # uniformly to cover that one long case instead left a large gap
        # under every short one.
        below_y = cy + ch // 2 + estimated_dialog_half_height + margin
        y = below_y if below_y + h <= screen_h else max(cy - margin - h, 0)
        x = cx + (cw - w) // 2
        x = max(0, min(x, screen_w - w))
        y = max(0, min(y, screen_h - h))
        self.geometry(f"+{x}+{y}")
        self.lift()


class _ConfirmDialog(tk.Toplevel):
    """A small Yes/No confirmation, built as another tour window - same
    borderless, overrideredirect, teal-bordered look every other tour
    step's own _Callout has - rather than a native OS dialog. Used in
    place of tkinter.messagebox.askyesno() for the Skip Tour confirmation
    specifically (see GuiTour._confirm_and_stop()). Two things read wrong
    with an OS-chrome popup here, both confirmed live, first with a plain
    tk_messageBox and then with a normal decorated Toplevel: it looked
    out of place next to every other tour surface (all borderless
    callouts, no titlebar), and - the more important one - it never
    reliably stacked to the front either way. That second part traces
    back to the exact same reason _Callout's own docstring gives for why
    IT uses "-topmost" instead of the stacking-order tricks used
    elsewhere in this file: a WM-managed (decorated) window's stacking
    under this Wayland/XWayland setup isn't reliably driven by
    "-topmost" the way an overrideredirect window's is, since Mutter
    isn't managing an overrideredirect window's stacking at all. Going
    overrideredirect here isn't just cosmetic, in other words - it's
    what actually fixes "doesn't go to the front" too."""
    def __init__(self, parent, title, text):
        super().__init__(parent)
        self.withdraw()
        self.transient(parent)
        self.overrideredirect(True)
        self.protocol("WM_DELETE_WINDOW", self._no)
        self.result = False
        # Same border/background recipe as _Callout - see its own
        # comment on _HIGHLIGHT_COLOR/_CALLOUT_BG.
        self.configure(bg=_HIGHLIGHT_COLOR, padx=2, pady=2)

        inner = tk.Frame(self, bg=_CALLOUT_BG)
        inner.pack(fill="both", expand=True)
        ttk.Label(
            inner, text=title, font=("TkDefaultFont", 11, "bold"), background=_CALLOUT_BG,
        ).pack(anchor="w", padx=10, pady=(10, 2))
        ttk.Label(
            inner, text=text, background=_CALLOUT_BG, wraplength=260, justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 8))

        btn_row = tk.Frame(inner, bg=_CALLOUT_BG)
        btn_row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btn_row, text="No", command=self._no).pack(side="right")
        ttk.Button(btn_row, text="Yes", command=self._yes).pack(side="right", padx=(0, 6))

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        # See _Callout's own identical attribute for why this is
        # permanent, not toggled - every NASsie window (this one
        # included) needs to stay above other applications for as long
        # as it's open.
        self.attributes("-topmost", True)
        self.lift()
        # A single -topmost/lift() right after a window is first created
        # and mapped, confirmed live (bisected across a dozen runs, ~1 in
        # 3 reproducing it), can still race the WM/X server actually
        # processing it and land un-topmost regardless - not fixed by
        # adding more update()/update_idletasks() calls here either, only
        # by giving it more real wall-clock time to land. Reasserting on
        # a short recurring timer (same technique _Callout's own
        # _relift() uses) catches that within one tick instead of
        # depending on a single call winning the race every time -
        # cancelled on <Destroy> below.
        self._relift_job = None
        self.bind("<Destroy>", self._cancel_relift, add="+")
        self._relift()
        self.grab_set()
        self.focus_set()

    def _relift(self):
        try:
            self.attributes("-topmost", True)
            self.lift()
        except tk.TclError:
            return
        self._relift_job = self.after(150, self._relift)

    def _cancel_relift(self, event=None):
        if self._relift_job is not None:
            try:
                self.after_cancel(self._relift_job)
            except tk.TclError:
                pass
            self._relift_job = None

    def _yes(self):
        self.result = True
        self.destroy()

    def _no(self):
        self.result = False
        self.destroy()


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
        # (bound_widget, event_name, funcid, reposition_fn) for the
        # current step's container tracking - see
        # _track_container()/_untrack_container().
        self._tracking = []
        # Set by pause_tracking() (see its own docstring) to whatever
        # _tracking held at the moment of pausing - None means nothing's
        # currently paused (the normal state).
        self._paused = None
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

    def _newest_user_row(self):
        # Just the user row itself (not the share above it too - see
        # _newest_share_region()) - for the "select the user you just
        # attached" step, which needs to point at exactly the row the
        # user still has to click, not the whole region.
        tree = self.gui.shares_list
        children = tree.get_children("")
        if not children:
            return tree
        share_item = children[-1]
        user_children = tree.get_children(share_item)
        if not user_children:
            return tree
        return _TreeRegion(tree, [user_children[-1]])

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
        # New Share and New User (from the Users panel) are both triggered
        # by a shaded "+" row now instead of a toolbar button (see gui.py's
        # own _AddRowTrigger) - but still open the same real popup dialogs
        # (CreateShareDialog/AddUserDialog) they always did; an earlier
        # version of this session had the row itself expand into an inline
        # form instead, reported live as reading wrong ("you've rebuilt
        # the form inside of this row"), so the popup-pointing steps below
        # are back to self._active_window + _HighlightWholeDialog (traces
        # the whole popup's border, not just the one field - see that
        # class's own docstring) exactly as before that detour. "Manage
        # Users"/"New User"(-panel-toggle)/"Close" are unaffected either
        # way - those still point at the docked panel's own toggle button
        # and "+" row trigger, not a popup.
        return [
            # "New Share" is a real row of gui.shares_list now (row #1 -
            # see gui.py's own _add_share_row_id), not a separate widget,
            # so it needs the same _TreeRegion adapter the "Shares" step
            # below already uses to point at a specific tree row rather
            # than the whole (much taller) tree widget.
            (lambda: gui.root, lambda: _TreeRegion(gui.shares_list, [gui._add_share_row_id]),
             "New Share", "Click here to create your first share.",
             "share_dialog_opened"),
            (lambda: self._active_window, lambda: _HighlightWholeDialog(self._active_window.name_entry),
             "Share Name", "Type a share name and click OK.",
             "share_name_confirmed"),
            (lambda: self._active_window, lambda: _HighlightWholeDialog(self._active_window.path_entry),
             "Folder",
             "Pick a folder for this share or use the provided default, then click OK.",
             "share_created"),
            # No separate "Confirmation" step after this one anymore - the
            # "Share Creation" toast that follows (GUIWizard._show_toast())
            # is self-explanatory on its own, and a callout ABOUT it ended
            # up landing right on top of it (both center on the shares
            # list's own bottom area now - see _Toast's own docstring),
            # covering the very thing it was explaining (reported live).
            # The next step just picks up once "share_created" fires,
            # same as every other step whose own action doesn't need a
            # separate acknowledgment.

            (lambda: gui.root, lambda: gui._users_toggle_btn.label,
             "Manage Users", "Open Manage Users to create and delete user accounts.",
             "user_mgmt_opened"),
            # "New User" is a real row of the Users panel's own Treeview
            # now too (row #1 - see UserManagementPanel.refresh()'s
            # add_row tag), same _TreeRegion adapter the "New Share" step
            # above uses for its own list row.
            (lambda: gui.root,
             lambda: _TreeRegion(gui._user_mgmt_panel.users_list, [gui._user_mgmt_panel._add_user_row_id]),
             "New User", "Click New User to create a new share user.",
             "user_dialog_opened"),
            (lambda: self._active_window, lambda: _HighlightWholeDialog(self._active_window.username_entry),
             "Username and Password", "Type a username and password, then click OK.",
             "user_created"),
            # No separate "Confirmation" step after this one either - see
            # the identical removal (and its own comment) right after
            # "Folder" above.
            # Same toggle button as the "Manage Users" step above - closing
            # the panel is just clicking it again now, not a native
            # titlebar ✕ (there's no separate window left to have one -
            # see UserManagementPanel's own docstring).
            (lambda: gui.root, lambda: gui._users_toggle_btn.label,
             "Close", "Click the Users button again to close this panel and return to the shares list.",
             "user_mgmt_closed"),

            (lambda: gui.root, lambda: self._newest_share_region(),
             "Shares", "Select your share to reveal its actions.",
             "share_selected"),
            # Walking every share-row button in its actual left-to-right
            # order (New User, Attach User, Delete Share - see
            # GUIWizard._build_share_action_bar()) - indexed off the
            # live action bar rather than a stored reference, since it's
            # rebuilt from scratch on every selection (see
            # _RowActionBar's own docstring); safe to index into
            # positionally because it's rebuilt in this same fixed order
            # every time a share row (not a user row) is selected.
            (lambda: gui.root, lambda: gui._share_action_bar.bar.winfo_children()[0],
             "New User",
             "Click to open the New User dialog.",
             "user_dialog_opened"),
            (lambda: self._active_window, lambda: _PointAtOnly(self._active_window.cancel_button),
             "New User",
             "Users added this way are automatically attached to the selected share. "
             "Press the Cancel button.",
             "user_dialog_cancelled"),
            (lambda: gui.root, lambda: gui._share_action_bar.bar.winfo_children()[1],
             "Attach User",
             "Press Attach User.",
             "attach_dropdown_opened"),
            # _PointAtOnly - the combobox itself still gets the usual
            # highlight box, but place_near()'s normal "is there a
            # sibling in the way" room check has no way to see the
            # native dropdown LIST this combobox opens right underneath
            # itself (see _build_inline_attach()'s own after() call) -
            # that's not a real Tk sibling widget the geometry manager
            # knows about, just an ephemeral popup, so the check found
            # "room" there and placed the callout right on top of it
            # (reported live, screenshotted: the callout covering "bob"/
            # "existing account" in the open list). place_outside_
            # container() (what _PointAtOnly falls back to) sits well
            # clear of it instead.
            (lambda: gui.root, lambda: _PointAtOnly(gui._share_action_bar.bar.winfo_children()[0]),
             "Attach User",
             "The dropdown shows all available users to attach to the selected share, "
             "including ones not made by NASsie (indicated by \"existing account\"). Select "
             "the user that you just created.",
             "user_attached"),
            # No separate "Confirmation" step after this one either - see
            # the identical removal (and its own comment) after "Folder"
            # earlier. Goes straight to "Delete Share" below once
            # "user_attached" fires - there's no separate password step
            # to wait through either: the account just created via "New
            # User" already got its Samba password at creation time (see
            # GUIWizard._commit_inline_attach()'s comment), so attaching
            # it to this first share never actually prompts for one.
            (lambda: gui.root, lambda: gui._share_action_bar.bar.winfo_children()[2],
             "Delete Share",
             "Press the Delete share button.",
             "share_delete_dialog_opened"),
            # No widget (None) - same reasoning as the other
            # "Confirmation" steps: GUIWizard._delete_selected_share()'s
            # own guarded info box is a plain tk_messageBox, no Python
            # widget handle to attach a highlight to.
            (lambda: gui.root, None,
             "Delete Share", "Press OK to return to the main window. Your share will NOT be deleted.",
             "share_delete_dialog_cancelled"),

            (lambda: gui.root, lambda: self._newest_user_row(),
             "Select User", "Select the user you just attached to reveal its own actions.",
             "share_selected"),
            # Same reasoning as the share row above - walking the user
            # row's buttons in order (permission toggle, QR code, detach
            # - see the same _build_share_action_bar()).
            (lambda: gui.root, lambda: gui._share_action_bar.bar.winfo_children()[0],
             "Permission",
             "Toggles this user between read-only (\U0001F4D6) and read-write (\U0001F4DD) - "
             "click it twice to see both and land back on the value you started with.",
             "access_level_changed"),
            (lambda: gui.root, lambda: gui._share_action_bar.bar.winfo_children()[1],
             "QR Code",
             "Click the QR Code button.",
             "qr_dialog_opened"),
            (lambda: self._active_window, lambda: _PointAtOnly(self._active_window.cancel_button),
             "QR Code",
             "Enter the user password to generate a QR code. Click Cancel for now.",
             "qr_prompt_cancelled"),
            (lambda: gui.root, lambda: gui._share_action_bar.bar.winfo_children()[2],
             "Detach",
             lambda: (
                 f"This removes {gui._selected_share_and_user()[1]}'s access to "
                 f"{gui._selected_share_and_user()[0]}. Click Detach to view the action."
             ),
             "user_detach_dialog_opened"),
            # No widget (None) - same reasoning as the other
            # "Confirmation" steps: GUIWizard._unattach_selected_user()'s
            # own guarded info box is a plain tk_messageBox, no Python
            # widget handle to attach a highlight to.
            (lambda: gui.root, None,
             "Detach", "Press OK to return to the main window. This user will NOT be detached.",
             "user_detach_dialog_cancelled"),
        ]

    def _step_resolves(self, index, check_widget=False):
        # True if this step's own container currently exists and is a
        # real, live widget - False for both container_fn() returning
        # None outright (see _show_step()'s own use of this) AND the
        # sneakier case of a step whose container_fn is `lambda: self.
        # _active_window` where that dialog has since been destroyed:
        # closing it doesn't clear the reference, just the underlying Tk
        # window, so container_fn() itself still returns a real (non-
        # None) Python object - only winfo_exists() (safe to call on an
        # already-destroyed widget; returns False rather than raising,
        # unlike nearly everything else) actually catches that one.
        try:
            container = self.steps[index][0]()
        except tk.TclError:
            return False
        if container is None:
            return False
        try:
            if not container.winfo_exists():
                return False
        except tk.TclError:
            return False
        # check_widget - only True from _resolve_resume_index() below, NOT
        # from _show_step()'s own (pre-existing, container-only) call.
        # Container-only is correct there: this same method doubles as
        # _show_step()'s guard on EVERY live step transition of a normal,
        # in-progress tour, not just a resumed one, and widget_fn() isn't
        # always safe to call speculatively there - e.g. the "Add User"/
        # "New User" steps reference a Treeview add-row id
        # (UserManagementPanel._add_user_row_id) that a just-opened
        # panel's own refresh() sets asynchronously (fetches list_users()
        # in a background thread, applies once it lands - see refresh()'s
        # own comment); calling widget_fn() here to "just check" during
        # ordinary forward progress could observe it mid-flight, before
        # that attribute even exists yet, and wrongly conclude the step
        # doesn't resolve. Reported live, both platforms: an in-progress
        # tour dying right after the step that opens Users/attaches a
        # user, nothing to do with any stale resume state - exactly that.
        # check_widget=True is safe (and needed) ONLY when walking back
        # through a stale, previous-process resume_index below, since
        # nothing there is "about to become valid a moment later" the way
        # a same-process async refresh is - it's either real right now or
        # it's from a process that's gone.
        if not check_widget:
            return True
        # Container-only used to be the whole check - correct for most
        # steps, but not the ones whose widget_fn depends on session state
        # the container itself says nothing about (a specific Treeview row
        # id from a *previous* process, e.g. - gui.root always exists
        # regardless). Confirmed live: resuming an interrupted tour landed
        # on exactly such a step, widget_fn's _TreeRegion referenced a row
        # id that no longer existed, and the TclError that used to escape
        # from deep inside _show_step()'s highlight/callout placement (see
        # _TreeRegion._bbox()'s own comment) took the whole step down with
        # nothing ever shown - on a windowed/no-console Windows build, Tk's
        # default callback-exception handling prints that to a stderr
        # nobody can see, so it read as "clicking Yes does nothing" with
        # no visible error at all. Actually resolving the widget here too
        # (not just checking the container) means _resolve_resume_index()
        # correctly walks back PAST a step like that to the nearest one
        # that's fully real, instead of confidently landing on one that
        # looks fine and then silently failing once _show_step() commits
        # to it.
        widget_fn = self.steps[index][1]
        if widget_fn is not None:
            try:
                widget = widget_fn()
                if widget is not None:
                    target = widget.widget if isinstance(widget, (_PointAtOnly, _HighlightWholeDialog)) else widget
                    if target is not None:
                        target.winfo_rootx()
            except (tk.TclError, AttributeError, IndexError):
                return False
        return True

    def _resolve_resume_index(self, index):
        # See start()'s own comment for why this exists at all. A fresh
        # GuiTour's self._active_window is always None (nothing's been
        # opened yet in THIS process), so any step whose container_fn is
        # `lambda: self._active_window` resolves to None right now
        # regardless of what index was asked for - walk backward until
        # landing on one that doesn't (gui.root itself, at worst, index
        # 0 - always resolves).
        index = max(0, min(index, len(self.steps) - 1))
        while index > 0 and not self._step_resolves(index, check_widget=True):
            index -= 1
        return index

    def start(self, resume_index=0):
        # resume_index > 0 means GUIWizard._offer_tour_resume() is picking
        # a previously-interrupted run back up (see tour_progress_index())
        # rather than starting fresh - _resolve_resume_index() below is
        # what actually makes that safe: several steps only resolve their
        # own container against self._active_window (a dialog from the
        # OLD process, long gone by the time a fresh one calls this), and
        # container_fn() returning None for one of those is exactly what
        # _show_step() already treats as "bail out, nothing to point at"
        # (see its own comment) - so a raw resume_index landing on one of
        # those would silently kill the tour again immediately, right
        # back to square one. Walking back to the nearest step that
        # currently resolves - always true for at least step 0, gui.root
        # itself - lands on the "click to open X" step that leads back
        # into it instead, which is the natural place to pick up: redo
        # that one click and everything past it is real again.
        self.index = self._resolve_resume_index(resume_index)
        mark_tour_started(self.index)
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
        self.root.bind_all("<Escape>", lambda e: self._confirm_and_stop())
        self._show_step()

    def _show_step(self):
        self._teardown_current()
        # Persisted every time this can change, not just once at start() -
        # cheap (one small file write), and means a force-quit mid-step
        # loses at most the current step, not the whole run since the
        # last time _offer_tour_resume() happened to fire. See
        # GUIWizard._offer_tour_resume() for where this gets read back on
        # the next launch.
        mark_tour_started(self.index)
        container_fn, widget_fn, title, text, wait_event = self.steps[self.index]
        # A step's text is usually a plain string, but the "Detach" step
        # needs the actual username/share name being demonstrated (see
        # its own entry in _build_steps()) - callable, called lazily
        # same as container_fn/widget_fn, so it reads gui's CURRENT
        # selection at the moment this step actually shows rather than
        # whatever it was when the step list was first built.
        if callable(text):
            text = text()
        container = container_fn()
        # Not just "is None" - a step whose container_fn is `lambda: self.
        # _active_window` still returns a real (non-None) Python object
        # once that dialog has been destroyed (closing it doesn't clear
        # the reference, just the underlying Tk window) - self._step_
        # resolves() catches that case too via winfo_exists(), which is
        # safe to call on an already-destroyed widget (returns False,
        # doesn't raise), unlike nearly everything else below that DOES
        # raise TclError on one. Reported live as an actual crash:
        # Previous, clicked from a step or two past a completed dialog
        # (its own window long since closed on success), landed back on
        # one of these and blew up right here.
        if container is None or not self._step_resolves(self.index):
            # The window this step depends on isn't around (the tour was
            # interrupted, or something closed out of order) - bail out
            # quietly rather than point at nothing. completed=False: this
            # wasn't a deliberate Skip, it's genuinely unfinished - see
            # tour_state()'s docstring for why that distinction matters.
            self.stop(completed=False)
            return
        container.update_idletasks()
        _bring_to_front(container)
        try:
            # None (a step with nothing of NASsie's own to point at - the
            # native "Done" confirmation dialog after a share/user is
            # created, which is a plain Tcl tk_messageBox with no Python
            # widget handle to attach a highlight to) skips the highlight
            # box entirely and places the callout outside the container's
            # own bounds instead - see _Callout.place_outside_container().
            widget = widget_fn() if widget_fn is not None else None
            point_at_only = isinstance(widget, _PointAtOnly)
            whole_dialog_highlight = isinstance(widget, _HighlightWholeDialog)
            highlight_target = widget.widget if (point_at_only or whole_dialog_highlight) else widget

            if whole_dialog_highlight:
                self._highlight = _HighlightBox(container)
                self._highlight.place_around_container()
            elif highlight_target is not None:
                self._highlight = _HighlightBox(container)
                self._highlight.place_around(highlight_target)
            else:
                self._highlight = None

            self._callout = _Callout(container, title, text, on_skip=self._confirm_and_stop)
            if highlight_target is not None and not point_at_only:
                self._callout.place_near(highlight_target)
            else:
                # Both the widget=None steps (native tk_messageBox
                # confirmations, no Python widget handle at all) and
                # _PointAtOnly ones (a real widget IS highlighted above,
                # but place_near() can't be trusted for it - see the
                # class's own docstring) land here.
                half_height = _DIALOG_HALF_HEIGHT
                self._callout.place_outside_container(container, half_height)
                # One extra self-correction shortly after - confirmed live
                # (intermittently, timing-dependent - a race, not a one-off)
                # landing at a stale (0, 0) with no later event to ever fix
                # it, right after a step transition that both destroys a
                # dialog AND withdraws that dialog's own OWNER window in
                # the same beat (e.g. AddUserDialog closing while
                # UserManagementWindow withdraws right under it) -
                # evidently sometimes enough WM churn that even
                # container.update()'s round trip inside
                # place_outside_container() itself isn't a hard guarantee.
                # This costs nothing when the first placement was already
                # right - it just recomputes the same answer.
                index_at_schedule = self.index
                def _self_correct(step_index=index_at_schedule):
                    if self._callout is not None and self.index == step_index:
                        try:
                            self._callout.place_outside_container(container, half_height)
                        except tk.TclError:
                            pass
                container.after(120, _self_correct)
            # Revealed only now, after the real placement above - see the
            # withdraw() in _Callout.__init__ for why.
            self._callout.deiconify()
        except (tk.TclError, AttributeError, IndexError):
            # Defense in depth alongside _step_resolves() now also probing
            # widget_fn() (see its own comment) - that check runs BEFORE
            # this method ever commits to showing this step, so in
            # practice it should already have steered a resumed tour past
            # anything that would land here. This is the fallback for
            # whatever that check doesn't happen to anticipate: better a
            # quiet, recoverable stop (same outcome _step_resolves()
            # failing produces above) than an exception escaping into a
            # Tk after()-callback, where a windowed/no-console Windows
            # build has nowhere visible to report it - which is exactly
            # how "clicking Yes on Resume Tour does nothing, no error, no
            # tour" was reported and reproduced.
            self.stop(completed=False)
            return
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
        point_at_only = isinstance(widget, _PointAtOnly)
        whole_dialog_highlight = isinstance(widget, _HighlightWholeDialog)
        highlight_target = widget.widget if (point_at_only or whole_dialog_highlight) else widget

        def reposition(event=None):
            if self._callout is None:
                return
            try:
                if whole_dialog_highlight:
                    self._highlight.place_around_container()
                elif highlight_target is not None:
                    # self._highlight is None (see _show_step()) when
                    # highlight_target is None - nothing to reposition.
                    self._highlight.place_around(highlight_target)

                if highlight_target is not None and not point_at_only:
                    self._callout.place_near(highlight_target)
                else:
                    self._callout.place_outside_container(container, _DIALOG_HALF_HEIGHT)
            except tk.TclError:
                pass

        # after_idle, not a direct bind - reposition() calls
        # widget.update_idletasks() (see _HighlightBox.place_around()'s
        # own docstring for why: the highlight target's geometry needs
        # to be genuinely settled, not stale, before reading it), which
        # is a real, synchronous, APPLICATION-WIDE flush of every
        # pending layout/redraw task - not scoped to just this highlight.
        # Calling that directly inside the <Configure> handler ran it
        # synchronously in the middle of Tk's own processing of that
        # exact event, for every <Configure> the container ever fires -
        # including every one of GUIWizard._animate_root_width()'s own
        # real per-step geometry() calls, whenever a tour step happens
        # to be showing (confirmed live: the tour is active, un-skipped,
        # in every screen-recorded report of "the whole window
        # repainting" so far). Deferring to after_idle doesn't remove
        # that flush - correctness still needs it - but it stops this
        # forcing itself synchronously into the middle of root's own
        # event handling, letting the animation's own after()-scheduled
        # steps run on their own schedule instead of queuing up behind
        # it.
        # reposition itself (not just the bind() funcid) is kept
        # alongside it - see pause_tracking()/resume_tracking() below,
        # which need to unbind and later re-bind this SAME closure
        # (container/widget/highlight_target etc., all captured above),
        # not a fresh one.
        self._tracking = [
            (container, "<Configure>", container.bind(
                "<Configure>", lambda e: container.after_idle(reposition), add="+"
            ), reposition),
        ]
        self._schedule_reassert(container)

    def pause_tracking(self):
        """Temporarily unbinds every current step's own container
        tracking (a no-op if nothing's tracked right now - no step
        showing a callout, or already paused) - see
        GUIWizard._animate_root_width()'s own call for why: reposition()
        calls widget.update_idletasks() (see _track_container()'s own
        comment), a real, synchronous, application-wide flush of every
        pending layout/redraw task, on every single <Configure> the
        tracked container fires - including every one of that method's
        own real per-step geometry() calls, for however many steps a
        glide runs. Pausing here means that flush happens once, in
        resume_tracking(), instead of once per step. Keeps
        self._tracking itself intact (just the live bindings removed),
        so resume_tracking() knows exactly what to re-bind - unlike
        _untrack_container(), which clears it because a step is
        actually ending, not just this callout's tracking going quiet
        for a moment."""
        self._paused = self._tracking
        for widget, event, funcid, _reposition in self._tracking:
            try:
                widget.unbind(event, funcid)
            except tk.TclError:
                pass

    def resume_tracking(self):
        """Re-binds whatever pause_tracking() paused (a no-op if nothing
        was), and runs each one's own reposition() exactly once right
        away - not waiting for the next real <Configure> - so nothing's
        left stale for however long the pause lasted (e.g. a whole
        panel-toggle glide's worth of geometry() calls the tracker never
        saw)."""
        paused = getattr(self, "_paused", None)
        if not paused:
            return
        self._paused = None
        new_tracking = []
        for widget, event, _old_funcid, reposition in paused:
            try:
                funcid = widget.bind(event, lambda e, r=reposition: widget.after_idle(r), add="+")
                new_tracking.append((widget, event, funcid, reposition))
                reposition()
            except tk.TclError:
                pass
        self._tracking = new_tracking

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
        #
        # Linux-only: unlike Mutter/Wayland, Windows honors "-topmost"
        # correctly and keeps it set once applied - the one-time
        # _bring_to_front() call already made when the step first shows
        # (_show_step(), _track_container()'s own caller) is enough
        # there. Running this loop unconditionally on Windows anyway was
        # a real bug, not just needless: _bring_to_front()'s False ->
        # update() -> True dance forces a synchronous full redraw and a
        # topmost/z-order toggle on whatever window the step currently
        # targets, every 1.5s, for as long as that step is up - visible
        # as a widespread "caught in a refresh" flicker (including the
        # text-insertion caret dropping out of whatever field has focus)
        # across every NASsie window, confirmed as Windows-only and
        # traced to this loop.
        if platform.system() != "Linux":
            return
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
        for widget, event, funcid, _reposition in self._tracking:
            try:
                widget.unbind(event, funcid)
            except tk.TclError:
                # Same reasoning as _HighlightBox.destroy() - the window
                # it was bound to may already be gone.
                pass
        self._tracking = []
        # A step actually ending is a real end, not a pause - nothing
        # left to resume later (see pause_tracking()/resume_tracking()).
        self._paused = None

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
            "You now have a live SMB share with an attached user.",
            on_skip=self.stop, skip_label="Close",
        )
        self._callout.place_near(widget)
        # Revealed only now, after the real placement above - see the
        # withdraw() in _Callout.__init__ for why.
        self._callout.deiconify()
        self._wait_event = None
        self._track_container(self.root, widget)

    def _confirm_and_stop(self):
        # A second verification before an actual skip takes effect -
        # reported live wanting the guide close to undefeatable, so one
        # stray click on Skip Tour (or Escape press) can't silently drop
        # out of a real walkthrough. This wraps the two USER-INITIATED
        # exits (the Skip Tour button, Escape) - not stop() itself, which
        # the FINISH screen's own "Close" button still calls directly
        # (nothing left to skip there - the tour's already done, this is
        # just dismissing the congratulations) and the involuntary
        # container-vanished bailout also calls directly (not a user
        # choice to confirm at all).
        #
        # _bring_to_front() (the same False->True re-trigger fix relied on
        # everywhere else in this file for the identical multi-window
        # race) so root itself is in front before _ConfirmDialog centers
        # itself over it below - see that class's own docstring for why a
        # plain tk_messageBox was wrong for this specifically (positioning
        # AND stacking, both confirmed live).
        _bring_to_front(self.root)
        # See _Callout.pause_relift()'s own docstring for why this has to
        # stop competing with the very dialog it's about to open.
        if self._callout is not None:
            self._callout.pause_relift()
        dialog = _ConfirmDialog(self.root, "Skip Tour", "Skip the rest of the guided tour?")
        self.root.wait_window(dialog)
        if self._callout is not None:
            self._callout.resume_relift()
        if dialog.result:
            self.stop()

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
