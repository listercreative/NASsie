"""First-run tour progress persistence - extracted out of tour.py so it
can be imported without pulling in tkinter at all.

Originally lived directly in tour.py alongside GuiTour and its other
Tk-specific UI classes. tour_qt.py already only ever needed these
particular functions (its own docstring says as much: "zero tkinter
dependency at all"), but importing them meant importing the whole of
tour.py - and tour.py's own top-level `import tkinter as tk` / `from
tkinter import ttk` came along for the ride regardless of which names
were actually used, since Python has no way to import "only part of" a
module. On Windows specifically, that meant gui_qt.py's own import
chain (gui_qt -> tour_qt -> tour -> tkinter) pulled the entire Tcl/Tk
runtime into a PyInstaller build whose whole point was to ship Qt as
the default and NOT bundle Tk at all - confirmed live as a real,
non-trivial contributor to NASsie.exe's size. Splitting this out is
what actually lets packaging/windows/build.ps1 exclude tour.py (and
gui.py, nassie_ttk, anim_debug) from that build without breaking
gui_qt.py's own tour.

tour.py still re-exports these same names (`from tour_state import
...`) so gui.py's own `from tour import GuiTour, tour_state, ...`
keeps working unchanged - this split is invisible to every existing
caller except tour_qt.py/gui_qt.py, which now import from here
directly instead of through tour.py.
"""
import os
import platform


def _real_home():
    # Root via sudo - notably the postinst-launched wizard, which always
    # runs as root regardless of who ran `apt install` - has HOME=/root.
    # For most users that IS their first-ever look at NASsie, so the
    # marker has to land in the real invoking user's home instead: written
    # against /root, it'd not just miss recording that user's actual first
    # look, it'd permanently hide the tour from every later real launch
    # too, since tour_state() would then only ever check root's own
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


def tour_state():
    # "new": never started - auto-start it, the normal first-run case.
    # "interrupted": started but neither finished nor explicitly skipped
    # (the file exists but its content isn't "completed") - the app
    # closed mid-tour (crash, force-quit, or just clicking NASsie's own
    # window close button) before mark_tour_completed() ever ran. Worth
    # distinguishing from "new" so GUIWizard can OFFER to pick it back up
    # instead of either silently never showing it again (this used to
    # write "seen" the instant the tour started, before the user had done
    # anything - a crash one step in meant never seeing it again) or
    # unconditionally restarting it every single launch until it happens
    # to be finished (which would get old fast for anyone who skips it
    # more than once on purpose).
    # "completed": finished, or explicitly skipped - never show again.
    path = _first_run_marker_path()
    if not os.path.exists(path):
        return "new"
    try:
        with open(path) as f:
            content = f.read().strip()
    except OSError:
        return "new"
    return "completed" if content == "completed" else "interrupted"


def mark_tour_started(index=0):
    # index - the current step (see GuiTour.index) - rides along in the
    # same "started" marker as "started:<index>", rather than a separate
    # file, so there's only ever one source of truth for "is a tour in
    # flight, and if so where". Read back by tour_progress_index() on the
    # next launch (see GUIWizard._offer_tour_resume()) to pick the
    # walkthrough back up close to where it left off instead of forcing a
    # full restart from step one.
    _write_tour_state(f"started:{index}")


def mark_tour_completed():
    _write_tour_state("completed")


def tour_progress_index():
    # The furthest step index a previous, interrupted run reached (see
    # mark_tour_started()'s own comment) - 0 if there's no marker, it's
    # already "completed", or it predates this field ever existing (a
    # bare "started" with no ":<index>", from an older NASsie version -
    # falls back to a plain restart from step one, same as before this
    # existed, rather than erroring on it).
    path = _first_run_marker_path()
    try:
        with open(path) as f:
            content = f.read().strip()
    except OSError:
        return 0
    if not content.startswith("started:"):
        return 0
    try:
        return max(0, int(content.split(":", 1)[1]))
    except ValueError:
        return 0


def _write_tour_state(state):
    path = _first_run_marker_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    with open(path, "w") as f:
        f.write(state)
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
