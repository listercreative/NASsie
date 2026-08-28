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
            bar.destroy()


class _Callout(tk.Frame):
    # Same reasoning as _HighlightBox: a real child widget of root, placed
    # via place() instead of a floating overrideredirect Toplevel, so it
    # can never end up displayed on top of some other window.
    def __init__(self, root, title, text, step_num, step_total, on_next, on_back, on_skip):
        super().__init__(root, bg=_HIGHLIGHT_COLOR, bd=0, highlightthickness=0, padx=2, pady=2)
        self.root = root

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

        ttk.Button(btn_row, text="Skip Tour", command=on_skip).pack(side="right", padx=(4, 0))
        ttk.Button(btn_row, text="Next" if step_num < step_total else "Done", command=on_next).pack(
            side="right", padx=(4, 0)
        )
        if step_num > 1:
            ttk.Button(btn_row, text="Back", command=on_back).pack(side="right", padx=(4, 0))

    def place_near(self, widget):
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()

        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()

        wx = widget.winfo_rootx() - self.root.winfo_rootx()
        wy = widget.winfo_rooty() - self.root.winfo_rooty()
        ww = widget.winfo_width()
        wh = widget.winfo_height()

        # Prefer sitting below the widget; flip above it if there isn't
        # room, then clamp horizontally so it never runs off either edge
        # of the window (its own bounds now, not the screen's).
        x = min(max(wx, 0), max(root_w - w, 0))
        below_y = wy + wh + 14
        y = below_y if below_y + h <= root_h else max(wy - h - 14, 0)
        self.place(x=x, y=y, width=w, height=h)
        self.lift()


class GuiTour:
    """A short, skippable, click-through tour of the main window - an
    outline drawn around each widget plus a callout explaining it, rather
    than an arrow that has to be aimed at the target from a distance."""

    def __init__(self, gui):
        self.gui = gui
        self.root = gui.root
        self.steps = self._build_steps()
        self.index = 0
        self._highlight = None
        self._callout = None

    def _build_steps(self):
        gui = self.gui
        return [
            (0, lambda: gui.name_entry, "Name your share",
             "Start here - give the folder you're sharing a name. This is what other "
             "computers will see it as on the network."),
            (0, lambda: gui.path_entry, "Pick a folder",
             "Choose the folder to share. Use Browse to pick one, or type a path directly."),
            (0, lambda: gui.users_list, "Add users",
             "Add the accounts that should be able to connect to this share, and whether "
             "each one gets read-only or full access."),
            (0, lambda: gui.create_button, "Create it",
             "Once a name, folder, and (optionally) users are set, click here to apply "
             "the configuration."),
            (1, lambda: gui.shares_list, "Manage existing shares",
             "Every share you've created shows up here, along with its folder and who has "
             "access. Select one to add users or delete it."),
            (2, lambda: gui.groups_list, "Groups",
             "Groups let you grant access to a whole set of users at once, and manage "
             "membership without touching each share individually."),
            (2, lambda: gui.system_users_list, "Users",
             "All accounts NASsie knows about, and every share/group each one belongs to - "
             "manage passwords and access from here."),
        ]

    def start(self):
        self.index = 0
        self._show_step()

    def _show_step(self):
        self._teardown_current()
        tab_index, widget_getter, title, text = self.steps[self.index]
        self.gui.notebook_select(tab_index)
        self.root.update_idletasks()
        widget = widget_getter()

        self._highlight = _HighlightBox(self.root)
        self._highlight.place_around(widget)

        self._callout = _Callout(
            self.root, title, text, self.index + 1, len(self.steps),
            on_next=self._next, on_back=self._back, on_skip=self.stop,
        )
        self._callout.place_near(widget)

    def _next(self):
        if self.index + 1 >= len(self.steps):
            self.stop()
            return
        self.index += 1
        self._show_step()

    def _back(self):
        if self.index == 0:
            return
        self.index -= 1
        self._show_step()

    def stop(self):
        self._teardown_current()

    def _teardown_current(self):
        if self._highlight:
            self._highlight.destroy()
            self._highlight = None
        if self._callout:
            self._callout.destroy()
            self._callout = None
