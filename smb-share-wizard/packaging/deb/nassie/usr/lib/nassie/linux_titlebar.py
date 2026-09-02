"""A hand-drawn titlebar for the main window, Linux only.

See window_corners.py's docstring for the diagnosis this exists to fix:
Mutter draws a SEPARATE decoration frame around a normal WM-managed window
to render its titlebar, and doesn't propagate the client's own X Shape
onto that frame - so no amount of shaping our own window ever rounds what's
actually on screen. The only way around that is to stop asking Mutter to
draw a frame at all.

install() does that via the Motif WM hints property (_MOTIF_WM_HINTS,
decorations=0) rather than Tk's overrideredirect(). That distinction is the
whole point: overrideredirect() takes a window out of WM management
entirely - no taskbar entry, no alt-tab, no iconify()/maximize support.
Motif hints only stop the WM from DRAWING decorations; the window stays
fully WM-managed, so iconify(), '-zoomed' maximize, and taskbar/alt-tab
presence keep working exactly as they do today - the WM is still the one
doing them, we're just also drawing our own titlebar row for the mouse to
grab instead of leaving that row for Mutter to draw.

Scoped to the main window only - dialogs keep their native decorations
(never part of the complaint, and a couple of tour steps already point at
a dialog's own native close button)."""
from __future__ import annotations

import ctypes
import tkinter as tk
from tkinter import ttk

import window_corners

_RESIZE_CURSORS = {
    "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
    "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
    "nw": "top_left_corner", "ne": "top_right_corner",
    "sw": "bottom_left_corner", "se": "bottom_right_corner",
}


def install(gui_wizard):
    """Best-effort, same posture as window_corners.apply() - if libX11
    isn't there or the property write fails, the window just keeps its
    native decorations, silently. Never worth failing the app over this."""
    root = gui_wizard.root
    if not _strip_decorations(root):
        return
    _build_titlebar(gui_wizard)


class _MotifWmHints(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_ulong),
        ("functions", ctypes.c_ulong),
        ("decorations", ctypes.c_ulong),
        ("input_mode", ctypes.c_long),
        ("status", ctypes.c_ulong),
    ]


def _strip_decorations(root):
    # Tk's own X connection is a separate client from the one opened
    # below, and Tk batches/defers its requests - root.winfo_id() can
    # return an ID for a window Tk has decided on locally but not yet
    # actually sent a CreateWindow request for. Racing that with our own
    # connection is silent, not just theoretically wrong: it hits BadWindow
    # (confirmed live), which install_scoped_x_error_handler() below is
    # specifically built to swallow rather than crash on - so without this,
    # _strip_decorations() reports success while the property write was a
    # no-op against a window the server doesn't know about yet.
    # update_idletasks() forces Tk to flush pending work, including
    # actually creating this window, before we go read its id.
    root.update_idletasks()
    try:
        xlib = ctypes.CDLL("libX11.so.6")
    except OSError:
        return False

    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    dpy = xlib.XOpenDisplay(None)
    if not dpy:
        return False

    # One synchronous, short-lived connection (open, two requests, close) -
    # see install_scoped_x_error_handler()'s docstring for why THIS shape
    # of caller has to call restore() before closing, unlike
    # window_corners.py's own long-lived per-<Configure> use of the same
    # helper.
    handler, restore = window_corners.install_scoped_x_error_handler(xlib, dpy)

    xlib.XInternAtom.restype = ctypes.c_ulong
    xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    motif_atom = xlib.XInternAtom(dpy, b"_MOTIF_WM_HINTS", 0)

    MWM_HINTS_DECORATIONS = 1 << 1
    hints = _MotifWmHints(flags=MWM_HINTS_DECORATIONS, functions=0, decorations=0, input_mode=0, status=0)

    xlib.XChangeProperty.restype = ctypes.c_int
    xlib.XChangeProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
    ]
    PROP_MODE_REPLACE = 0
    FORMAT_32 = 32
    xlib.XChangeProperty(
        dpy, root.winfo_id(), motif_atom, motif_atom, FORMAT_32,
        PROP_MODE_REPLACE, ctypes.byref(hints), 5,
    )
    xlib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    xlib.XSync(dpy, 0)
    restore()
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xlib.XCloseDisplay(dpy)
    return True


def _build_titlebar(gui_wizard):
    root = gui_wizard.root
    bg = ttk.Style(root).lookup("TFrame", "background") or "#f3f3f3"

    bar = tk.Frame(root, bg=bg)
    bar.pack(side="top", fill="x")

    left = tk.Frame(bar, bg=bg)
    left.pack(side="left", fill="y", padx=(10, 0), pady=6)

    # Kept on gui_wizard, not just a local - a PhotoImage with no surviving
    # Python reference gets garbage collected even though Tk is still
    # displaying it, which blanks the label the next time Tk redraws it.
    icon_image = getattr(gui_wizard, "_icon_image", None)
    if icon_image:
        scale = max(1, icon_image.width() // 18)
        gui_wizard._titlebar_icon_image = icon_image.subsample(scale, scale)
        tk.Label(left, image=gui_wizard._titlebar_icon_image, bg=bg).pack(side="left", padx=(0, 6))

    title_label = tk.Label(left, text="NASsie", bg=bg, font=("TkDefaultFont", 10, "bold"))
    title_label.pack(side="left")

    right = tk.Frame(bar, bg=bg)
    right.pack(side="right")

    maximize_state = {"maximized": False, "geometry": None}

    def toggle_maximize(event=None):
        if maximize_state["maximized"]:
            try:
                root.attributes("-zoomed", False)
            except tk.TclError:
                pass
            if maximize_state["geometry"]:
                root.geometry(maximize_state["geometry"])
            maximize_state["maximized"] = False
            maximize_btn.configure(text="▢")
        else:
            maximize_state["geometry"] = root.geometry()
            try:
                root.attributes("-zoomed", True)
            except tk.TclError:
                # '-zoomed' needs an EWMH-capable WM to honor it - true of
                # Mutter, but a manual full-work-area fallback costs
                # nothing and keeps this from being a dead button on
                # whatever WM doesn't.
                root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
            maximize_state["maximized"] = True
            maximize_btn.configure(text="❘❘")
        return "break"

    def _titlebar_button(parent, text, command, hover_bg, hover_fg=None):
        btn = tk.Label(parent, text=text, bg=bg, fg="#333333", padx=14, pady=4, font=("TkDefaultFont", 10))

        def on_enter(event):
            btn.configure(bg=hover_bg, fg=hover_fg or "#333333")

        def on_leave(event):
            btn.configure(bg=bg, fg="#333333")

        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        btn.bind("<Button-1>", lambda event: command())
        btn.pack(side="left")
        return btn

    _titlebar_button(right, "─", root.iconify, "#e5e5e5")
    maximize_btn = _titlebar_button(right, "▢", toggle_maximize, "#e5e5e5")
    _titlebar_button(right, "✕", root.destroy, "#e81123", hover_fg="white")

    drag_state = {}

    def on_drag_press(event):
        drag_state["x"] = event.x_root - root.winfo_x()
        drag_state["y"] = event.y_root - root.winfo_y()

    def on_drag_motion(event):
        if maximize_state["maximized"]:
            return
        root.geometry(f"+{event.x_root - drag_state['x']}+{event.y_root - drag_state['y']}")

    for widget in (bar, left, title_label):
        widget.bind("<ButtonPress-1>", on_drag_press)
        widget.bind("<B1-Motion>", on_drag_motion)
        widget.bind("<Double-Button-1>", toggle_maximize)

    _install_resize_grips(root, bg)


def _install_resize_grips(root, bg):
    thickness = 5
    corner_size = 10
    grips = {}

    def make_grip(edge):
        grip = tk.Frame(root, bg=bg, cursor=_RESIZE_CURSORS[edge])
        start = {}

        def on_press(event):
            start["data"] = (
                event.x_root, event.y_root,
                root.winfo_x(), root.winfo_y(),
                root.winfo_width(), root.winfo_height(),
            )

        def on_motion(event):
            if "data" not in start:
                return
            start_x, start_y, orig_x, orig_y, orig_w, orig_h = start["data"]
            dx = event.x_root - start_x
            dy = event.y_root - start_y
            min_w, min_h = root.minsize()
            x, y, w, h = orig_x, orig_y, orig_w, orig_h
            if "e" in edge:
                w = max(min_w, orig_w + dx)
            if "w" in edge:
                w = max(min_w, orig_w - dx)
                x = orig_x + (orig_w - w)
            if "s" in edge:
                h = max(min_h, orig_h + dy)
            if "n" in edge:
                h = max(min_h, orig_h - dy)
                y = orig_y + (orig_h - h)
            root.geometry(f"{w}x{h}+{x}+{y}")

        grip.bind("<ButtonPress-1>", on_press)
        grip.bind("<B1-Motion>", on_motion)
        return grip

    # place() rather than pack()/grid() specifically so these track root's
    # own size on every resize for free via relx/rely/relwidth/relheight,
    # with no <Configure> handler needed to keep repositioning them.
    edge_placement = {
        "n": dict(relx=0, rely=0, relwidth=1, height=thickness),
        "s": dict(relx=0, rely=1.0, anchor="sw", relwidth=1, height=thickness),
        "w": dict(relx=0, rely=0, relheight=1, width=thickness),
        "e": dict(relx=1.0, rely=0, anchor="ne", relheight=1, width=thickness),
    }
    for edge, placement in edge_placement.items():
        grips[edge] = make_grip(edge)
        grips[edge].place(**placement)

    # Corners are made and placed AFTER edges and lifted on top, so the
    # small square where an edge and its neighbor overlap resolves to the
    # (more specific) corner cursor/handler rather than whichever edge
    # frame happened to be drawn there first.
    corner_placement = {
        "nw": dict(relx=0, rely=0, width=corner_size, height=corner_size),
        "ne": dict(relx=1.0, rely=0, anchor="ne", width=corner_size, height=corner_size),
        "sw": dict(relx=0, rely=1.0, anchor="sw", width=corner_size, height=corner_size),
        "se": dict(relx=1.0, rely=1.0, anchor="se", width=corner_size, height=corner_size),
    }
    for edge, placement in corner_placement.items():
        grips[edge] = make_grip(edge)
        grips[edge].place(**placement)
        grips[edge].lift()
