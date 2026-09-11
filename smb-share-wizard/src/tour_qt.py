"""Guided first-run tour - PySide6 port of tour.py's GuiTour.

The step-machine design (event-driven advancement, resumable-via-disk
state) is UI-framework-agnostic and ports conceptually intact - see the
migration plan's own note. The persistence functions (tour_state(),
mark_tour_started(), mark_tour_completed(), tour_progress_index(), and
their own helpers) have zero tkinter dependency at all and are reused
directly from tour.py rather than duplicated here - only the visual
pieces (highlight box, callout bubble, confirmation dialog) and the
step machine itself needed a real port.

Deliberately simpler than tour.py in a few places, not just translated:
- No _ConfirmDialog port - a plain QMessageBox.question() replaces it.
  tour.py's own docstring on that class is explicit that its entire
  reason for existing (borderless + overrideredirect) was working
  around Mutter/Wayland not honoring "-topmost" for a WM-managed
  window - a Linux/X11-specific problem. Qt/Windows' native modal
  QMessageBox stacks correctly on its own.
- No _schedule_reassert() port (tour.py's own recurring "-topmost"
  re-assertion timer) - that method's own comment says outright it's
  Linux-only ("if platform.system() != 'Linux': return") and actively
  wrong on Windows, where a one-time raise is already enough. Since
  this port's callout uses Qt's WindowStaysOnTopHint (which Windows
  keeps honoring once set, per that same comment), the recurring timer
  has nothing to compensate for here.
- pause_tracking()/resume_tracking() aren't ported either - they exist
  in tour.py purely to avoid update_idletasks()'s synchronous,
  application-wide layout flush on every single geometry() step of a
  panel glide. Qt's mapToGlobal()/mapFromGlobal() (what reposition()
  uses here) are cheap coordinate lookups against already-computed
  layout, not a flush - there's no equivalent cost to avoid.
"""
import platform

from PySide6.QtCore import Qt, QRect, QPoint, QEvent, QObject, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QDialogButtonBox, QMessageBox, QApplication,
)

from tour import (
    tour_state, mark_tour_started, mark_tour_completed, tour_progress_index,
)

_HIGHLIGHT_COLOR = "#0e92ab"
_CALLOUT_BG = "#eaf6f8"
_BORDER_THICKNESS = 3
_DIALOG_HALF_HEIGHT = 90


class _PointAtOnly:
    """Wraps a target for a step where the widget itself should still get
    the usual highlight box, but place_near()'s own "is there room
    around the widget" check can't be trusted for it (an ephemeral
    native dropdown list, say - not a real sibling widget Qt's layout
    system knows about). Falls back to place_outside_container()
    instead - matches tour.py's own _PointAtOnly exactly."""
    def __init__(self, widget):
        self.widget = widget


class _HighlightWholeDialog:
    """Wraps a target for a step where the callout should still point
    near the widget as usual, but the highlight BOX should trace the
    whole dialog's border instead of tightly hugging just that field -
    matches tour.py's own _HighlightWholeDialog exactly."""
    def __init__(self, widget):
        self.widget = widget


class _TreeRegion:
    """Adapts one or more QTreeWidgetItems to a single rect, so a tour
    step can point at the specific row(s) that actually matter instead
    of the whole (often much taller) tree widget - matches tour.py's
    own _TreeRegion, minus the TclError dance: Qt's visualItemRect()
    just returns an empty QRect for a stale/gone item instead of
    raising, so there's nothing extra to guard against here."""
    def __init__(self, tree, items):
        self.tree = tree
        self.items = [i for i in (items or []) if i is not None]

    def _local_rect(self):
        rects = [self.tree.visualItemRect(i) for i in self.items]
        rects = [r for r in rects if not r.isEmpty()]
        if not rects:
            # Nothing to bound - a row scrolled out of view, or none of
            # the items exist anymore (a resumed step referencing a row
            # from a previous process) - fall back to the tree's own
            # full bounds rather than a degenerate empty rect.
            return self.tree.viewport().rect()
        x0 = min(r.left() for r in rects)
        y0 = min(r.top() for r in rects)
        x1 = max(r.right() for r in rects)
        y1 = max(r.bottom() for r in rects)
        return QRect(x0, y0, x1 - x0, y1 - y0)

    def global_rect(self):
        r = self._local_rect()
        top_left = self.tree.viewport().mapToGlobal(r.topLeft())
        return QRect(top_left, r.size())


def _global_rect_of(target):
    """A plain QWidget or a _TreeRegion - both this module's own overlay
    classes only ever need a single global-coordinate QRect out of
    whatever they're pointing at."""
    if isinstance(target, _TreeRegion):
        return target.global_rect()
    return QRect(target.mapToGlobal(QPoint(0, 0)), target.size())


class _HighlightBox(QWidget):
    """A thin rectangle outline around a widget/region - a real CHILD of
    the target window (place()'d in Tk terms; setGeometry()'d here),
    not a separate top-level window, so it moves/raises/minimizes with
    that window automatically. Matches tour.py's own _HighlightBox,
    collapsed from four separate bar widgets into one widget with a
    QSS border - Qt's stylesheet border draws the same four-sided
    outline in one paint pass, no need to fake it with four rectangles."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # A plain QWidget doesn't paint its stylesheet's background/
        # border at all by default (unlike QFrame) - WA_StyledBackground
        # is what actually routes painting through the stylesheet engine
        # instead of a no-op default paintEvent. Confirmed live: without
        # this, the border was completely invisible even though
        # place_around()'s own geometry math was correct.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background: transparent; border: {_BORDER_THICKNESS}px solid {_HIGHLIGHT_COLOR};")

    def place_around(self, target):
        pad = 4
        g = _global_rect_of(target)
        top_left = self.parentWidget().mapFromGlobal(g.topLeft())
        self.setGeometry(top_left.x() - pad, top_left.y() - pad, g.width() + pad * 2, g.height() + pad * 2)
        self.show()
        self.raise_()

    def place_around_container(self):
        inset = 3
        r = self.parentWidget().rect()
        self.setGeometry(inset, inset, max(0, r.width() - inset * 2), max(0, r.height() - inset * 2))
        self.show()
        self.raise_()


class _Callout(QWidget):
    """A borderless bubble with a title, body text, and a single Skip/
    Close button - matches tour.py's own _Callout. A real top-level
    window in global screen coordinates (not a child of whatever it's
    pointing into), for the same reason tour.py's own docstring gives:
    a place()/layout-clipped child can't extend past its parent's own
    bounds, so once a step points into a window SHORTER than the
    callout itself, there's no position inside that parent where it
    fits without covering the very field it explains.

    owner is given as this window's real Qt/OS parent (not just held as
    a Python reference) - matches Tk's transient(parent_window): it
    keeps the callout stacked above that ONE specific window, the same
    thing every step's own _bring_to_front() call already re-asserts,
    without WindowStaysOnTopHint's global "above literally everything,
    including unrelated windows" effect. That global version was tried
    first and confirmed live to be wrong for this app specifically -
    the "Folder" step's callout stayed pinned on top of the NATIVE
    folder-browser dialog Browse opens, a real system window with no
    relationship to the tour at all, rather than yielding to it while
    the user was actively using it.

    No Next/Back buttons here either, matching tour.py's identical
    reasoning - each step advances itself once its real action actually
    happens (see GuiTourQt.on_event()), not paced by clicks through a
    narrated slideshow."""

    def __init__(self, owner, title, text, on_skip, skip_label="Skip Tour"):
        super().__init__(
            owner.window() if owner is not None else None,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
        )
        # Never steals keyboard focus from whatever field the user is
        # actively typing into (the share name, a password, ...) - same
        # concern tour.py's own _bring_to_front(steal_focus=False) call
        # sites are guarding against, just structural here instead of a
        # per-call flag, since this window never needs real focus at all
        # (its one button is clickable without it).
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(f"background: {_HIGHLIGHT_COLOR};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        inner = QWidget()
        inner.setStyleSheet(f"background: {_CALLOUT_BG};")
        outer.addWidget(inner)

        inner_layout = QVBoxLayout(inner)
        title_label = QLabel(title)
        title_label.setFont(QFont(title_label.font().family(), 11, QFont.Weight.Bold))
        inner_layout.addWidget(title_label)

        self.text_label = QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setFixedWidth(260)
        inner_layout.addWidget(self.text_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        skip_btn = QPushButton(skip_label)
        skip_btn.clicked.connect(on_skip)
        btn_row.addWidget(skip_btn)
        inner_layout.addLayout(btn_row)

    def set_text(self, text):
        self.text_label.setText(text)

    def _measure(self):
        self.adjustSize()
        return self.sizeHint().width(), self.sizeHint().height()

    def place_near(self, target):
        w, h = self._measure()
        screen = QApplication.primaryScreen().geometry()
        screen_w, screen_h = screen.width(), screen.height()

        g = _global_rect_of(target)
        wx, wy, ww, wh = g.x(), g.y(), g.width(), g.height()

        container = target.tree.window() if isinstance(target, _TreeRegion) else target.window()
        cg = QRect(container.mapToGlobal(QPoint(0, 0)), container.size())
        cx, cy = cg.x(), cg.y()
        cbottom = cy + cg.height()

        margin = 14
        below_widget_y = wy + wh + margin
        above_widget_y = wy - margin - h

        def _sibling_in_rect(y0, y1):
            # A real sibling QWidget in the way of this candidate spot -
            # matches tour.py's own _sibling_in_rect(): a stacked form's
            # OTHER fields, or a toolbar's other buttons, not just
            # "close to the container's own edge" (which false-positived
            # on widgets near the top of a tall window with nothing
            # actually beneath them - see tour.py's own comment).
            if isinstance(target, _TreeRegion) or target.parentWidget() is None:
                return False
            parent = target.parentWidget()
            for sib in parent.findChildren(QWidget):
                if sib is target or sib.parentWidget() is not parent or not sib.isVisible():
                    continue
                sg = QRect(sib.mapToGlobal(QPoint(0, 0)), sib.size())
                if sg.left() < wx + ww and sg.left() + sg.width() > wx and sg.top() < y1 and sg.top() + sg.height() > y0:
                    return True
            return False

        if below_widget_y + h <= min(cbottom, screen_h) and not _sibling_in_rect(below_widget_y, below_widget_y + h):
            x, y = wx, below_widget_y
        elif above_widget_y >= max(cy, 0) and not _sibling_in_rect(above_widget_y, above_widget_y + h):
            x, y = wx, above_widget_y
        else:
            below_container_y = cbottom + margin
            y = below_container_y if below_container_y + h <= screen_h else max(cy - margin - h, 0)
            x = cx

        x = max(0, min(x, screen_w - w))
        y = max(0, min(y, screen_h - h))
        self.move(x, y)
        self.raise_()

    def place_outside_container(self, container: QWidget, estimated_dialog_half_height=90):
        w, h = self._measure()
        screen = QApplication.primaryScreen().geometry()
        screen_w, screen_h = screen.width(), screen.height()

        cg = QRect(container.mapToGlobal(QPoint(0, 0)), container.size())
        cw, ch = cg.width(), cg.height()
        cx, cy = cg.x(), cg.y()

        margin = 24
        below_y = cy + ch // 2 + estimated_dialog_half_height + margin
        y = below_y if below_y + h <= screen_h else max(cy - margin - h, 0)
        x = cx + (cw - w) // 2
        x = max(0, min(x, screen_w - w))
        y = max(0, min(y, screen_h - h))
        self.move(x, y)
        self.raise_()


class _ContainerTracker(QObject):
    """Keeps a step's highlight/callout glued to their container's
    current position/size - matches tour.py's own _track_container(),
    minus the pause/resume machinery (see this module's own docstring
    for why that doesn't carry over). Installed as an event filter on
    the container rather than overriding resizeEvent/moveEvent, since
    the container is an existing widget (MainWindow or a dialog) this
    module doesn't own the class of."""

    def __init__(self, container: QWidget, reposition_fn):
        super().__init__(container)
        self._reposition_fn = reposition_fn
        self._pending = False
        container.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self._schedule()
        return False

    def _schedule(self):
        # Coalesces a burst of Move/Resize events (a panel glide fires
        # one every animation frame) into a single reposition per burst,
        # matching tour.py's own after_idle deferral.
        if self._pending:
            return
        self._pending = True

        def _run():
            self._pending = False
            self._reposition_fn()

        QTimer.singleShot(0, _run)


class GuiTourQt:
    """PySide6 port of tour.py's GuiTour - see that class's own
    docstring for the overall design (event-driven advancement, follows
    the user into whatever dialog just opened). gui is the MainWindow
    instance; _build_steps() below is what actually encodes the tour's
    content and has to be kept in sync with MainWindow's own widget
    structure by hand, the same way tour.py's _build_steps() is kept in
    sync with gui.py's."""

    def __init__(self, gui):
        self.gui = gui
        self.steps = self._build_steps()
        self.index = 0
        self._highlight = None
        self._callout = None
        self._wait_event = None
        self._tracker = None
        self._reposition = None
        self._active_window = None

    def _newest_share_region(self):
        tree = self.gui.shares_tree
        count = tree.topLevelItemCount()
        for i in range(count - 1, -1, -1):
            item = tree.topLevelItem(i)
            if item is not self.gui._add_share_row:
                children = [item.child(j) for j in range(item.childCount())]
                return _TreeRegion(tree, [item] + children)
        return _TreeRegion(tree, [])

    def _newest_user_row(self):
        tree = self.gui.shares_tree
        count = tree.topLevelItemCount()
        for i in range(count - 1, -1, -1):
            item = tree.topLevelItem(i)
            if item is not self.gui._add_share_row and item.childCount() > 0:
                return _TreeRegion(tree, [item.child(item.childCount() - 1)])
        return _TreeRegion(tree, [])

    def _row_action_button(self, index):
        # The Nth button in the shares tree's CURRENT dynamic row action
        # bar, in left-to-right order - matches tour.py's own
        # gui._share_action_bar.bar.winfo_children()[N]. Safe to index
        # positionally for the same reason that comment gives: it's
        # rebuilt from scratch in a fixed order every time a row is
        # selected (see _RowActionBar/_build_share_row_actions).
        bar = self.gui._share_action_bar
        if index >= bar._layout.count():
            return None
        item = bar._layout.itemAt(index)
        return item.widget() if item else None

    def _build_steps(self):
        # (container_fn, widget_fn, title, text, wait_event) - see
        # tour.py's own _build_steps() for the full reasoning behind
        # each step; this list mirrors it 1:1, just against gui_qt.py's
        # own widget names instead of gui.py's.
        gui = self.gui
        return [
            (lambda: gui, lambda: _TreeRegion(gui.shares_tree, [gui._add_share_row]),
             "New Share", "Click here to create your first share.",
             "share_dialog_opened"),
            (lambda: self._active_window, lambda: _HighlightWholeDialog(self._active_window.name_entry),
             "Share Name", "Type a share name and click Next.",
             "share_name_confirmed"),
            (lambda: self._active_window, lambda: _HighlightWholeDialog(self._active_window.path_entry),
             "Folder",
             "Pick a folder for this share or use the provided default, then click Create Share.",
             "share_created"),

            (lambda: gui, lambda: gui.users_btn,
             "Manage Users", "Open Manage Users to create and delete user accounts.",
             "user_mgmt_opened"),
            (lambda: gui,
             lambda: _TreeRegion(gui.user_mgmt_panel.tree, [gui.user_mgmt_panel._add_user_row]),
             "New User", "Click New User to create a new share user.",
             "user_dialog_opened"),
            (lambda: self._active_window, lambda: _HighlightWholeDialog(self._active_window.username_entry),
             "Username and Password", "Type a username and password, then click OK.",
             "user_created"),
            (lambda: gui, lambda: gui.users_btn,
             "Close", "Click the Users button again to close this panel and return to the shares list.",
             "user_mgmt_closed"),

            (lambda: gui, lambda: self._newest_share_region(),
             "Shares", "Select your share to reveal its actions.",
             "share_selected"),
            (lambda: gui, lambda: self._row_action_button(0),
             "New User",
             "Click to open the New User dialog.",
             "user_dialog_opened"),
            (lambda: self._active_window, lambda: _PointAtOnly(self._active_window.cancel_button),
             "New User",
             "Users added this way are automatically attached to the selected share. "
             "Press the Cancel button.",
             "user_dialog_cancelled"),
            (lambda: gui, lambda: self._row_action_button(1),
             "Attach User",
             "Press Attach User.",
             "attach_dropdown_opened"),
            (lambda: gui, lambda: _PointAtOnly(self._row_action_button(0)),
             "Attach User",
             "The dropdown shows all available users to attach to the selected share, "
             "including ones not made by NASsie (indicated by \"existing account\"). Select "
             "the user that you just created.",
             "user_attached"),
            (lambda: gui, lambda: self._row_action_button(2),
             "Delete Share",
             "Press the Delete share button.",
             "share_delete_dialog_opened"),
            (lambda: gui, None,
             "Delete Share", "Press No to return to the main window. Your share will NOT be deleted.",
             "share_delete_dialog_cancelled"),

            (lambda: gui, lambda: self._newest_user_row(),
             "Select User", "Select the user you just attached to reveal its own actions.",
             "share_selected"),
            (lambda: gui, lambda: self._row_action_button(0),
             "Permission",
             "Toggles this user between read-only and read-write - "
             "click it twice to see both and land back on the value you started with.",
             "access_level_changed"),
            (lambda: gui, lambda: self._row_action_button(1),
             "QR Code",
             "Click the QR Code button.",
             "qr_dialog_opened"),
            (lambda: self._active_window, lambda: _PointAtOnly(self._active_window.cancel_button),
             "QR Code",
             "Enter the user password to generate a QR code. Click Cancel for now.",
             "qr_prompt_cancelled"),
            (lambda: gui, lambda: self._row_action_button(2),
             "Detach",
             lambda: (
                 f"This removes {gui._selected_share_and_user()[1]}'s access to "
                 f"{gui._selected_share_and_user()[0]}. Click Detach to view the action."
             ),
             "user_detach_dialog_opened"),
            (lambda: gui, None,
             "Detach", "Press No to return to the main window. This user will NOT be detached.",
             "user_detach_dialog_cancelled"),
        ]

    def _step_resolves(self, index, check_widget=False):
        try:
            container = self.steps[index][0]()
        except (RuntimeError, AttributeError):
            return False
        if container is None:
            return False
        try:
            # A destroyed QWidget's Python wrapper raises RuntimeError on
            # nearly any attribute access - the Qt-side equivalent of
            # tour.py's own winfo_exists() check.
            container.isVisible()
        except RuntimeError:
            return False
        if not check_widget:
            return True
        widget_fn = self.steps[index][1]
        if widget_fn is not None:
            try:
                widget = widget_fn()
                if widget is not None:
                    target = widget.widget if isinstance(widget, (_PointAtOnly, _HighlightWholeDialog)) else widget
                    if target is not None:
                        _global_rect_of(target)
            except (RuntimeError, AttributeError, IndexError):
                return False
        return True

    def _resolve_resume_index(self, index):
        index = max(0, min(index, len(self.steps) - 1))
        while index > 0 and not self._step_resolves(index, check_widget=True):
            index -= 1
        return index

    def start(self, resume_index=0):
        self.index = self._resolve_resume_index(resume_index)
        mark_tour_started(self.index)
        self._show_step()

    def _show_step(self):
        self._teardown_current()
        mark_tour_started(self.index)
        container_fn, widget_fn, title, text, wait_event = self.steps[self.index]
        if callable(text):
            text = text()
        container = container_fn()
        if container is None or not self._step_resolves(self.index):
            self.stop(completed=False)
            return
        container.raise_()
        container.activateWindow()
        try:
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
                self._callout.place_outside_container(container, _DIALOG_HALF_HEIGHT)
            self._callout.show()
        except (RuntimeError, AttributeError, IndexError):
            self.stop(completed=False)
            return
        self._wait_event = wait_event
        self._track_container(container, widget)

    def _track_container(self, container, widget):
        point_at_only = isinstance(widget, _PointAtOnly)
        whole_dialog_highlight = isinstance(widget, _HighlightWholeDialog)
        highlight_target = widget.widget if (point_at_only or whole_dialog_highlight) else widget

        def reposition():
            if self._callout is None:
                return
            try:
                if whole_dialog_highlight:
                    self._highlight.place_around_container()
                elif highlight_target is not None:
                    self._highlight.place_around(highlight_target)
                if highlight_target is not None and not point_at_only:
                    self._callout.place_near(highlight_target)
                else:
                    self._callout.place_outside_container(container, _DIALOG_HALF_HEIGHT)
            except RuntimeError:
                pass

        # Exposed as self._reposition so refresh_position() (see its own
        # docstring) can trigger this same closure on demand, not just
        # in response to a Move/Resize event on the container.
        self._reposition = reposition
        self._tracker = _ContainerTracker(container, reposition)

    def refresh_position(self):
        # Called by MainWindow._animate_panel() once a Log/Users panel
        # glide actually finishes - belt-and-suspenders alongside
        # _ContainerTracker's own passive Move/Resize event-filter
        # tracking (which should already keep up DURING the glide,
        # since each animation frame's setGeometry() fires a real
        # QResizeEvent on the container). This guarantees the END state
        # is correct regardless: the glide's LAST frame's event and the
        # child layout (header buttons, row action bars, ...) actually
        # settling into their final geometry aren't provably the same
        # event-loop tick - reported live as the highlight box ending
        # up visibly off from its target after a panel toggle finished,
        # not just lagging mid-glide. A no-op whenever no step is
        # currently showing (self._callout is None) or the container/
        # target no longer resolves (already-closed dialog, etc.) -
        # reposition() itself guards both.
        if self._tracker is not None:
            self._reposition()

    def on_event(self, event, window=None):
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
        if self._callout is None or self._wait_event != "share_name_confirmed":
            return
        self._callout.set_text(f"That name didn't work — {message} Fix it and click Next again.")
        widget_fn = self.steps[self.index][1]
        try:
            self._callout.place_near(widget_fn())
        except RuntimeError:
            pass

    def _show_finish(self):
        mark_tour_completed()
        self._teardown_current()
        widget = self._newest_share_region()
        gui = self.gui
        gui.raise_()
        gui.activateWindow()
        self._highlight = _HighlightBox(gui)
        self._highlight.place_around(widget)
        self._callout = _Callout(
            gui,
            "You're all set!",
            "You now have a live SMB share with an attached user.",
            on_skip=self.stop, skip_label="Close",
        )
        self._callout.place_near(widget)
        self._callout.show()
        self._wait_event = None
        self._track_container(gui, widget)

    def _confirm_and_stop(self):
        # Plain QMessageBox instead of tour.py's own hand-rolled
        # _ConfirmDialog - see this module's own docstring for why that
        # class's whole reason for existing doesn't apply on Qt/Windows.
        self.gui.raise_()
        self.gui.activateWindow()
        result = QMessageBox.question(
            self.gui, "Skip Tour", "Skip the rest of the guided tour?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.stop()

    def stop(self, completed=True):
        if completed:
            mark_tour_completed()
        self._teardown_current()

    def _teardown_current(self):
        if self._tracker is not None:
            try:
                self._tracker.deleteLater()
            except RuntimeError:
                pass
            self._tracker = None
        if self._highlight is not None:
            try:
                self._highlight.deleteLater()
            except RuntimeError:
                pass
            self._highlight = None
        if self._callout is not None:
            try:
                self._callout.deleteLater()
            except RuntimeError:
                pass
            self._callout = None
