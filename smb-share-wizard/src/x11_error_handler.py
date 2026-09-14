"""Pure ctypes/X11 plumbing, extracted out of window_corners.py so it can
be imported without pulling in tkinter at all.

_XRectangle and install_scoped_x_error_handler() were already, per
gui_qt.py's own comment before this split, "pure ctypes/X11 plumbing, no
Tk involved despite living in window_corners.py" - reused as-is by that
file's own _round_linux_bottom() (a genuine Qt port, working on QWidget,
not a Tk window at all) rather than redefined. But window_corners.py's
OTHER functions (apply(), _round_windows(), the Tk-specific parts of its
own _round_linux_bottom()) do need tkinter, via a top-level `import
tkinter as tk` - and Python has no way to import "only part of" a
module, so gui_qt.py importing just these two names still pulled all of
tkinter in regardless of which names it actually used. Confirmed live as
a real, non-trivial contributor to a Windows PyInstaller build whose
whole point was to ship Qt as the default and not bundle Tk at all - see
build.ps1's own history for the size cost this (and the equivalent
tour.py/tour_state.py split) actually had.

window_corners.py still re-exports these same names for its own callers
(gui.py's `import window_corners` usage) - this split is invisible to
every existing caller except gui_qt.py, which now imports from here
directly instead of through window_corners.py.
"""
import ctypes


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
