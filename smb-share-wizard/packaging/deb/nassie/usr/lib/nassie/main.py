import sys
import os

if __name__ == "__main__":
    # A PyInstaller --windowed build has no console, so sys.stdout/stderr are
    # None; core.py logs via bare print(), which would crash without this.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


    def report_failure(message):
        # Used by run_gui_qt() below - can fail on the one platform
        # (--windowed Windows) where a plain print() reaches no one,
        # since stdout/stderr are devnull (see the top of this file).
        print(message, file=sys.stderr)
        if os.name == "nt":
            # A native MessageBoxW needs no GUI toolkit of its own to
            # already be working (PySide6 may be exactly what just
            # failed to import), so it can't fail the same way the
            # thing it's reporting on just did.
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, message, "NASsie", 0x10)
            except Exception:
                pass

    def run_tui_then_basic():
        try:
            from tui import TUIWizard
            TUIWizard().run()
        except Exception as e:
            print(f"Terminal UI unavailable ({e}); falling back to the basic prompt-based wizard.")
            from cli import CLIWizard
            CLIWizard().start()

    def run_gui_qt():
        # The Windows production default (see the os.name == "nt"
        # branch below) and reachable directly via --gui-qt on every
        # platform - the only desktop GUI now that Tk (gui.py/tour.py/
        # nassie_ttk/window_corners.py/anim_debug.py) has been removed
        # entirely (see the migration plan's Phase 7 and gui_qt.py's own
        # module docstring for the phase history that led here). No
        # fallback on failure - a crash here should be loud and visible,
        # not silently swallowed into a different UI.
        #
        # Runs as a subprocess (launch_gui_qt()) rather than importing and
        # calling gui_qt.run() directly - a native crash in PySide6's
        # compiled bindings kills the process it runs in with no Python
        # traceback at all, so in-process that used to take this whole
        # invocation down silently. Out of process, the exit status is
        # inspectable and reportable instead.
        from core import launch_gui_qt, describe_gui_qt_failure
        try:
            result = launch_gui_qt()
        except OSError as e:
            # subprocess.run() itself failing to spawn the child at all -
            # antivirus quarantine, a corrupted/missing self-exe, a
            # permissions issue - is a different failure mode from the
            # child spawning and then crashing (what describe_gui_qt_
            # failure() below handles). Without this, it would propagate
            # all the way up through main.py's own top-level try/except
            # (which only catches KeyboardInterrupt) and die completely
            # silently on a --windowed build - exactly the failure mode
            # report_failure() exists to prevent. Matches core.py's
            # _elevated_relaunch()/_elevated_relaunch_capturing(), which
            # now catch this same OSError spawn-failure case too.
            report_failure(f"Could not launch the desktop UI: {e}")
            sys.exit(1)
        failure = describe_gui_qt_failure(result)
        if failure:
            report_failure(failure)
        sys.exit(result.returncode)

    def run_basic_cli():
        from cli import CLIWizard
        CLIWizard().start()

    def print_help():
        print("""NASsie - cross-platform SMB share configuration wizard

Usage:
  nassie                Launch the terminal UI (TUI) - the Qt GUI on Windows
  nassie --gui-qt        Launch the desktop UI (PySide6/Qt)
  nassie --cli           Launch the basic prompt-based wizard
  nassie --help, -h      Show this help message and exit""")

    # Each of these is a relaunch target for SMBWizard._elevated_relaunch():
    # the flag matches what elevate_and_*() passed as arg_flag, and the
    # value is the *_from_file() entry point that consumes the temp JSON
    # payload written before elevation. Internal only - not part of the
    # user-facing flag set validated below.
    RELAUNCH_HANDLERS = {
        "--apply": "apply_from_file",
        "--delete-share": "delete_share_from_file",
        "--create-user": "create_user_from_file",
        "--add-user": "add_user_to_share_from_file",
        "--change-access": "change_access_from_file",
        "--change-group-access": "change_group_access_from_file",
        "--revoke-user": "revoke_share_access_from_file",
        "--delete-user": "delete_user_from_file",
        "--delete-group": "delete_group_from_file",
        "--assign-group": "assign_user_to_group_from_file",
        "--revoke-group": "revoke_group_membership_from_file",
        "--create-group": "create_group_from_file",
        "--assign-group-share": "assign_group_to_share_from_file",
        "--unassign-group-share": "unassign_group_from_share_from_file",
    }

    try:
        if len(sys.argv) >= 2 and sys.argv[1] == "--uninstall-delete-tour-marker":
            # Internal only - invoked by the MSI's uninstall sequence as a
            # separate, deliberately headless immediate custom action
            # (see nassie.wxs and core.py's delete_tour_marker_windows()
            # for why this isn't folded into --uninstall-folder-prompt
            # below, despite both being immediate/interactive-session
            # actions for the same %APPDATA% reason). Not user-facing.
            from core import SMBWizard
            SMBWizard.delete_tour_marker_windows()
        elif len(sys.argv) >= 2 and sys.argv[1] == "--uninstall-folder-prompt":
            # Internal only - invoked by the MSI's uninstall sequence as an
            # immediate (interactive-session) custom action, before
            # --uninstall-cleanup below runs deferred as SYSTEM and can't
            # show UI at all. Not user-facing.
            from core import SMBWizard
            SMBWizard.prompt_uninstall_folders_windows()
        elif len(sys.argv) >= 2 and sys.argv[1] == "--uninstall-cleanup":
            # Internal only - invoked by the MSI's uninstall custom action
            # (see nassie.wxs), not user-facing. Takes no payload file,
            # unlike the elevation relaunch handlers below.
            from core import SMBWizard
            SMBWizard.uninstall_cleanup_windows()
        elif len(sys.argv) >= 2 and sys.argv[1] == "--create-desktop-shortcut":
            # Internal only - invoked by the MSI's "Add desktop shortcut"
            # checkbox on the final install screen (see nassie.wxs). Not
            # user-facing.
            from core import SMBWizard
            SMBWizard.create_desktop_shortcut_windows()
        elif len(sys.argv) >= 2 and sys.argv[1] == "--gui-qt-child":
            # Internal only - the actual in-process PySide6 entry point,
            # reached only via launch_gui_qt()'s subprocess relaunch (see
            # core.py) on a frozen build, where sys.executable is this
            # same app rather than a general-purpose interpreter that
            # could be handed gui_qt.py's path directly (the unfrozen/
            # source-install path core.py falls back to instead). Kept
            # separate from run_gui_qt() (the public --gui-qt flag's own
            # handler, which is what SPAWNS this) so relaunching doesn't
            # recurse - this branch calls gui_qt.run() directly, with
            # nothing further to relaunch. Not user-facing.
            from gui_qt import run
            run()
        elif len(sys.argv) >= 3 and sys.argv[1] in RELAUNCH_HANDLERS:
            from core import SMBWizard
            getattr(SMBWizard, RELAUNCH_HANDLERS[sys.argv[1]])(sys.argv[2])
        else:
            args = sys.argv[1:]
            recognized = {"--gui-qt", "--cli", "--help", "-h"}
            unknown = [a for a in args if a not in recognized]
            if unknown:
                print(f"Unknown option: {unknown[0]}", file=sys.stderr)
                print("See 'nassie --help' for usage.", file=sys.stderr)
                sys.exit(2)

            if "--help" in args or "-h" in args:
                print_help()
            elif "--gui-qt" in args:
                run_gui_qt()
            elif "--cli" in args:
                run_basic_cli()
            elif os.name == "nt":
                # A --windowed PyInstaller build (how NASsie.exe is built)
                # has no console to attach a curses TUI to, whether it was
                # launched by double-click or from a terminal - GUI is the
                # only usable default here until/unless a separate
                # console-subsystem Windows build exists. The Qt rewrite -
                # Phases 1-6 are done (app shell, panels/animation,
                # dialogs, tour, packaging - see gui_qt.py's own module
                # docstring and the migration plan) and this is the
                # actual point of the whole rewrite: the panel-animation
                # work that started it hit a real structural ceiling in
                # Tk (no compositor-backed resize primitive on Windows).
                # Tk itself (gui.py/tour.py/nassie_ttk/window_corners.py/
                # anim_debug.py) has since been removed entirely - see
                # the migration plan's Phase 7.
                run_gui_qt()
            else:
                run_tui_then_basic()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
