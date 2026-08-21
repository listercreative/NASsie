import sys
import os

if __name__ == "__main__":
    # A PyInstaller --windowed build has no console, so sys.stdout/stderr are
    # None; core.py logs via bare print(), which would crash without this.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


    def run_tui_then_basic():
        try:
            from tui import TUIWizard
            TUIWizard().run()
        except Exception as e:
            print(f"Terminal UI unavailable ({e}); falling back to the basic prompt-based wizard.")
            from cli import CLIWizard
            CLIWizard().start()

    def run_gui_then_tui():
        try:
            from gui import GUIWizard
            GUIWizard().run()
        except Exception as e:
            print(f"GUI unavailable ({e}); falling back to the terminal wizard.")
            run_tui_then_basic()

    def run_basic_cli():
        from cli import CLIWizard
        CLIWizard().start()

    def print_help():
        print("""Kelpie - cross-platform SMB share configuration wizard

Usage:
  kelpie                Launch the terminal UI (TUI)
  kelpie --gui           Launch the graphical desktop UI
  kelpie --cli           Launch the basic prompt-based wizard
  kelpie --help, -h      Show this help message and exit""")

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
        if len(sys.argv) >= 2 and sys.argv[1] == "--uninstall-folder-prompt":
            # Internal only - invoked by the MSI's uninstall sequence as an
            # immediate (interactive-session) custom action, before
            # --uninstall-cleanup below runs deferred as SYSTEM and can't
            # show UI at all. Not user-facing.
            from core import SMBWizard
            SMBWizard.prompt_uninstall_folders_windows()
        elif len(sys.argv) >= 2 and sys.argv[1] == "--uninstall-cleanup":
            # Internal only - invoked by the MSI's uninstall custom action
            # (see kelpie.wxs), not user-facing. Takes no payload file,
            # unlike the elevation relaunch handlers below.
            from core import SMBWizard
            SMBWizard.uninstall_cleanup_windows()
        elif len(sys.argv) >= 3 and sys.argv[1] in RELAUNCH_HANDLERS:
            from core import SMBWizard
            getattr(SMBWizard, RELAUNCH_HANDLERS[sys.argv[1]])(sys.argv[2])
        else:
            args = sys.argv[1:]
            recognized = {"--gui", "--cli", "--help", "-h"}
            unknown = [a for a in args if a not in recognized]
            if unknown:
                print(f"Unknown option: {unknown[0]}", file=sys.stderr)
                print("See 'kelpie --help' for usage.", file=sys.stderr)
                sys.exit(2)

            if "--help" in args or "-h" in args:
                print_help()
            elif "--gui" in args:
                run_gui_then_tui()
            elif "--cli" in args:
                run_basic_cli()
            elif os.name == "nt":
                # A --windowed PyInstaller build (how Kelpie.exe is built)
                # has no console to attach a curses TUI to, whether it was
                # launched by double-click or from a terminal - GUI is the
                # only usable default here until/unless a separate
                # console-subsystem Windows build exists.
                run_gui_then_tui()
            else:
                run_tui_then_basic()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
