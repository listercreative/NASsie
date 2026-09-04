"""Best-effort native corner rounding for the main window, and (Windows
only) turning off DWM's own transition animations for it.

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

set_transitions_suppressed() (below, Windows only) is the other half of
that: DWM tweens a top-level window's bounds across its own transition
duration on EVERY resize/move by default, repainting content as it
goes, regardless of how many (or how few) root.geometry() calls the
app itself makes to get there - confirmed live, screen-recorded on a
real Windows machine, that GUIWizard's panel-toggle glides
(_toggle_users_panel()/_toggle_log_panel()) still looked gradual and
choppy even after being reduced to one supposedly-atomic geometry()
call. That's DWM's own animation, layered on top of whatever the app
asked for - the app has no control over its timing or easing at all
unless it's told to stop entirely for a moment.

An earlier version of this tried exactly that: force transitions off
for the window's entire lifetime, in _round_windows() below,
permanently. Works, but is a genuinely bad trade globally - it also
kills the native minimize/restore animation for this window, all the
time, not just during NASsie's own panel glides - correctly rejected
live. set_transitions_suppressed() is the scoped version:
GUIWizard._animate_root_width() calls it (True) immediately before
its own geometry() loop and (False) immediately after, so DWM is only
ever told to stand down for the ~160ms NASsie's own glide is actually
running - native minimize/restore keeps its normal animation the rest
of the time.

_enable_composited_resize() (below, also Windows only, also called
from _round_windows()) is the OTHER standing Windows-only fix here,
and a different kind from either of the above: WS_EX_COMPOSITED is the
standard, decades-old Win32 answer to a top-level window with many
child controls each showing stale/uninitialized pixels for whatever a
resize just exposed, until every individual one gets around to
repainting - not something specific to Tk, DWM transitions, or this
app. Set once, permanently, at startup, unlike
set_transitions_suppressed()'s deliberately scoped toggle - this is a
standing instruction for how the window's whole child hierarchy gets
composited, not an animation-timing knob.
"""
from __future__ import annotations

import ctypes
import platform
import tkinter as tk


def apply(window, radius=8):
    """Best-effort - silently does nothing if the platform, the extension,
    or the shared library it needs isn't there. Never worth failing the
    app over a rounded corner.

    Returns a zero-argument callable the caller can invoke later to force
    an immediate reapplication (Linux only - a no-op elsewhere) - see
    _round_linux_bottom()'s own return value for why GUIWizard's own
    panel-toggle animations (_toggle_users_panel()/_toggle_log_panel())
    need this on top of the <Configure> binding already set up below."""
    system = platform.system()
    if system == "Windows":
        _round_windows(window)
    elif system == "Linux":
        return _round_linux_bottom(window, radius)
    # macOS: already rounded natively.
    return lambda: None


def _windows_dwm_handle(window):
    """(dwmapi, hwnd) for `window`'s real, OS-decorated top-level HWND, or
    None if either the DLLs or the window itself aren't resolvable (no
    dwmapi on this Windows version, or the Tk window's already gone) -
    every Windows-only caller below (_round_windows(),
    set_transitions_suppressed()) shares this exact lookup rather than
    each re-deriving it slightly differently."""
    try:
        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return None
    # winfo_id() is Tk's own child frame HWND, not the OS-decorated
    # top-level window DWM actually draws - GetParent() walks up to that
    # one. The same lookup ttkbootstrap/sv_ttk-style "set the titlebar
    # dark too" helpers use for the identical reason.
    try:
        hwnd = user32.GetParent(window.winfo_id())
    except tk.TclError:
        return None
    return dwmapi, hwnd


def _round_windows(window):
    handle = _windows_dwm_handle(window)
    if handle is None:
        return
    dwmapi, hwnd = handle
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
    _enable_composited_resize(hwnd)


def _enable_composited_resize(hwnd):
    """Sets WS_EX_COMPOSITED on the real top-level HWND - the standard,
    decades-old Win32 fix for exactly the class of bug
    GUIWizard._animate_root_width()'s own per-widget freezes (header
    logo, Path column stretch, the Users toggle button, GuiTour's
    highlight tracking, the Users panel's own scrim) were all reactive
    patches for: a top-level window with many child controls, each
    painting itself separately, shows stale or uninitialized (often
    black) pixels for whatever's newly exposed by a resize until every
    individual child gets around to repainting - reported live,
    screen-recorded, as a ghosted scrollbar and a black flash right at
    the Users panel's own growing edge. WS_EX_COMPOSITED tells DWM to
    draw the ENTIRE window hierarchy into one off-screen buffer and
    present it atomically instead - this is what that style exists for,
    not something specific to Tk or this app. Set once, permanently, at
    startup (unlike set_transitions_suppressed()'s own deliberately
    scoped toggle) - this isn't an animation-timing knob to turn on and
    off around a glide, it's a standing instruction for how this
    window's whole child hierarchy gets composited, period.

    Doesn't replace the per-widget freezes above - those also cut real,
    unnecessary REDRAW WORK (fewer Treeview column relayouts, fewer
    widget rebuilds), which is worth keeping regardless of whether this
    also fixes the visual artifact those redraws were causing."""
    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return
    GWL_EXSTYLE = -20
    WS_EX_COMPOSITED = 0x02000000
    try:
        user32.GetWindowLongPtrW.restype = ctypes.c_longlong
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.restype = ctypes.c_longlong
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
        current = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, current | WS_EX_COMPOSITED)
    except (AttributeError, OSError):
        pass


def set_transitions_suppressed(window, suppressed):
    """Windows only, no-op elsewhere (and best-effort even there - see
    _windows_dwm_handle()). Toggles DWMWA_TRANSITIONS_FORCEDISABLED for
    `window`'s real top-level HWND - see this module's own docstring for
    why GUIWizard._animate_root_width() brackets its own geometry() loop
    with True right before and False right after, rather than this ever
    being set once for the window's whole lifetime: scoping it to just
    the moment NASsie's own glide is actually running is what keeps
    native minimize/restore animated the rest of the time.

    Returns True if DwmSetWindowAttribute actually reported success,
    False for every other case (no dwmapi, no HWND, or the call itself
    erroring) - _animate_root_width() feeds this straight to
    anim_debug.log()'s own hwnd_ok field, since a False here means
    Windows is left animating this resize no matter what the rest of
    this method's own bracketing does - worth knowing directly rather
    than inferring it from screen-recorded choppiness alone."""
    handle = _windows_dwm_handle(window)
    if handle is None:
        return False
    dwmapi, hwnd = handle
    DWMWA_TRANSITIONS_FORCEDISABLED = 3
    value = ctypes.c_int(1 if suppressed else 0)
    try:
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_TRANSITIONS_FORCEDISABLED,
            ctypes.byref(value), ctypes.sizeof(value),
        )
        return True
    except OSError:
        return False


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
        return lambda: None

    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    dpy = xlib.XOpenDisplay(None)
    if not dpy:
        return lambda: None

    xext.XShapeQueryExtension.restype = ctypes.c_int
    xext.XShapeQueryExtension.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
    ]
    event_base = ctypes.c_int()
    error_base = ctypes.c_int()
    if not xext.XShapeQueryExtension(dpy, ctypes.byref(event_base), ctypes.byref(error_base)):
        xlib.XCloseDisplay(dpy)
        return lambda: None

    xext.XShapeCombineRectangles.restype = None
    xext.XShapeCombineRectangles.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(_XRectangle), ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ]
    xlib.XFlush.argtypes = [ctypes.c_void_p]
    xlib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    xlib.XQueryTree.restype = ctypes.c_int
    xlib.XQueryTree.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint),
    ]
    xlib.XGetGeometry.restype = ctypes.c_int
    xlib.XGetGeometry.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
    ]
    xlib.XFree.argtypes = [ctypes.c_void_p]

    SHAPE_BOUNDING, SHAPE_SET, UNSORTED = 0, 0, 0

    # A protocol error here (this connection's window racing a resize
    # against its own destruction, say) is never worth taking the app down
    # over - see install_scoped_x_error_handler()'s docstring for why this
    # can't just be a blanket "ignore everything" handler. `restore` is
    # deliberately unused - `dpy` here lives indefinitely (future
    # <Configure> events keep using it), so the chain has to stay in
    # place for as long as `handler` itself does, below.
    handler, _restore = install_scoped_x_error_handler(xlib, dpy)

    def _target_window(client_id):
        # Shaping the Tk CLIENT window itself (winfo_id()) only rounds
        # its own corners - fine when that client IS the whole visible
        # window (an overrideredirect()'d Toplevel with no WM
        # decorations, e.g. this app's own dialogs/callouts), but root
        # here keeps its native titlebar (see git history - the old
        # custom linux_titlebar.py this replaced ran overrideredirect
        # instead). A reparenting WM (Mutter included) wraps a decorated
        # window in its OWN separate frame window (confirmed live via
        # `xwininfo -root -tree`: a "mutter-x11-frames" window containing
        # the "NASsie" client as its child, with the client inset by the
        # frame's own titlebar/border on every side, bottom included) -
        # THAT frame is what the user actually sees the square-cornered
        # bottom edge of, since it extends past the client on every side.
        # Shaping the client alone rounds a corner that then sits
        # entirely inside the frame's own unshaped border, invisible
        # regardless of how correct the math is - reported live ("clearly
        # we aren't rounding the bottom"). XQueryTree's own parent is
        # that frame when one exists; falls back to the client id itself
        # (the pre-existing behavior) when the parent IS the root window
        # - no reparenting, e.g. a WM that isn't compositing this way, or
        # none running at all.
        root_ret = ctypes.c_ulong()
        parent_ret = ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        nchildren = ctypes.c_uint()
        ok = xlib.XQueryTree(
            dpy, client_id, ctypes.byref(root_ret), ctypes.byref(parent_ret),
            ctypes.byref(children), ctypes.byref(nchildren),
        )
        if children:
            xlib.XFree(children)
        if not ok or parent_ret.value in (0, root_ret.value):
            return client_id
        return parent_ret.value

    def _apply(event=None):
        try:
            window.update_idletasks()
            client_id = window.winfo_id()
        except tk.TclError:
            # Window's already been destroyed - nothing left to shape.
            return
        target_id = _target_window(client_id)
        # The TARGET window's own live geometry, not window.winfo_width()/
        # height() - those report the Tk CLIENT's size, which is smaller
        # than the WM frame being shaped here whenever one was found
        # above (inset by the frame's own titlebar/border on every side).
        root_ret = ctypes.c_ulong()
        x = ctypes.c_int()
        y = ctypes.c_int()
        w = ctypes.c_uint()
        h = ctypes.c_uint()
        border_width = ctypes.c_uint()
        depth = ctypes.c_uint()
        if not xlib.XGetGeometry(
            dpy, target_id, ctypes.byref(root_ret), ctypes.byref(x), ctypes.byref(y),
            ctypes.byref(w), ctypes.byref(h), ctypes.byref(border_width), ctypes.byref(depth),
        ):
            return
        w, h = w.value, h.value
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
        xext.XShapeCombineRectangles(
            dpy, target_id, SHAPE_BOUNDING, 0, 0,
            rect_array, len(rects), SHAPE_SET, UNSORTED,
        )
        xlib.XSync(dpy, 0)

    window.bind("<Configure>", _apply, add="+")
    window.after(50, _apply)
    # A second, later reapplication on top of the <Configure> binding
    # above and the caller's own on-demand reapply() (see GUIWizard's
    # _toggle_users_panel()/_toggle_log_panel(), which call the returned
    # function once their own resize animation's on_complete fires) -
    # <Configure> can fire mid-resize, before the WM's own asynchronous
    # redraw of its native titlebar/decorations for the NEW size has
    # actually caught up, and that redraw can clobber whatever shape was
    # already set. 250ms after the initial apply() call gives that a
    # real chance to settle first.
    window.after(250, _apply)
    # Kept alive on the window itself - both the ctypes callback and the
    # Display connection need to outlive this function call (the
    # callback can be invoked by libX11 at any later point, and a
    # garbage-collected CFUNCTYPE object is a use-after-free from C's
    # side), for as long as <Configure> can still fire.
    window._nassie_corner_handler = handler
    window._nassie_corner_display = dpy
    return _apply
