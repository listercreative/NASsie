"""PySide6 (Qt) rewrite of the GUI - in progress, not the shipping default.
See the migration plan (C:\\Users\\caden\\.claude\\plans\\pure-leaping-lamport.md)
for full context and phasing. Launched via `nassie --gui-qt`, entirely
separate from gui.py's Tk GUIWizard - the two share no runtime state and
are never active in the same process.

Scope landed so far: app shell (Phase 1), the shares list with a real
sortable tree and per-share action bar, the Users/Log panels with a real
QPropertyAnimation-driven toggle (the actual point of this migration -
see the plan's Phase 0 proof-of-concept for why), and the core dialogs
(Phase 2/3). NOT yet ported: the guided tour (Phase 4 - flagged as the
highest-risk piece, deliberately deferred rather than rushed), the full
dark-mode/QSS theme (Phase 5 - only a base light palette exists so far),
and Qt-specific packaging (Phase 6).
"""
from __future__ import annotations

import contextlib
import ctypes
import io
import os
import platform
import sys

from PySide6.QtCore import (
    Qt, QSize, QRect, QPropertyAnimation, QEasingCurve, QThread, Signal, QTimer, QObject, QEvent,
    QRegularExpression,
)
from PySide6.QtGui import QIcon, QPalette, QColor, QPixmap, QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QToolButton, QPushButton, QFrame, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QMessageBox,
    QPlainTextEdit, QFileDialog, QStackedWidget, QSizePolicy,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import SMBWizard, QR_PASSWORD_RESET_NOTE, pick_directory_native, SHARE_NAME_MAX_LEN, SHARE_NAME_RE

# ---------------------------------------------------------------------------
# Constants - queried/measured live from this machine's real Tk app rather
# than guessed, per the user's own explicit direction on Phase 1.
# ---------------------------------------------------------------------------
_ICON_SIZE = 24
_TOGGLE_BUTTON_SIZE = 34
_DEFAULT_FONT_FAMILY = "Segoe UI"
_DEFAULT_FONT_SIZE = 9
_LOGO_SIZE = 64
_BASE_WIDTH = 750
_BASE_HEIGHT = 600
_PANEL_WIDTH = 280
_GLIDE_MS = 220

# Exact values from nassie_ttk/theme/light.tcl's own `colors` array and
# gui.py's own hardcoded constants - read directly from source, not
# eyeballed off a screenshot (screenshots are for layout/spacing
# reference only - see the migration plan's Phase 5 notes).
_COLORS = {
    "fg": "#1c1c1c",
    "bg": "#fafafa",
    "disfg": "#a0a0a0",
    "selfg": "#ffffff",
    "selbg": "#0e92ab",
    "accent": "#0e92ab",
    # Treeview row selection is deliberately the LOGO's green, not the
    # teal accent - light.tcl's own comment explains why: a teal
    # selection read as indistinguishable from the add-row/stripe tint,
    # which is already that same accent color elsewhere in the app.
    "tree_selbg": "#72af52",
    "tree_selfg": "#ffffff",
    # gui.py's own _ADD_ROW_BG/_STRIPE_BG constants (the pinned "+" row's
    # tint, and the alternating-row stripe) - not part of the ttk theme
    # package at all, applied directly via tree.tag_configure().
    "add_row_bg": "#eaf6f8",
    "stripe_bg": "#f2f2f2",
    # _ToggleButton._PRESSED_BG - the toolbar toggle's own "stays
    # pressed while its panel is open" fill color.
    "toggle_pressed_bg": "#37474f",
    "border": "#d0d0d0",
}
# gui.py's own explicit override (ttk.Style(...).configure("Treeview",
# rowheight=36)) - takes priority over light.tcl's own font-metric-
# derived default, so 36 is the real, final value to match.
_TREE_ROW_HEIGHT = 36


def _build_stylesheet() -> str:
    c = _COLORS
    return f"""
        QMainWindow, QWidget {{ background: {c['bg']}; color: {c['fg']}; }}
        QTreeWidget {{
            background: #ffffff;
            border: 1px solid {c['border']};
            outline: none;
        }}
        QTreeWidget::item {{ height: {_TREE_ROW_HEIGHT}px; outline: none; }}
        QTreeWidget::item:selected {{ background: {c['tree_selbg']}; color: {c['tree_selfg']}; outline: none; border: none; }}
        QTreeWidget::item:focus {{ outline: none; border: none; }}
        QHeaderView::section {{
            background: #ffffff;
            border: none;
            border-bottom: 1px solid {c['border']};
            border-right: 1px solid {c['border']};
            padding: 4px 6px;
        }}
        QToolButton {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #ececec);
            border: 1px solid {c['border']};
            border-radius: 4px;
        }}
        QToolButton:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f7f7f7, stop:1 #e4e4e4);
        }}
        QToolButton:pressed {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dcdcdc, stop:1 #eeeeee);
            border-color: #a8a8a8;
        }}
        QToolButton:checked {{ background: {c['toggle_pressed_bg']}; border-color: {c['toggle_pressed_bg']}; }}
        QPushButton {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #ececec);
            border: 1px solid {c['border']};
            border-radius: 4px;
            padding: 6px 10px;
        }}
        QPushButton:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f7f7f7, stop:1 #e4e4e4);
        }}
        QPushButton:pressed {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #dcdcdc, stop:1 #eeeeee);
            border-color: #a8a8a8;
        }}
        QLineEdit, QComboBox, QPlainTextEdit {{
            background: #ffffff;
            border: 1px solid {c['border']};
            border-radius: 3px;
            padding: 3px;
        }}
    """


def _restripe_tree(tree: "QTreeWidget", pinned_item=None):
    # Matches gui.py's _SortableTree._restripe(): two INDEPENDENT
    # alternations, not one continuous count - top-level rows (shares, or
    # accounts in the Users panel) alternate among EACH OTHER only (the
    # pinned add-row is skipped, not counted), tinted with the add-row's
    # own teal; each top-level item's own children (a share's attached
    # users) alternate separately among THEMSELVES with a plain gray,
    # restarting the count fresh per parent.
    top_tint = QColor(_COLORS["add_row_bg"])
    child_tint = QColor(_COLORS["stripe_bg"])
    white = QColor("#ffffff")
    top_index = 0
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        if item is pinned_item:
            continue
        bg = top_tint if top_index % 2 == 1 else white
        for col in range(tree.columnCount()):
            item.setBackground(col, bg)
        top_index += 1
        for j in range(item.childCount()):
            child = item.child(j)
            cbg = child_tint if j % 2 == 0 else white
            for col in range(tree.columnCount()):
                child.setBackground(col, cbg)


def _make_add_row() -> "QTreeWidgetItem":
    # A FRESH item every call, never reused across repopulates -
    # QTreeWidget.clear() deletes the underlying C++ object of every
    # item it owns, so re-adding a previously-cleared instance (as an
    # earlier version of this did, keeping one add-row item alive on
    # self across refreshes) throws "Internal C++ object already
    # deleted" the moment a SECOND refresh runs - which happens on
    # every panel open (refresh_all() at startup, then again from
    # _toggle_panel()) - and silently blanks the whole tree, since Qt
    # prints the exception from the slot instead of raising it further.
    # Blank text - _AddRowOverlay below is what actually shows the "+"
    # (the real icon_add.png asset, not a text glyph), matching gui.py's
    # own add-row exactly. This item's own background is still set as a
    # one-frame fallback (see gui.py's identical "add_row" tag comment)
    # - the overlay fully covers it once attached.
    item = QTreeWidgetItem([""])
    item.setBackground(0, QColor(_COLORS["add_row_bg"]))
    return item


class _AddRowOverlay(QLabel):
    """Floating overlay for the pinned add-row - mirrors gui.py's own
    _AddRowFeedback exactly: a real widget on top of the row, not
    reliant on the tree's per-COLUMN cell geometry. setIndexWidget()
    (an earlier version of this) sizes to the model index's own rect,
    which excludes the branch/expand-arrow indent Qt reserves for every
    top-level row whenever ANY item in the tree has children (true here
    - a share's attached users nest under it) - confirmed live, it left
    an unshaded gap along the row's own left edge even though the
    item's own background (painted separately, not through the index
    widget) was already genuinely full-width. visualItemRect() instead
    gives the row's TRUE full bounding rect, matching Tk's own
    tree.bbox(item) with no column argument - see that class's own
    docstring for why that specifically matters on a multi-column row.

    Also owns hover/press feedback (idle/hover/pressed tints) and the
    click itself directly, same as Tk's version and for the same reason
    - a bare tree row gives no cursor change or visible click response
    of its own, and there is no way to tell it's a button otherwise.
    """
    _HOVER_BG = "#d7eef2"
    _PRESSED_BG = "#c2e6ec"

    def __init__(self, tree: "QTreeWidget", item: "QTreeWidgetItem", on_click):
        super().__init__(tree.viewport())
        self._tree = tree
        self._item = item
        self._on_click = on_click
        self._idle_bg = _COLORS["add_row_bg"]
        self._pressed = False
        icon = _icon("icon_add")
        if not icon.isNull():
            self.setPixmap(icon.pixmap(_ICON_SIZE, _ICON_SIZE))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paint(self._idle_bg)

    def _paint(self, color):
        self.setStyleSheet(f"background-color: {color};")

    def reposition(self):
        rect = self._tree.visualItemRect(self._item)
        if rect.isEmpty():
            self.hide()
            return
        # visualItemRect() still bounds its WIDTH to column 0's own
        # span (root-decoration/indent excluded, but not the "Path"
        # column past it) even on a first-column-spanned item - forcing
        # left/right to the viewport's own full width is what actually
        # gets the "+" centered across the WHOLE row, matching Tk's own
        # tree.bbox(item) (no column argument = every visible column
        # combined - see this class's docstring).
        rect.setLeft(0)
        rect.setRight(self._tree.viewport().width())
        self.setGeometry(rect)
        self.show()
        self.raise_()

    def enterEvent(self, event):
        if not self._pressed:
            self._paint(self._HOVER_BG)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self._pressed:
            self._paint(self._idle_bg)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._pressed = True
        self._paint(self._PRESSED_BG)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        inside = self.rect().contains(event.position().toPoint())
        self._paint(self._HOVER_BG if inside else self._idle_bg)
        if inside and self._on_click:
            self._on_click()


def _attach_add_row_icon(tree: "QTreeWidget", item: "QTreeWidgetItem", on_click) -> "_AddRowOverlay":
    overlay = _AddRowOverlay(tree, item, on_click)
    # Keeps the overlay glued to the row's current bbox across whatever
    # can move or resize it - matches gui.py's own reasoning for binding
    # _AddRowFeedback.reposition() to <Configure>/<MouseWheel> (scrolling
    # or resizing a row without repositioning this leaves it floating
    # over the WRONG row, or off past the tree's edge entirely).
    tree.header().sectionResized.connect(lambda *a: overlay.reposition())
    tree.verticalScrollBar().valueChanged.connect(lambda *a: overlay.reposition())
    if tree.horizontalScrollBar() is not None:
        tree.horizontalScrollBar().valueChanged.connect(lambda *a: overlay.reposition())
    # Deferred - right after insertion the view hasn't necessarily
    # finished laying out yet (same reasoning as gui.py's own
    # after_idle() deferral there), so an immediate reposition() call
    # here could still see a stale/empty rect.
    QTimer.singleShot(0, overlay.reposition)
    return overlay


class _BlankClickDeselecter(QObject):
    """Clears a tree's selection on a click that lands on its own empty
    background (below the last row, or any area with no item under the
    cursor) - matches gui.py's own _deselect_on_blank_click(): ttk's
    Treeview (and, it turns out, QTreeWidget here too) only ever acts
    on a click when a row IS under the cursor, so without this, the
    previously-selected row (and its own floating _RowActionBar) just
    stayed selected/highlighted indefinitely after clicking away from
    it - reported live, for both the shares list and the Users panel.
    """

    def __init__(self, tree: "QTreeWidget"):
        super().__init__(tree)
        self.tree = tree
        tree.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress and self.tree.itemAt(event.position().toPoint()) is None:
            self.tree.clearSelection()
        return False


def _row_action_button(icon_name: str, tooltip: str, handler) -> "QToolButton":
    btn = QToolButton()
    btn.setIcon(_icon(icon_name))
    btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
    btn.setToolTip(tooltip)
    btn.clicked.connect(handler)
    return btn


class _RowActionBar(QWidget):
    """Floating, row-anchored action buttons - mirrors gui.py's own
    _RowActionBar exactly: not a persistent toolbar anywhere, just a
    small strip of icon buttons that appears over the RIGHT side of
    whichever row is currently selected, rebuilt via
    build_fn(container_layout, item) -> bool every time the selection
    changes (build_fn clears/populates the layout with whatever's
    relevant to THAT specific row - a share row's actions differ from
    one of its attached-user rows, and the add-row/an in-progress
    attach both return False to keep the bar hidden). No separate
    static action row exists anywhere else - see MainWindow's own
    _build_shares_page() and UserManagementPanel's identical use of
    this class for the two places it's wired up.
    """

    def __init__(self, tree: "QTreeWidget", build_fn):
        super().__init__(tree.viewport())
        self.tree = tree
        self.build_fn = build_fn
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(1)
        self.hide()
        tree.itemSelectionChanged.connect(self.update_bar)
        # Reposition only (no rebuild) on scroll/column-resize - matches
        # gui.py's own reasoning: neither changes WHICH row is selected
        # or what build_fn returns for it, only where the already-built
        # bar needs to sit.
        tree.header().sectionResized.connect(lambda *a: self.reposition())
        tree.verticalScrollBar().valueChanged.connect(lambda *a: self.reposition())
        if tree.horizontalScrollBar() is not None:
            tree.horizontalScrollBar().valueChanged.connect(lambda *a: self.reposition())

    def _clear(self):
        while self._layout.count():
            child = self._layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def update_bar(self):
        self._clear()
        items = self.tree.selectedItems()
        if not items or not self.build_fn(self._layout, items[0]):
            self.hide()
            return
        self.reposition()

    def reposition(self):
        if self._layout.count() == 0:
            return
        items = self.tree.selectedItems()
        if not items:
            self.hide()
            return
        rect = self.tree.visualItemRect(items[0])
        if rect.isEmpty():
            self.hide()
            return
        bar_w = self.sizeHint().width()
        viewport_w = self.tree.viewport().width()
        x = max(0, viewport_w - bar_w)
        self.setGeometry(x, rect.top(), bar_w, rect.height())
        self.show()
        self.raise_()


class _TreeSorter:
    """Click-to-sort on a QTreeWidget's header, matching gui.py's own
    _SortableTree: only top-level rows reorder (a share's own attached-
    user children keep their fetch order, untouched by the shares
    heading), and the pinned add-row always stays pinned at index 0
    regardless of sort column/direction - see that class's own
    pinned_first/_repin(). Deliberately NOT QTreeWidget's built-in
    setSortingEnabled(): that auto-resorts on every insert, which would
    intermix the pinned row with real data by text order instead of
    leaving it fixed. Native sort-arrow indicator is still shown via
    QHeaderView.setSortIndicator(), independent of that auto-sort
    mechanism.
    """

    def __init__(self, tree: "QTreeWidget", pinned_getter):
        self.tree = tree
        self.pinned_getter = pinned_getter
        self.col = None
        self.reverse = False
        header = tree.header()
        header.setSortIndicatorShown(True)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_click)

    def _on_click(self, col):
        if self.col == col:
            self.reverse = not self.reverse
        else:
            self.col = col
            self.reverse = False
        self.apply()

    def apply(self):
        pinned = self.pinned_getter()
        if self.col is not None:
            order = Qt.SortOrder.DescendingOrder if self.reverse else Qt.SortOrder.AscendingOrder
            self.tree.header().setSortIndicator(self.col, order)
            top_items = [
                self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(i) is not pinned
            ]
            for item in top_items:
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
            top_items.sort(key=lambda it: it.text(self.col).lower(), reverse=self.reverse)
            for item in top_items:
                self.tree.addTopLevelItem(item)
        if pinned is not None:
            idx = self.tree.indexOfTopLevelItem(pinned)
            if idx > 0:
                self.tree.takeTopLevelItem(idx)
                self.tree.insertTopLevelItem(0, pinned)
        _restripe_tree(self.tree, pinned)


def _asset_base_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _icon(name: str) -> QIcon:
    path = os.path.join(_asset_base_dir(), "icons", f"{name}.png")
    return QIcon(path) if os.path.exists(path) else QIcon()


def _apply_base_palette(app: QApplication):
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(_COLORS["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(_COLORS["fg"]))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Text, QColor(_COLORS["fg"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(_COLORS["selbg"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(_COLORS["selfg"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(_COLORS["disfg"]))
    app.setPalette(palette)


def _round_window_corners(window: QWidget):
    # Confirmed in the migration plan: not Tk-specific, operates on a raw
    # HWND. Ports by swapping just the HWND-resolution helper to
    # QWidget.winId() - Qt's top-level widget's winId() IS the real
    # OS-decorated HWND directly, no GetParent() walk-up needed the way
    # Tk required.
    if platform.system() != "Windows":
        return None
    try:
        dwmapi = ctypes.windll.dwmapi
    except (AttributeError, OSError):
        return None
    hwnd = int(window.winId())

    def _round():
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        try:
            dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except OSError:
            pass

    def _suppress_transitions(suppressed):
        value = ctypes.c_int(1 if suppressed else 0)
        try:
            dwmapi.DwmSetWindowAttribute(hwnd, 3, ctypes.byref(value), ctypes.sizeof(value))
            return True
        except OSError:
            return False

    _round()
    return _suppress_transitions


# ---------------------------------------------------------------------------
# Background worker - generic replacement for gui.py's repeating
# threading.Thread + root.after(0, callback) marshal-back pattern (the
# inventory found this ~10+ times across GUIWizard). One QThread subclass,
# parameterized by the callable to run and args, emits `done` with
# (result, log_output) on the Qt main thread automatically - Signal/Slot
# crossing threads is queued-connection by default, no manual marshaling.
# ---------------------------------------------------------------------------
class _Worker(QThread):
    done = Signal(object, str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        buffer = io.StringIO()
        result = None
        try:
            with contextlib.redirect_stdout(buffer):
                result = self._fn(*self._args, **self._kwargs)
        except Exception as e:
            buffer.write(f"\nUnexpected error: {e}\n")
        self.done.emit(result, buffer.getvalue())


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------
class ChoiceDialog(QDialog):
    def __init__(self, parent, title, message, choices):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.result_value = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(message))
        self.combo = QComboBox()
        self.combo.addItems(choices)
        layout.addWidget(self.combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_ok(self):
        self.result_value = self.combo.currentText()
        self.accept()


class PasswordPromptDialog(QDialog):
    def __init__(self, parent, title="Password", message="Enter password:"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.result_value = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(message))
        self.entry = QLineEdit()
        self.entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.entry.returnPressed.connect(self._on_ok)
        layout.addWidget(self.entry)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_ok(self):
        self.result_value = self.entry.text()
        self.accept()


class QrCodeDialog(QDialog):
    def __init__(self, parent, share_name, username, payload):
        super().__init__(parent)
        self.setWindowTitle(f"QR Code - {username}")
        layout = QVBoxLayout(self)
        # Matches gui.py's own QrCodeDialog text exactly, including the
        # wording - not a paraphrase.
        layout.addWidget(QLabel(f"Scan for easy external configuration of '{share_name}' as {username}"))
        try:
            import qrcode
            img = qrcode.make(payload)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            pixmap = QPixmap()
            pixmap.loadFromData(buf.getvalue())
            qr_label = QLabel()
            qr_label.setPixmap(pixmap)
            layout.addWidget(qr_label)
        except Exception as e:
            layout.addWidget(QLabel(f"(QR generation failed: {e})"))
        layout.addWidget(QLabel("Compatible with the LockNAS app's bridge QR scanner."))
        # NOT QR_PASSWORD_RESET_NOTE - that's the pre-change confirmation
        # shown before a password reset (see UserManagementPanel's own
        # _change_password_flow()), a different message from this
        # dialog's own security warning about the QR itself, which
        # gui.py shows here instead.
        warning = QLabel(
            "Contains this user's password in plain sight - don't leave it "
            "on screen or let anyone photograph it who shouldn't have access."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #b00000;")
        warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class AddUserDialog(QDialog):
    def __init__(self, parent, existing_usernames, show_access_level=True):
        super().__init__(parent)
        self.setWindowTitle("New User")
        self.result_data = None
        self._existing = existing_usernames

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.username_entry = QLineEdit()
        self.password_entry = QLineEdit()
        self.password_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_entry = QLineEdit()
        self.confirm_entry.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Username:", self.username_entry)
        form.addRow("Password:", self.password_entry)
        form.addRow("Confirm:", self.confirm_entry)
        self.readonly_check = QCheckBox("Read-only access")
        if show_access_level:
            form.addRow("", self.readonly_check)
        layout.addLayout(form)

        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #c0392b;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_ok(self):
        username = self.username_entry.text().strip()
        password = self.password_entry.text()
        confirm = self.confirm_entry.text()
        ok, message = SMBWizard.check_username(username)
        if not ok:
            self.error_label.setText(message)
            return
        if username in self._existing:
            self.error_label.setText(f"'{username}' already exists.")
            return
        if not password:
            self.error_label.setText("Password can't be empty.")
            return
        if password != confirm:
            self.error_label.setText("Passwords don't match.")
            return
        self.result_data = {
            "username": username, "password": password, "read_only": self.readonly_check.isChecked(),
        }
        self.accept()


class CreateShareDialog(QDialog):
    """Two-page wizard (name -> path), matching gui.py's own CreateShareDialog
    shape. Background share creation via _Worker (Phase 2's QThread
    replacement for the old threading.Thread + after(0) pattern)."""

    def __init__(self, parent, wizard: SMBWizard, on_done):
        super().__init__(parent)
        self.setWindowTitle("New Share")
        self.wizard = wizard
        self._on_done = on_done
        self._worker = None

        layout = QVBoxLayout(self)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        # Page 1: name
        name_page = QWidget()
        name_layout = QVBoxLayout(name_page)
        name_layout.addWidget(QLabel("Share name:"))
        self.name_entry = QLineEdit()
        # Blocks disallowed characters/length at the keystroke, not just
        # on submit - matches gui.py's own CreateShareDialog
        # (_validate_name_input(), a Tk validate="key" callback). Same
        # source-of-truth pattern/length as core.check_share_name() -
        # share names are written unescaped as a "[name]" smb.conf
        # section header, so this is a real injection guard, not just
        # UX polish (see SHARE_NAME_RE's own comment in core.py).
        self.name_entry.setMaxLength(SHARE_NAME_MAX_LEN)
        self.name_entry.setValidator(
            QRegularExpressionValidator(QRegularExpression(SHARE_NAME_RE.pattern), self.name_entry)
        )
        name_layout.addWidget(self.name_entry)
        self.name_error = QLabel()
        self.name_error.setStyleSheet("color: #c0392b;")
        name_layout.addWidget(self.name_error)
        next_btn = QPushButton("Next")
        next_btn.clicked.connect(self._go_to_path_page)
        name_layout.addWidget(next_btn)
        self.stack.addWidget(name_page)

        # Page 2: path
        path_page = QWidget()
        path_layout = QVBoxLayout(path_page)
        path_layout.addWidget(QLabel("Folder to share:"))
        path_row = QHBoxLayout()
        self.path_entry = QLineEdit()
        path_row.addWidget(self.path_entry)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        path_layout.addLayout(path_row)
        self.path_error = QLabel()
        self.path_error.setStyleSheet("color: #c0392b;")
        path_layout.addWidget(self.path_error)
        buttons_row = QHBoxLayout()
        back_btn = QPushButton("Back")
        back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        buttons_row.addWidget(back_btn)
        self.create_btn = QPushButton("Create Share")
        self.create_btn.clicked.connect(self._on_create)
        buttons_row.addWidget(self.create_btn)
        path_layout.addLayout(buttons_row)
        self.stack.addWidget(path_page)

    def _go_to_path_page(self):
        name = self.name_entry.text().strip()
        ok, message = self.wizard.check_share_name(name)
        if not ok:
            self.name_error.setText(message)
            return
        self.name_error.setText("")
        self.path_entry.setText(self.wizard.default_share_path(name))
        self.stack.setCurrentIndex(1)

    def _browse(self):
        # pick_directory_native() is itself a same-line no-op on
        # Windows/macOS ("if platform.system() != 'Linux': return
        # False, None") - gated here the same way gui.py's own
        # CreateShareDialog._browse_for_path() gates it, rather than
        # calling it unconditionally just to always get (False, None)
        # back on those platforms. Real signature is (handled, path),
        # not a bare path - matches gui.py's own unpacking exactly.
        handled, selected = False, None
        if platform.system() == "Linux":
            handled, selected = pick_directory_native("Select Folder to Share")
        if not handled:
            selected = QFileDialog.getExistingDirectory(self, "Select Folder to Share")
        if selected:
            self.path_entry.setText(os.path.normpath(selected))

    def _on_create(self):
        name = self.name_entry.text().strip()
        path = self.path_entry.text().strip() or self.wizard.default_share_path()
        name_ok, name_message = self.wizard.check_share_name(name)
        if not name_ok:
            self.stack.setCurrentIndex(0)
            self.name_error.setText(name_message)
            return
        path_ok, path_message = self.wizard.check_share_path(path)
        if not path_ok:
            self.path_error.setText(path_message)
            return

        self.wizard.share_name = name
        self.wizard.share_path = path
        self.wizard.users = []
        self.create_btn.setEnabled(False)

        def apply():
            if self.wizard.has_admin_privileges():
                self.wizard.dispatch_execution()
            else:
                self.wizard.elevate_and_apply({
                    "name": self.wizard.share_name, "path": self.wizard.share_path, "users": self.wizard.users,
                })
            # NOT the elevated step's own return value/captured output -
            # matches gui.py's own CreateShareDialog._apply_worker()
            # exactly. When elevation is needed, the actual work runs in
            # a SEPARATE relaunched process (UAC/pkexec/osascript) - its
            # own print() output (including a "Success." marker) lands
            # on THAT process's stdout, not something this thread can
            # ever see, and on Windows specifically there's no way to
            # stream it back at all (Start-Process -Verb RunAs can't be
            # combined with output redirection across the UAC boundary -
            # confirmed live: PowerShell's own Start-Process parameter
            # sets put -Verb and -RedirectStandardOutput/Error in
            # mutually exclusive sets). Re-querying whether the share
            # now actually exists sidesteps all of that.
            return any(s["name"] == self.wizard.share_name for s in self.wizard.list_shares())

        self._worker = _Worker(apply)
        self._worker.done.connect(self._on_create_finished)
        self._worker.start()

    def _on_create_finished(self, created, log_output):
        self.create_btn.setEnabled(True)
        self._on_done(self.wizard.share_name if created else None, log_output)
        self.accept()


# ---------------------------------------------------------------------------
# Users panel
# ---------------------------------------------------------------------------
class UserManagementPanel(QWidget):
    def __init__(self, main_window: "MainWindow"):
        super().__init__()
        self.main_window = main_window
        self.wizard = main_window.wizard

        layout = QVBoxLayout(self)
        # No "Users" title above the tree - matches gui.py's own
        # UserManagementPanel, which drops its title deliberately so this
        # panel's tree starts at the exact same y as the shares list's
        # tree (see that class's docstring).
        layout.setContentsMargins(8, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Username"])
        # The single "Username" column has to be told explicitly to
        # fill the tree's full width - without this, Qt leaves the
        # remaining viewport space unclaimed by any column, which reads
        # as a phantom second "column" that a row's own selection
        # highlight/background never covers (confirmed live: a selected
        # row's green highlight stopped partway across, well short of
        # the scrollbar).
        self.tree.header().setStretchLastSection(True)
        # Always-visible vertical scrollbar (no horizontal - a single
        # column never needs one) - matches gui.py's own users_list,
        # which packs a real ttk.Scrollbar unconditionally rather than
        # Qt's default auto-hide-when-not-needed policy.
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.tree.itemClicked.connect(self._on_item_clicked)
        _BlankClickDeselecter(self.tree)
        layout.addWidget(self.tree, 1)

        # Pinned "+" row inside the tree - matches gui.py's own
        # UserManagementPanel ("'New User' is a real row of the Users
        # panel's own Treeview now too"), not a separate button. Plain
        # "+" text, not the icon_add.png asset - Qt's default tree
        # delegate only honors setTextAlignment() for TEXT, not for a
        # decoration icon (which stays left-anchored regardless of
        # alignment, even on a spanned item) - confirmed live, a
        # left-stuck icon doesn't match _AddRowFeedback's own centered-
        # across-the-whole-row look. A bold, larger-than-body-text "+"
        # gets close to the same visual weight without needing a custom
        # paint delegate just for this one glyph. Built fresh in
        # _apply_users() on every populate, not once here - see
        # _make_add_row()'s own docstring for why.
        self._add_user_row = None
        self._add_user_row_overlay = None
        self._sorter = _TreeSorter(self.tree, lambda: self._add_user_row)

        # Floating, row-anchored action buttons instead of a fixed
        # static row - see _RowActionBar's own docstring. No separate
        # Change Password/Delete buttons live anywhere else now.
        self._action_bar = _RowActionBar(self.tree, self._build_row_actions)

    def _build_row_actions(self, container: QHBoxLayout, item) -> bool:
        if item is self._add_user_row:
            return False
        container.addWidget(_row_action_button("icon_key", "Change Password", self._on_change_password))
        container.addWidget(_row_action_button("icon_delete", "Delete User", self._on_delete_user))
        return True

    def _on_item_clicked(self, item, column):
        if item is self._add_user_row:
            self.tree.clearSelection()
            self._on_new_user()

    def refresh(self):
        def fetch():
            return self.wizard.list_users()

        worker = _Worker(fetch)
        worker.done.connect(self._apply_users)
        self.main_window._keep_alive(worker)
        worker.start()

    def _apply_users(self, users, log_output):
        self.tree.clear()
        self._add_user_row = _make_add_row()
        self.tree.addTopLevelItem(self._add_user_row)
        # Must be set AFTER the item is actually in the tree - see the
        # shares tree's identical call in _populate_shares() for why.
        self._add_user_row.setFirstColumnSpanned(True)
        if self._add_user_row_overlay is not None:
            self._add_user_row_overlay.deleteLater()
        self._add_user_row_overlay = _attach_add_row_icon(self.tree, self._add_user_row, self._on_new_user)
        for u in users or []:
            username = u.get("username", "?")
            # Labels a pre-existing (non-NASsie) account right in the
            # list itself, not just in the Attach User picker (see
            # MainWindow._enter_attach_mode()'s identical labeling) -
            # requested live: a real account like a Windows login
            # showing here (it has share access, or was otherwise
            # surfaced) needs to read as clearly NOT one NASsie can
            # freely delete/reset, at a glance, before ever reaching
            # for the Delete/Change Password buttons that already
            # guard against it.
            if not u.get("managed", False):
                username += " (existing account)"
            QTreeWidgetItem(self.tree, [username])
        self._sorter.apply()

    def _on_new_user(self):
        # Full account list from the wizard, not just this tree's own
        # (filtered) displayed rows - refresh()/_apply_users() only
        # shows accounts NASsie manages or that already have share
        # access, but a real pre-existing Windows account with neither
        # yet is still a genuine collision here: create_user() resets an
        # EXISTING account's password unconditionally on a name match,
        # with no managed-only guard of its own - this uniqueness check
        # is the only thing standing between a typed name and silently
        # overwriting a real person's login password. Matches gui.py's
        # _create_new_user(), which validates against the same full list.
        worker = _Worker(self.wizard.list_users)
        worker.done.connect(lambda users, log: self._show_new_user_dialog(users))
        self.main_window._keep_alive(worker)
        worker.start()

    def _show_new_user_dialog(self, users):
        existing = {u.get("username") for u in users or []}
        dialog = AddUserDialog(self, existing, show_access_level=False)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_data:
            return
        data = dialog.result_data
        worker = _Worker(self.wizard.add_user, data["username"], data["password"])
        worker.done.connect(lambda result, log: self._on_created(data["username"], result, log))
        self.main_window._keep_alive(worker)
        worker.start()

    def _on_created(self, username, created, log_output):
        if log_output.strip():
            self.main_window.log_panel.append(log_output)
        if created:
            self.main_window._toast(f"'{username}' has been created.")
        else:
            QMessageBox.critical(self, "Failed", f"Could not create user '{username}'.\n\n{log_output.strip()}")
        self.main_window.refresh_all()

    def _on_delete_user(self):
        items = self.tree.selectedItems()
        if not items or items[0] is self._add_user_row:
            return
        # Strip the " (existing account)" label back off - that's
        # display-only (see _apply_users()), not part of the real
        # username.
        username = items[0].text(0).split(" (")[0]
        worker = _Worker(self.wizard.list_users)
        worker.done.connect(lambda users, log, u=username: self._confirm_delete_user(u, users))
        self.main_window._keep_alive(worker)
        worker.start()

    def _confirm_delete_user(self, username, users):
        user = next((u for u in users or [] if u.get("username") == username), None)
        if not (user and user.get("managed", False)):
            # Never delete an account NASsie didn't create - matches
            # gui.py's UserManagementPanel._delete_user() guard exactly.
            QMessageBox.information(
                self, "Delete User",
                f"'{username}' is an existing computer account, not one NASsie created - NASsie won't "
                "delete it. Detach it from its shares instead, or delete the account itself from "
                "your computer's own account settings.",
            )
            return
        if QMessageBox.question(
            self, "Delete User",
            f"Delete user '{username}' entirely? This removes their account everywhere, not just one share.",
        ) != QMessageBox.StandardButton.Yes:
            return
        worker = _Worker(self.wizard.remove_user, username)
        worker.done.connect(lambda result, log, u=username: self._on_deleted(u, result, log))
        self.main_window._keep_alive(worker)
        worker.start()

    def _on_deleted(self, username, deleted, log_output):
        if log_output.strip():
            self.main_window.log_panel.append(log_output)
        if deleted:
            self.main_window._toast(f"Deleted user '{username}'.")
        else:
            QMessageBox.critical(self, "Failed", f"Could not delete user '{username}'.\n\n{log_output.strip()}")
        self.main_window.refresh_all()

    def _on_change_password(self):
        items = self.tree.selectedItems()
        if not items or items[0] is self._add_user_row:
            return
        username = items[0].text(0).split(" (")[0]
        worker = _Worker(self.wizard.list_users)
        worker.done.connect(lambda users, log, u=username: self._change_password_flow(u, users))
        self.main_window._keep_alive(worker)
        worker.start()

    def _change_password_flow(self, username, users):
        user = next((u for u in users or [] if u.get("username") == username), None)
        shares = (user or {}).get("shares", [])
        if not shares:
            QMessageBox.information(self, "Change Password", f"'{username}' doesn't have access to any share yet.")
            return
        # Windows has no separate SMB password store - changing it here
        # would also change what a real Windows account signs in with, so
        # never do that for an account NASsie didn't create itself.
        # Matches gui.py's identical guard.
        if not (user or {}).get("managed", False) and self.wizard.system == "Windows":
            QMessageBox.information(
                self, "Change Password",
                f"'{username}' is an existing Windows account, not one NASsie created - changing its "
                "password here would also change what they sign in with, so NASsie won't do that. "
                "Change their password from Windows' own account settings instead.",
            )
            return
        if QMessageBox.question(self, "Change Password", QR_PASSWORD_RESET_NOTE) != QMessageBox.StandardButton.Yes:
            return
        if len(shares) == 1:
            self._prompt_new_password(username, shares[0])
            return
        dialog = ChoiceDialog(self, "Choose share", "Change password (and show a QR code) for which share?", shares)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_value:
            return
        self._prompt_new_password(username, dialog.result_value)

    def _prompt_new_password(self, username, share_name):
        pw_dialog = PasswordPromptDialog(
            self, "New password", f"New password for '{username}' (replaces their current one):",
        )
        if pw_dialog.exec() != QDialog.DialogCode.Accepted or not pw_dialog.result_value:
            return
        password = pw_dialog.result_value
        worker = _Worker(self._do_change_password, share_name, username, password)
        worker.done.connect(
            lambda result, log, s=share_name, u=username, p=password: self._on_password_changed(s, u, p, result, log)
        )
        self.main_window._keep_alive(worker)
        worker.start()

    def _do_change_password(self, share_name, username, password):
        # Preserve the user's current access level on this share - a
        # password change shouldn't silently flip them back to
        # read-write. Matches gui.py's identical lookup.
        shares = self.wizard.list_shares()
        share = next((s for s in shares if s["name"] == share_name), None)
        share_user = next((u for u in (share or {}).get("users", []) if u["username"] == username), None)
        read_only = share_user.get("read_only", False) if share_user else False
        return self.wizard.grant_share_access(share_name, username, password, read_only)

    def _on_password_changed(self, share, username, password, changed, log_output):
        if log_output.strip():
            self.main_window.log_panel.append(log_output)
        if changed:
            self.main_window._toast(f"Password changed for '{username}'.")
            payload = self.wizard.build_locknas_qr_payload(share, username, password)
            QrCodeDialog(self, share, username, payload).exec()
        else:
            QMessageBox.critical(self, "Failed", f"Could not change password.\n\n{log_output.strip()}")
        self.main_window.refresh_all()


class LogPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        # No top/bottom margin - matches the shares tree's own 0-margin
        # layout and the Users panel's identical top=0 convention, so
        # the Log panel's text box is exactly as tall as the shares
        # list, not visibly shorter/inset (requested live). Log docks
        # on the LEFT (see MainWindow.__init__), so the gutter toward
        # the shares tree is this panel's own RIGHT edge, not left -
        # opposite of the Users panel's (8, 0, 0, 0).
        layout.setContentsMargins(0, 0, 8, 0)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text, 1)

    def append(self, text):
        self.text.appendPlainText(text)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.wizard = SMBWizard()
        self._workers = []  # keeps QThread objects alive until they finish
        self._suppress_transitions = None
        # Independent open/closed state per side - Log and Users can
        # both be open at once now (requested live), unlike the earlier
        # single shared _panel_kind design that force-closed one to
        # open the other. self._animation tracks whichever ONE glide is
        # currently running (both panels animate the SAME window
        # "geometry" property, so two QPropertyAnimations can't run on
        # it at once without fighting each other frame-by-frame) -
        # _pending_toggle queues at most one more request (the latest
        # click wins) to run right after the current glide finishes,
        # rather than starting a second animation on top of it.
        self._panel_state = {"log": False, "users": False}
        self._animation = None
        self._pending_toggle = None

        # Which shares_tree item (if any) is currently showing the
        # inline "Attach User" combobox instead of its normal row
        # action buttons - see _on_attach_user()/_build_inline_attach().
        # Matches gui.py's own GUIWizard.__init__ identical state.
        self._attaching_item = None
        self._attaching_share = None
        self._attaching_candidates = []
        self._attaching_labels = {}

        self.setWindowTitle("NASsie")
        self.resize(_BASE_WIDTH, _BASE_HEIGHT)
        # A floor matching the no-panel baseline - matches gui.py's own
        # root.minsize(width, height), set right after computing that
        # same baseline geometry. Without it, the window is otherwise
        # freely resizable down to nothing, squeezing the shares list
        # and toolbar into an overlapping, unreadable mess instead of
        # just clipping/scrolling.
        self.setMinimumSize(_BASE_WIDTH, _BASE_HEIGHT)

        icon_path = os.path.join(_asset_base_dir(), "nassie_icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_header(icon_path))

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        root_layout.addWidget(separator)

        content_row = QHBoxLayout()
        content_row.setSpacing(0)
        root_layout.addLayout(content_row, 1)

        # Log docks on the LEFT (the window grows/glides leftward to
        # reveal it - see _animate_panel()'s grows_left handling), Users
        # on the RIGHT - matches gui.py's own original design intent
        # (GUIWizard._toggle_log_panel()'s docstring, and the toolbar's
        # own never-restored ".pack(side='left')" comment for the Log
        # button). Tk had to hide the Log button entirely because
        # growing the window leftward meant moving root's own x position
        # frame-by-frame via manual after()-stepped geometry() calls,
        # which glitched visibly and had no fix. Qt's QPropertyAnimation
        # animates x and width together as one real, compositor-backed
        # motion - the same mechanism already used for the right-growing
        # Users panel - so that specific glitch doesn't apply here; two
        # independent hosts (one per side) is what makes both directions
        # possible from the same _animate_panel() logic.
        self.log_panel = LogPanel()
        self.log_panel_host = QWidget()
        self.log_panel_host.setFixedWidth(0)
        log_host_layout = QVBoxLayout(self.log_panel_host)
        log_host_layout.setContentsMargins(0, 0, 0, 0)
        log_host_layout.addWidget(self.log_panel)
        self.log_panel.hide()
        content_row.addWidget(self.log_panel_host)

        content_row.addWidget(self._build_shares_page(), 1)

        self.user_mgmt_panel = UserManagementPanel(self)
        self.side_panel_host = QWidget()
        self.side_panel_host.setFixedWidth(0)
        side_layout = QVBoxLayout(self.side_panel_host)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self.user_mgmt_panel)
        self.user_mgmt_panel.hide()
        content_row.addWidget(self.side_panel_host)

        self.refresh_all()

    # -- header/toolbar/shares list construction --------------------------

    def _build_header(self, icon_path: str) -> QWidget:
        # One combined row - the logo and the two toggle buttons have no
        # reason to sit in separate rows of their own (requested live) -
        # Log on the left (where its panel now docks - see
        # log_panel_host in __init__), logo centered, Users on the right
        # (ditto).
        header = QWidget()
        header.setFixedHeight(56)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)

        self.log_btn = self._toolbar_toggle_button("icon_log", "View Log", "log")
        layout.addWidget(self.log_btn)

        layout.addStretch(1)
        if os.path.exists(icon_path):
            logo = QLabel()
            pixmap = QPixmap(icon_path).scaled(
                _LOGO_SIZE, _LOGO_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
            logo.setPixmap(pixmap)
            layout.addWidget(logo)
        layout.addStretch(1)

        self.users_btn = self._toolbar_toggle_button("icon_users", "Manage Users", "users")
        layout.addWidget(self.users_btn)
        return header

    def _toolbar_toggle_button(self, icon_name, tooltip, kind) -> QToolButton:
        btn = QToolButton()
        icon = _icon(icon_name)
        if not icon.isNull():
            btn.setIcon(icon)
            btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        else:
            btn.setText(tooltip[:1])
        btn.setFixedSize(_TOGGLE_BUTTON_SIZE, _TOGGLE_BUTTON_SIZE)
        btn.setCheckable(True)
        btn.setToolTip(tooltip)
        btn.clicked.connect(lambda: self._toggle_panel(kind))
        return btn

    def _build_shares_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self.shares_tree = QTreeWidget()
        self.shares_tree.setHeaderLabels(["Share Name", "Path"])
        # "Path" (the last column) fills any remaining width instead of
        # leaving it unclaimed by any column - matches gui.py's own
        # shares_list.column("path", stretch=True). Still scrolls
        # horizontally when content genuinely needs more room than
        # that (see the AlwaysOn policy below) - stretch only fills a
        # GAP, it doesn't cap the column's natural width.
        self.shares_tree.header().setStretchLastSection(True)
        # Always-visible scrollbars, both axes - matches gui.py's own
        # shares_list, which packs real ttk.Scrollbar widgets (vertical
        # AND horizontal) unconditionally rather than Qt's default
        # auto-hide-when-not-needed policy.
        self.shares_tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.shares_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.shares_tree.itemClicked.connect(self._on_shares_item_clicked)
        _BlankClickDeselecter(self.shares_tree)
        layout.addWidget(self.shares_tree, 1)

        # Pinned "+" row INSIDE the tree, not a separate button below it -
        # matches gui.py's own design (_populate_shares_list()'s own
        # add-row, tag_configure("add_row", background=_ADD_ROW_BG)).
        # setFirstColumnSpanned(True) centers it across the WHOLE row
        # (every column, not just column 0); the actual "+" glyph and
        # its hover/press feedback come from _AddRowOverlay - see its
        # own docstring for why that has to be a real floating widget,
        # not the tree's own per-column cell rendering. Built fresh in
        # _populate_shares() on every populate, not once here - see
        # _make_add_row()'s own docstring for why.
        self._add_share_row = None
        self._add_share_row_overlay = None
        self._shares_sorter = _TreeSorter(self.shares_tree, lambda: self._add_share_row)

        # Floating, row-anchored action buttons instead of a fixed
        # static row - see _RowActionBar's own docstring. No separate
        # New User/Attach/Delete/etc. buttons live anywhere else now.
        self._share_action_bar = _RowActionBar(self.shares_tree, self._build_share_row_actions)

        return page

    # -- panel toggle animation (the actual point of this migration) ------

    # kind -> (panel, toggle button, fixed-width host, grows the window
    # to the left instead of the right). Log's host sits left of the
    # shares page, Users' sits right of it (see __init__) - growing left
    # means x decreases as width increases, keeping the right edge (and
    # everything already on screen) visually anchored in place, same as
    # growing right keeps the LEFT edge anchored.
    def _panel_info(self, kind):
        if kind == "log":
            return self.log_panel, self.log_btn, self.log_panel_host, True
        return self.user_mgmt_panel, self.users_btn, self.side_panel_host, False

    def _toggle_panel(self, kind):
        if self._animation is not None:
            # A glide is already in flight (either side) - queue this
            # request instead of starting a second QPropertyAnimation
            # on the same window "geometry" property, which would fight
            # the running one frame-by-frame rather than composing with
            # it. Only the latest queued request survives; it runs the
            # moment the current glide's on_finished() fires.
            self._pending_toggle = kind
            return
        opening = not self._panel_state[kind]
        if opening and kind == "users":
            self.user_mgmt_panel.refresh()
        self._animate_panel(kind, opening)

    def _animate_panel(self, kind: str, opening: bool):
        panel, btn, host, grows_left = self._panel_info(kind)

        start = self.geometry()
        width_delta = _PANEL_WIDTH if opening else -_PANEL_WIDTH
        target_width = start.width() + width_delta
        end_x = start.x() - width_delta if grows_left else start.x()
        end = QRect(end_x, start.y(), target_width, start.height())

        if opening:
            panel.show()
            host.setFixedWidth(_PANEL_WIDTH)

        if self._suppress_transitions:
            self._suppress_transitions(True)

        self._animation = QPropertyAnimation(self, b"geometry")
        self._animation.setDuration(_GLIDE_MS)
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def on_finished():
            if not opening:
                panel.hide()
                host.setFixedWidth(0)
            self._panel_state[kind] = opening
            btn.setChecked(opening)
            if self._suppress_transitions:
                self._suppress_transitions(False)
            self._animation = None
            if self._pending_toggle is not None:
                next_kind = self._pending_toggle
                self._pending_toggle = None
                self._toggle_panel(next_kind)

        self._animation.finished.connect(on_finished)
        self._animation.start()

    # -- worker lifetime ----------------------------------------------------

    def _keep_alive(self, worker: _Worker):
        self._workers.append(worker)
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)

    def _toast(self, message):
        # Phase 2 stand-in for gui.py's own transient _Toast widget -
        # functional (log-panel visibility + a message box would be too
        # heavy-handed for routine confirmations), a real toast widget is
        # cosmetic polish left for a later pass.
        self.statusBar().showMessage(message, 4000)

    # -- data refresh ---------------------------------------------------

    def refresh_all(self):
        worker = _Worker(self._fetch_all)
        worker.done.connect(self._apply_all)
        self._keep_alive(worker)
        worker.start()

    def _fetch_all(self):
        shares = self.wizard.list_shares()
        groups = self.wizard.list_groups()
        overrides = self.wizard.effective_share_access(shares, groups)
        return {"shares": shares, "overrides": overrides}

    def _apply_all(self, data, log_output):
        if not data:
            return
        self._populate_shares(data["shares"])
        if self._panel_state["users"]:
            self.user_mgmt_panel.refresh()

    def _populate_shares(self, shares):
        self.shares_tree.clear()
        self._add_share_row = _make_add_row()
        self.shares_tree.addTopLevelItem(self._add_share_row)
        # Must be set AFTER the item is actually in the tree (a fresh
        # QTreeWidgetItem has no model index to span until then) - see
        # the item's own construction comment for why this matters:
        # centering the "+" glyph across the whole row, not just
        # column 0.
        self._add_share_row.setFirstColumnSpanned(True)
        if self._add_share_row_overlay is not None:
            self._add_share_row_overlay.deleteLater()
        self._add_share_row_overlay = _attach_add_row_icon(self.shares_tree, self._add_share_row, self._on_new_share)
        for share in shares or []:
            name = share.get("name", "?")
            path = share.get("path") or "Unknown"
            share_item = QTreeWidgetItem(self.shares_tree, [name, path])
            for u in share.get("users", []):
                label = u.get("username", "?")
                if u.get("read_only"):
                    label += " (read-only)"
                QTreeWidgetItem(share_item, [label, ""])
        self.shares_tree.expandAll()
        self._shares_sorter.apply()

    def _selected_share_and_user(self):
        items = self.shares_tree.selectedItems()
        if not items or items[0] is self._add_share_row:
            return None, None
        item = items[0]
        if item.parent() is None:
            return item.text(0), None
        return item.parent().text(0), item.text(0).split(" (")[0]

    def _on_shares_item_clicked(self, item, column):
        if item is self._add_share_row:
            self.shares_tree.clearSelection()
            self._on_new_share()

    def _build_share_row_actions(self, container: QHBoxLayout, item) -> bool:
        # The add-row is a real Treeview row too, so _RowActionBar's own
        # itemSelectionChanged calls this for it at least once before
        # _on_shares_item_clicked() clears the selection - nothing here
        # applies to it. Matches gui.py's own identical guard.
        if item is self._add_share_row:
            return False
        if item is self._attaching_item:
            return self._build_inline_attach(container, item)
        if item.parent() is not None:
            # A user row's own actions are scoped to that user's access
            # to this share, not the share itself. Order matches
            # gui.py's own _build_share_action_bar(): permission toggle
            # (the state checked most often), QR code, detach (the
            # destructive one, furthest from an accidental click).
            read_only = "(read-only)" in item.text(0)
            icon, tip = ("icon_readonly", "Read-only - click to make read-write") if read_only else (
                "icon_readwrite", "Read-write - click to make read-only"
            )
            container.addWidget(_row_action_button(icon, tip, self._on_toggle_access))
            container.addWidget(_row_action_button("icon_qr", "Show QR Code", self._on_show_qr))
            container.addWidget(_row_action_button("icon_detach", "Detach User", self._on_detach_user))
        else:
            container.addWidget(_row_action_button("icon_new_user", "New User", self._on_grant_new_user))
            container.addWidget(_row_action_button("icon_attach", "Attach User", self._on_attach_user))
            container.addWidget(_row_action_button("icon_delete", "Delete Share", self._on_delete_share))
        return True

    def _build_inline_attach(self, container: QHBoxLayout, item) -> bool:
        # Selecting a value commits immediately - no OK/Cancel, no
        # separate dialog window. Matches gui.py's own
        # _build_inline_attach()/_commit_inline_attach().
        combo = QComboBox()
        label_to_user = {}
        for u in self._attaching_candidates:
            label = self._attaching_labels.get(u["username"], u["username"])
            combo.addItem(label)
            label_to_user[label] = u
        combo.setCurrentIndex(-1)

        def on_activated(index):
            user = label_to_user.get(combo.itemText(index))
            if user:
                self._commit_inline_attach(user)

        combo.activated.connect(on_activated)
        container.addWidget(combo, 1)
        container.addWidget(_row_action_button("icon_cancel", "Cancel", self._cancel_inline_attach))
        # Opens the dropdown right away - the whole point of a
        # contextual dropdown is picking a user in one motion, not a
        # second click just to open what clicking Attach already asked
        # for. Deferred - the combo has to actually be laid out first.
        QTimer.singleShot(10, combo.showPopup)
        return True

    def _cancel_inline_attach(self):
        self._attaching_item = None
        self._attaching_share = None
        self._attaching_candidates = []
        self._attaching_labels = {}
        self._share_action_bar.update_bar()

    # -- share/user actions ----------------------------------------------

    def _on_new_share(self):
        dialog = CreateShareDialog(self, self.wizard, self._on_share_created)
        dialog.exec()

    def _on_share_created(self, share_name, log_output):
        if log_output.strip():
            self.log_panel.append(log_output)
        if share_name:
            self._toast(f"Share '{share_name}' created.")
        else:
            QMessageBox.critical(self, "Failed", f"Could not create the share.\n\n{log_output.strip()}")
        self.refresh_all()

    def _on_delete_share(self):
        share, _ = self._selected_share_and_user()
        if not share:
            return
        if QMessageBox.question(self, "Delete Share", f"Delete share '{share}'?") != QMessageBox.StandardButton.Yes:
            return
        worker = _Worker(self.wizard.remove_share, share, False)
        worker.done.connect(lambda result, log: self._on_share_deleted(share, result, log))
        self._keep_alive(worker)
        worker.start()

    def _on_share_deleted(self, share, removed, log_output):
        if log_output.strip():
            self.log_panel.append(log_output)
        if removed:
            self._toast(f"Deleted share '{share}'.")
        else:
            QMessageBox.critical(self, "Failed", f"Could not delete share '{share}'.\n\n{log_output.strip()}")
        self.refresh_all()

    def _on_grant_new_user(self):
        share, _ = self._selected_share_and_user()
        if not share:
            return
        worker = _Worker(self.wizard.list_users)
        worker.done.connect(lambda users, log, s=share: self._show_grant_new_user_dialog(s, users))
        self._keep_alive(worker)
        worker.start()

    def _show_grant_new_user_dialog(self, share, users):
        # Real existing-username set, not empty - matches gui.py's
        # _new_user_for_selected_share(): without this, AddUserDialog's
        # own uniqueness check can't catch a typed name that collides
        # with a real existing account, and grant_share_access() would
        # then be called as if it were a brand new user.
        existing = {u.get("username") for u in users or []}
        dialog = AddUserDialog(self, existing)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_data:
            return
        data = dialog.result_data
        worker = _Worker(
            self.wizard.grant_share_access, share, data["username"], data["password"], data["read_only"],
        )
        worker.done.connect(lambda result, log: self._on_access_granted(share, data["username"], result, log))
        self._keep_alive(worker)
        worker.start()

    def _on_access_granted(self, share, username, added, log_output):
        if log_output.strip():
            self.log_panel.append(log_output)
        if added:
            self._toast(f"Added '{username}' to '{share}'.")
        else:
            QMessageBox.critical(self, "Failed", f"Could not add '{username}' to '{share}'.\n\n{log_output.strip()}")
        self.refresh_all()

    def _on_attach_user(self):
        share, _ = self._selected_share_and_user()
        if not share:
            return
        worker = _Worker(self._fetch_attach_candidates, share)
        worker.done.connect(lambda result, log, s=share: self._enter_attach_mode(s, result))
        self._keep_alive(worker)
        worker.start()

    def _fetch_attach_candidates(self, share):
        shares = self.wizard.list_shares()
        share_data = next((s for s in shares if s["name"] == share), None)
        already = {u["username"] for u in (share_data or {}).get("users", [])}
        return [u for u in self.wizard.list_users() if u["username"] not in already]

    def _enter_attach_mode(self, share, candidates):
        if not candidates:
            QMessageBox.information(self, "Attach User", "Every existing user already has access to this share.")
            return
        items = self.shares_tree.selectedItems()
        if not items:
            return
        # Labels a pre-existing (non-NASsie) account instead of hiding
        # it - matches gui.py's GUIWizard._attach_user_to_selected_
        # share() - this picker deliberately still offers real computer
        # accounts (existing_account_grant_message() covers the safety
        # story for picking one), but has to be distinguishable from a
        # NASsie-managed one, not shown identically.
        self._attaching_item = items[0]
        self._attaching_share = share
        self._attaching_candidates = candidates
        self._attaching_labels = {
            u["username"]: u["username"] if u.get("managed") else f'{u["username"]} (existing account)'
            for u in candidates
        }
        self._share_action_bar.update_bar()

    def _commit_inline_attach(self, user):
        share = self._attaching_share
        username = user["username"]
        self._attaching_item = None
        self._attaching_share = None
        self._attaching_candidates = []
        self._attaching_labels = {}
        self._share_action_bar.update_bar()

        # A password already exists for this account if it's managed by
        # NASsie (set at create_user() time) or already attached to
        # another share (Samba/macOS store one password per account, not
        # per share) - only a genuinely untouched pre-existing account
        # has no known credentials yet. Matches gui.py's
        # _commit_inline_attach() exactly.
        already_has_password = user.get("managed", False) or bool(user.get("shares"))
        password = None
        if not already_has_password:
            pw_dialog = PasswordPromptDialog(self, "Password", f"Set a password for '{username}' on this share:")
            if pw_dialog.exec() != QDialog.DialogCode.Accepted or not pw_dialog.result_value:
                return
            password = pw_dialog.result_value

        if not user.get("managed", False):
            if QMessageBox.question(
                self, "Existing computer account", self._existing_account_grant_message(username),
            ) != QMessageBox.StandardButton.Yes:
                return
            if self.wizard.system == "Windows":
                # Windows SMB has no password store of its own - it
                # authenticates against the same local account password
                # the person already signs in with. NEVER pass a typed
                # password through to _add_user_to_share_windows() for an
                # account NASsie didn't create - that would silently
                # overwrite their real Windows sign-in password. Matches
                # gui.py's identical override in _commit_inline_attach().
                password = None

        worker = _Worker(self.wizard.grant_share_access, share, username, password, False)
        worker.done.connect(lambda result, log: self._on_access_granted(share, username, result, log))
        self._keep_alive(worker)
        worker.start()

    def _existing_account_grant_message(self, username):
        # Matches gui.py's GUIWizard.existing_account_grant_message()
        # exactly.
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

    def _on_detach_user(self):
        share, user = self._selected_share_and_user()
        if not share or not user:
            return
        worker = _Worker(self.wizard.revoke_share_access, share, user)
        worker.done.connect(lambda result, log: self._on_access_revoked(share, user, result, log))
        self._keep_alive(worker)
        worker.start()

    def _on_access_revoked(self, share, user, revoked, log_output):
        if log_output.strip():
            self.log_panel.append(log_output)
        if revoked:
            self._toast(f"Removed {user}'s access to '{share}'.")
        else:
            QMessageBox.critical(self, "Failed", f"Could not remove access.\n\n{log_output.strip()}")
        self.refresh_all()

    def _on_toggle_access(self):
        share, user = self._selected_share_and_user()
        if not share or not user:
            return
        # Reads the current state back off the tree label rather than a
        # separate lookup - the label already encodes "(read-only)".
        item = self.shares_tree.selectedItems()[0]
        currently_read_only = "(read-only)" in item.text(0)
        worker = _Worker(self.wizard.change_share_access, share, user, not currently_read_only)
        worker.done.connect(lambda result, log: self._on_access_changed(share, user, result, log))
        self._keep_alive(worker)
        worker.start()

    def _on_access_changed(self, share, user, changed, log_output):
        if log_output.strip():
            self.log_panel.append(log_output)
        if not changed:
            QMessageBox.critical(self, "Failed", f"Could not change access.\n\n{log_output.strip()}")
        self.refresh_all()

    def _on_show_qr(self):
        share, user = self._selected_share_and_user()
        if not share or not user:
            return
        pw_dialog = PasswordPromptDialog(self, "Password", f"Enter {user}'s password to generate a QR code:")
        if pw_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        password = pw_dialog.result_value
        if not self.wizard.verify_password(user, password, share):
            QMessageBox.critical(self, "Failed", "Incorrect password.")
            return
        payload = self.wizard.build_locknas_qr_payload(share, user, password)
        QrCodeDialog(self, share, user, payload).exec()


def run():
    app = QApplication(sys.argv)
    app.setFont(QFont(_DEFAULT_FONT_FAMILY, _DEFAULT_FONT_SIZE))
    _apply_base_palette(app)
    app.setStyleSheet(_build_stylesheet())
    win = MainWindow()
    win.show()
    win._suppress_transitions = _round_window_corners(win)
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
