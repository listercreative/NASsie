"""Timestamped diagnostics for the panel-toggle glide (_animate_root_width()/
_animate_users_scrim()/_animate_log_scrim() in gui.py), written to a file
instead of the console - the Windows build is PyInstaller --windowed (see
packaging/windows/build.ps1), which has no console attached at all, so a
bare print() here would just silently vanish rather than land anywhere a
tester could actually see it. A screen recording only shows what the
compositor eventually painted, not what the app itself asked for and
when - this is for telling those apart: was a given frame's on-screen
jankiness because the app's own step timing was uneven, or because
Windows/DWM sat on a call the app made right on schedule.

Always on, not gated behind a flag or env var - the volume is tiny (a
few dozen lines per toggle, only while a toggle is actually running,
never continuously), so keeping this always on beats asking a tester to
first discover, then set, some environment variable before every run.
The file is truncated fresh on every launch (see _ensure_open()) rather
than growing forever across a long testing session - only the CURRENT
session's sequence of toggles is ever useful for diagnosing the next
one, and an ever-growing file left over from a prior session would just
make grepping the right one harder.
"""
from __future__ import annotations

import os
import platform
import time

_start = time.perf_counter()
_path = None  # Resolved once, lazily - see _ensure_open(). "" means
              # resolution was already tried and failed (no point
              # retrying every single log() call for the rest of the
              # process's life).


def _log_dir():
    # Same convention tour.py's _first_run_marker_path() uses for its
    # own first-run marker - %APPDATA%\NASsie on Windows (where this
    # actually matters - see this module's own docstring), falling back
    # to a plain dotfile dir elsewhere since this file is a developer
    # diagnostic, not a real per-platform feature, and doesn't need
    # tour.py's own sudo-user home resolution to go with it.
    if platform.system() == "Windows" and os.environ.get("APPDATA"):
        return os.path.join(os.environ["APPDATA"], "NASsie")
    return os.path.join(os.path.expanduser("~"), ".config", "nassie")


def _ensure_open():
    global _path
    if _path is not None:
        return _path
    path = os.path.join(_log_dir(), "animation_debug.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"=== NASsie animation diagnostics - {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"- {platform.system()} {platform.release()} ===\n")
    except OSError:
        path = ""
    _path = path
    return _path


def log(message):
    """Best-effort, never worth failing (or even slowing down) the
    animation itself over - a write failure here just means this
    particular run goes undiagnosed, not a broken app."""
    path = _ensure_open()
    if not path:
        return
    elapsed_ms = (time.perf_counter() - _start) * 1000
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[t+{elapsed_ms:9.2f}ms] {message}\n")
    except OSError:
        pass


def path():
    """Where the log actually landed (or would land) - GUIWizard surfaces
    this once at startup via _append_log() so a tester can find it
    without having to already know this module's own conventions."""
    resolved = _ensure_open()
    return resolved or os.path.join(_log_dir(), "animation_debug.log")
