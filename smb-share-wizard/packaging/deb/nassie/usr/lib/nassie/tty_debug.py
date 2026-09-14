"""Timestamped diagnostics for the TUI-to-gui_qt handoff (tui.py's
_launch_gui_outside_curses()), written to a file instead of the console -
the whole point of this handoff is that it hands the controlling terminal
back and forth between curses and a subprocess, so a bare print() here
would itself perturb the exact thing being diagnosed (what's actually in
the tty's escape-sequence stream at each step) and could easily be lost
in - or mistaken for - the garbage this exists to explain.

Written for one specific live bug: on at least one real terminal, mouse-
motion and focus-event tracking (xterm DECSET modes 1000/1003/1004/1006)
end up enabled once curses exits, and every subsequent mouse move/focus
change gets echoed to the tty as raw escape bytes until something
explicitly turns them back off - see tui.py's
_reset_terminal_tracking_modes(). This file cannot itself observe whether
those modes are actually on (that's terminal-emulator-side state with no
termios/stty-visible equivalent - Python has no way to query it), so it
logs exactly when each relevant step happens instead: what TERM/
COLORTERM/TERM_PROGRAM this session reports, when curses hands off
control, when the reset sequences are sent (before AND after the
subprocess, per that function's own docstring on why both), and the
subprocess's own timing and exit status. Lining that log's timestamps up
against when a tester visually sees the garbage start is what actually
localizes which step is responsible, instead of guessing from one report.

Always on, not gated behind a flag or env var, same reasoning as
anim_debug.py: the volume is a dozen or so lines per "Launch Desktop UI"
use, never continuous, so it beats asking a tester to discover and set
some environment variable first. The file is truncated fresh on every
TUI launch (see _ensure_file()) - only the current session's sequence of
events is ever useful for the next report, and stale runs would just
make grepping the right one harder.
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
                         # stops retrying it on every single call for the
                         # rest of the process's life.


def _log_dir():
    # Same convention as anim_debug.py's _log_dir()/tour.py's
    # _first_run_marker_path() - a plain dotfile dir here since this is a
    # Linux-only bug (the TUI doesn't exist on the Windows build) and
    # doesn't need that module's Windows %APPDATA% branch.
    return os.path.join(os.path.expanduser("~"), ".config", "nassie")


def _ensure_file():
    global _file, _resolved_path, _open_failed
    if _file is not None or _open_failed:
        return _file
    resolved = os.path.join(_log_dir(), "tty_debug.log")
    _resolved_path = resolved
    try:
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        _file = open(resolved, "w", encoding="utf-8", buffering=1)
        _file.write(
            f"=== NASsie tty diagnostics - {time.strftime('%Y-%m-%d %H:%M:%S')} - "
            f"{platform.system()} {platform.release()} ===\n"
            f"TERM={os.environ.get('TERM')!r} "
            f"COLORTERM={os.environ.get('COLORTERM')!r} "
            f"TERM_PROGRAM={os.environ.get('TERM_PROGRAM')!r} "
            f"XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE')!r}\n"
        )
    except OSError:
        _open_failed = True
        _file = None
    return _file


def log(message):
    """Best-effort, never worth failing (or even slowing down) the actual
    handoff over - a write failure here just means this particular run
    goes undiagnosed, not a broken app."""
    f = _ensure_file()
    if f is None:
        return
    elapsed_ms = (time.perf_counter() - _start) * 1000
    try:
        f.write(f"[t+{elapsed_ms:9.2f}ms] {message}\n")
    except OSError:
        pass


def path():
    """Where the log actually landed (or would land) - printed once by
    the TUI when it launches gui_qt, so a tester can find it without
    already knowing this module's own conventions."""
    if _resolved_path is None:
        _ensure_file()
    return _resolved_path or os.path.join(_log_dir(), "tty_debug.log")
