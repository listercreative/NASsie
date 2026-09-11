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
The file is truncated fresh on every launch (see _ensure_file()) rather
than growing forever across a long testing session - only the CURRENT
session's sequence of toggles is ever useful for diagnosing the next
one, and an ever-growing file left over from a prior session would just
make grepping the right one harder.

log() keeps ONE file handle open for the whole process, rather than a
fresh open()/write()/close() per call - see its own docstring for why
that distinction turned out to matter a lot here specifically, not just
as a micro-optimization.
"""
from __future__ import annotations

import os
import platform
import time

_start = time.perf_counter()
_file = None            # The persistent, kept-open handle, once opened
                         # successfully - see _ensure_file().
_resolved_path = None    # The log path, resolved once either way (used
                         # by path() even if opening it actually failed).
_open_failed = False     # True after one failed open attempt, so log()
                         # stops retrying it on every single call for
                         # the rest of the process's life.


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


def _ensure_file():
    """Opens the log file ONCE and keeps that handle for every later
    log() call to reuse, instead of a fresh open("a")/close() every
    single time (the original design here). That original version
    meant _animate_root_width()'s own 10-step glide - the exact thing
    this module exists to diagnose - was doing upwards of twenty real
    file-open operations, on the main thread, inside the same ~160ms
    window being measured: confirmed live as a real, self-inflicted
    contributor to "the whole window repainting" reports, not just a
    measurement tool watching from the sidelines. Windows specifically
    charges more for this than Linux does per open() - antivirus real-
    time scanning commonly hooks CreateFile itself, not just the
    write() that follows it - so this was likely distorting, maybe even
    partly CAUSING, some of what earlier anim_debug.log sessions
    appeared to show. buffering=1 (line-buffered): every write() still
    reaches disk immediately (no risk of losing the last few lines if
    the app's closed uncleanly), it just does that without the
    open/close cycle around each individual line."""
    global _file, _resolved_path, _open_failed
    if _file is not None or _open_failed:
        return _file
    resolved = os.path.join(_log_dir(), "animation_debug.log")
    _resolved_path = resolved
    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        _file = open(resolved, "w", encoding="utf-8", buffering=1)
        _file.write(f"=== NASsie animation diagnostics - {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"- {platform.system()} {platform.release()} ===\n")
    except OSError:
        _open_failed = True
        _file = None
    return _file


def log(message):
    """Best-effort, never worth failing (or even slowing down) the
    animation itself over - a write failure here just means this
    particular run goes undiagnosed, not a broken app."""
    f = _ensure_file()
    if f is None:
        return
    elapsed_ms = (time.perf_counter() - _start) * 1000
    try:
        f.write(f"[t+{elapsed_ms:9.2f}ms] {message}\n")
    except OSError:
        pass


def path():
    """Where the log actually landed (or would land) - GUIWizard surfaces
    this once at startup via _append_log() so a tester can find it
    without having to already know this module's own conventions."""
    if _resolved_path is None:
        _ensure_file()
    return _resolved_path or os.path.join(_log_dir(), "animation_debug.log")
