"""Best-effort native corner rounding for the main window.

Windows 11 already rounds every top-level window via DWM with no app
involvement in the common case, but DWMWA_WINDOW_CORNER_PREFERENCE makes
that explicit rather than relying on it. macOS has rounded window
corners at the OS level unconditionally - nothing to do there.

Linux is the one platform that actually needs work: Tk has no native
Wayland backend, so even on a Wayland session (like the one this was
diagnosed on) it's always speaking X11, via XWayland. A WM that themes
its own titlebar (GNOME/Mutter included) draws rounded corners on just
that titlebar strip - the window's own client rectangle underneath it,
which is all Tk actually owns, stays a plain square-cornered X window
by default. apply() below clips that client rectangle's bottom two
corners with the X Shape extension so they match the WM-drawn rounded
top instead of standing out as hard corners under it - see the
GUIWizard.__init__ call site in gui.py for why only the bottom two.
"""
from __future__ import annotations

import ctypes
import platform
import tkinter as tk


def apply(window, radius=8):
    """Best-effort - silently does nothing if the platform, the extension,
    or the shared library it needs isn't there. Never worth failing the
    app over a rounded corner."""
    system = platform.system()
    if system == "Windows":
        _round_windows(window)
    elif system == "Linux":
        _round_linux_bottom(window, radius)
    # macOS: already rounded natively.


def _round_windows(window):
    try:
        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return
    # winfo_id() is Tk's own child frame HWND, not the OS-decorated
    # top-level window DWM actually draws - GetParent() walks up to that
    # one. The same lookup ttkbootstrap/sv_ttk-style "set the titlebar
    # dark too" helpers use for the identical reason.
    hwnd = user32.GetParent(window.winfo_id())
    DWMWA_WINDOW_CORNER_PREFERENCE = 33
    DWMWCP_ROUND = 2
    preference = ctypes.c_int(DWMWCP_ROUND)
    try:
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference), ctypes.sizeof(preference),
        )
    except OSError:
        # Windows 10 and earlier don't have this attribute at all -
        # DwmSetWindowAttribute just errors instead of no-oping.
        pass


class _XRectangle(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_short), ("y", ctypes.c_short),
        ("width", ctypes.c_ushort), ("height", ctypes.c_ushort),
    ]


class _XErrorEvent(ctypes.Structure):
    # Only the leading fields ctypes needs to read (display, to tell an
    # error on OUR throwaway connection apart from one on Tk's own) -
    # the struct's real tail (error_code/request_code/minor_code/
    # resourceid) is left off since nothing here reads them, but the
    # layout up to here must match Xlib.h exactly for `display` to line
    # up correctly.
    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("serial", ctypes.c_ulong),
    ]


_ERROR_HANDLER_TYPE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))


def install_scoped_x_error_handler(xlib, dpy):
    """Swallows protocol errors raised against `dpy` specifically; chains
    anything else to whatever handler was already installed (almost
    certainly Tk's own) rather than clobbering it - XSetErrorHandler is one
    slot shared by every Display connection in the process, not per-
    connection, so a naive unconditional override here would also eat
    errors meant for Tk's own connection.

    Returns (handler, restore) - two things the caller manages differently
    depending on how long `dpy` sticks around:

    - A caller that keeps `dpy` open indefinitely (window_corners.py's own
      use: a per-<Configure> shape update that can fire at any future
      point) should never call `restore`, and must keep `handler` itself
      referenced for as long as that's true - a garbage-collected
      CFUNCTYPE is a use-after-free from libX11's side the next time it
      tries to invoke a now-freed function pointer.
    - A caller doing one synchronous, short-lived `dpy` (open, a couple of
      requests, close) should call `restore()` right before closing it,
      putting the previous handler back - otherwise this chain (and the
      Python closure `handler` keeps alive) outlives its own `dpy` and
      NOTHING is left keeping `handler` referenced once the function that
      installed it returns, so it's a ticking use-after-free the moment
      Python's GC actually collects it and Tk's connection hits an
      unrelated error later."""
    xlib.XSetErrorHandler.restype = ctypes.c_void_p
    xlib.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    previous_handler_addr = ctypes.c_void_p()

    def _on_x_error(display, error_event_ptr):
        try:
            if error_event_ptr and error_event_ptr.contents.display == dpy:
                return 0
        except (ValueError, AttributeError):
            return 0
        if previous_handler_addr.value:
            return ctypes.cast(previous_handler_addr, _ERROR_HANDLER_TYPE)(display, error_event_ptr)
        return 0

    handler = _ERROR_HANDLER_TYPE(_on_x_error)
    previous_handler_addr.value = xlib.XSetErrorHandler(handler)

    def restore():
        xlib.XSetErrorHandler(previous_handler_addr)

    return handler, restore


def _round_linux_bottom(window, radius):
    try:
        xlib = ctypes.CDLL("libX11.so.6")
        xext = ctypes.CDLL("libXext.so.6")
    except OSError:
        return

    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    dpy = xlib.XOpenDisplay(None)
    if not dpy:
        return

    xext.XShapeQueryExtension.restype = ctypes.c_int
    xext.XShapeQueryExtension.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
    ]
    event_base = ctypes.c_int()
    error_base = ctypes.c_int()
    if not xext.XShapeQueryExtension(dpy, ctypes.byref(event_base), ctypes.byref(error_base)):
        xlib.XCloseDisplay(dpy)
        return

    xext.XShapeCombineRectangles.restype = None
    xext.XShapeCombineRectangles.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(_XRectangle), ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ]
    xlib.XFlush.argtypes = [ctypes.c_void_p]
    xlib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]

    SHAPE_BOUNDING, SHAPE_SET, UNSORTED = 0, 0, 0

    # A protocol error here (this connection's window racing a resize
    # against its own destruction, say) is never worth taking the app down
    # over - see install_scoped_x_error_handler()'s docstring for why this
    # can't just be a blanket "ignore everything" handler. `restore` is
    # deliberately unused - `dpy` here lives indefinitely (future
    # <Configure> events keep using it), so the chain has to stay in
    # place for as long as `handler` itself does, below.
    handler, _restore = install_scoped_x_error_handler(xlib, dpy)

    def _apply(event=None):
        try:
            window.update_idletasks()
            w = window.winfo_width()
            h = window.winfo_height()
        except tk.TclError:
            # Window's already been destroyed - nothing left to shape.
            return
        r = max(0, min(radius, w // 2, h // 2))
        if r <= 0 or w <= 0 or h <= 0:
            return
        # One full-width band for everything above the rounded strip,
        # then one 1px-tall rectangle per row of the bottom `r` rows,
        # each inset by how far a circle of that radius would be inset
        # at that row - the standard rectangle-list approximation the X
        # Shape extension itself expects (there's no "just give me a
        # radius" call). Row i=0 is the TOP of the band (y = h - r,
        # flush with the straight wall above it, so inset 0/full width)
        # and inset grows toward the bottom-most row (i = r - 1, nearest
        # the flat bottom edge) - getting this backwards draws a corner
        # that flares outward at the bottom instead of curving inward.
        rects = [_XRectangle(0, 0, w, h - r)]
        for i in range(r):
            inset = r - int((r * r - i * i) ** 0.5)
            row_w = max(0, w - 2 * inset)
            rects.append(_XRectangle(inset, h - r + i, row_w, 1))
        rect_array = (_XRectangle * len(rects))(*rects)
        try:
            win_id = window.winfo_id()
        except tk.TclError:
            return
        xext.XShapeCombineRectangles(
            dpy, win_id, SHAPE_BOUNDING, 0, 0,
            rect_array, len(rects), SHAPE_SET, UNSORTED,
        )
        xlib.XSync(dpy, 0)

    window.bind("<Configure>", _apply, add="+")
    window.after(50, _apply)
    # Kept alive on the window itself - both the ctypes callback and the
    # Display connection need to outlive this function call (the
    # callback can be invoked by libX11 at any later point, and a
    # garbage-collected CFUNCTYPE object is a use-after-free from C's
    # side), for as long as <Configure> can still fire.
    window._nassie_corner_handler = handler
    window._nassie_corner_display = dpy
