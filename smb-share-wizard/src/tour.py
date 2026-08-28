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


def _first_run_marker_path():
    if platform.system() == "Windows" and os.environ.get("APPDATA"):
        base = os.path.join(os.environ["APPDATA"], "NASsie")
    else:
        base = os.path.expanduser("~/.config/nassie")
    return os.path.join(base, "tour_seen")


def has_seen_tour():
    return os.path.exists(_first_run_marker_path())


def mark_tour_seen():
    path = _first_run_marker_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w"):
        pass


class _HighlightBox:
    """Four thin borderless windows forming a rectangle outline around a
    widget - avoids relying on Tk's poorly-supported, Windows-only
    -transparentcolor trick to punch a see-through hole in one overlay."""

    def __init__(self, root):
        self.bars = [tk.Toplevel(root) for _ in range(4)]
        for bar in self.bars:
            bar.overrideredirect(True)
            bar.configure(bg=_HIGHLIGHT_COLOR)
            bar.attributes("-topmost", True)

    def place_around(self, widget):
        widget.update_idletasks()
        pad = 4
        x = widget.winfo_rootx() - pad
        y = widget.winfo_rooty() - pad
        w = widget.winfo_width() + pad * 2
        h = widget.winfo_height() + pad * 2
        t = _BORDER_THICKNESS
        top, bottom, left, right = self.bars
        top.geometry(f"{w}x{t}+{x}+{y}")
        bottom.geometry(f"{w}x{t}+{x}+{y + h - t}")
        left.geometry(f"{t}x{h}+{x}+{y}")
        right.geometry(f"{t}x{h}+{x + w - t}+{y}")

    def destroy(self):
        for bar in self.bars:
            bar.destroy()


class _Callout(tk.Toplevel):
    def __init__(self, root, title, text, step_num, step_total, on_next, on_back, on_skip):
        super().__init__(root)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        # A 2px frame in the highlight color doubles as the callout's own
        # border - one less widget to draw and keep aligned.
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

        ttk.Button(btn_row, text="Skip Tour", command=on_skip).pack(side="right", padx=(4, 0))
        ttk.Button(btn_row, text="Next" if step_num < step_total else "Done", command=on_next).pack(
            side="right", padx=(4, 0)
        )
        if step_num > 1:
            ttk.Button(btn_row, text="Back", command=on_back).pack(side="right", padx=(4, 0))

    def place_near(self, widget, screen_w, screen_h):
        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()

        wx = widget.winfo_rootx()
        wy = widget.winfo_rooty()
        ww = widget.winfo_width()
        wh = widget.winfo_height()

        # Prefer sitting below the widget; flip above it if there isn't
        # room, then clamp horizontally so it never runs off either edge.
        x = min(max(wx, 0), max(screen_w - w, 0))
        below_y = wy + wh + 14
        y = below_y if below_y + h <= screen_h else max(wy - h - 14, 0)
        self.geometry(f"{w}x{h}+{x}+{y}")


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
        self._callout.place_near(widget, self.root.winfo_screenwidth(), self.root.winfo_screenheight())

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
