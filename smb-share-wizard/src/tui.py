import contextlib
import curses
import io
import os
import textwrap
import threading

from core import SMBWizard, QR_PASSWORD_RESET_NOTE

# Big block-letter "KELPIE" wordmark shown above the horse banner - width
# matched to the logo (65 vs 63 columns) so they read as one unit.
KELPIE_TITLE = [
    '88      a8P  88888888888 88          88888888ba  88 88888888888  ',
    '88    ,88\'   88          88          88      "8b 88 88           ',
    '88  ,88"     88          88          88      ,8P 88 88           ',
    "88,d88'      88aaaaa     88          88aaaaaa8P' 88 88aaaaa      ",
    '8888"88,     88"""""     88          88""""""\'   88 88"""""      ',
    '88P   Y8b    88          88          88          88 88           ',
    '88     "88,  88          88          88          88 88           ',
    '88       Y8b 88888888888 88888888888 88          88 88888888888  ',
]

KELPIE_LOGO = [
    '                                                 -# :.',
    '                                           .:..:*@@=#',
    '                                        -*%**%@@@@@@@+',
    '                                      *@@*+@@@@@@@@@+%@.',
    '                                     %@@+*@@@@@+@@@@@@@@+',
    '                                    *@@%=@@@@@@*+%@%%@@@@%=',
    '                                    @@@=%@@@@@@+    .-+@@@*',
    '                     .=*+=-.       -@@@-@@@@@@@=       .:',
    '                       :@@%%#*-   .+*#@-@@@@@@@@.',
    '                        @@%%@#*#+     .-#@@@@@@@@.',
    '                      =%@%@@#*@#+@=     +@@@@@@@@@.',
    '                      #%#*==**-*+:**-.--#@@@@@@@@@%',
    '                 ...-+#%@@@@@@@@@@@@@@%#@@@@@@@@@@@-',
    '             :+*++#@@@%*++=-+*%@@%#***%@@@@@@@@@@@@=',
    '  .....:-+*%@#=:**+======------====+**=--+#@@@%#***+*##*+++=-:.',
    ' .:-+*****+===++==---------===-..:--------=++*###****+===:.',
    '        ..::---:::::::--==========+++++**#*+++++++++--:',
    '                    ..:::--========-::..:-==++===-:',
]

# Per-cell xterm-256 color index, sampled from the real logo's RGB.
# Used when the terminal supports 256 colors.
KELPIE_LOGO_COLORS_256 = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 72, 72, 0, 24, 60],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 72, 72, 72, 66, 72, 65, 24],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 72, 72, 72, 72, 72, 71, 71, 72, 72, 72],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 72, 72, 72, 72, 66, 29, 72, 72, 72, 65, 65, 65, 72, 72],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 72, 72, 72, 66, 24, 30, 66, 66, 72, 72, 72, 66, 72, 72, 72, 72],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 72, 72, 66, 24, 66, 30, 66, 24, 66, 66, 66, 30, 24, 30, 66, 72, 66, 66],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 66, 66, 24, 24, 66, 66, 66, 0, 0, 0, 0, 60, 24, 24, 24, 66, 66, 66],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 66, 66, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 66, 66, 66, 24, 30, 66, 71, 66, 0, 0, 0, 0, 0, 0, 0, 59, 24],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 66, 66, 66, 0, 0, 0, 66, 66, 66, 66, 66, 66, 66, 66, 66, 24, 24, 66, 71, 71, 71],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 66, 66, 66, 66, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 24, 24, 30, 66, 71, 71, 71],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 0, 0, 0, 0, 0, 66, 66, 66, 30, 24, 24, 66, 71, 71, 107, 107],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 24, 24, 30, 66, 107, 107, 107],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 30, 24, 24, 24, 66, 107, 107, 107, 107],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 66, 66, 66, 66, 66, 66, 66, 24, 24, 24, 23, 59, 59, 59, 23, 23, 23, 24, 24, 24, 30, 66, 66, 66, 66, 66, 66, 66, 66, 30, 24, 24, 24, 66, 71, 107, 107, 107, 107],
    [0, 0, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 30, 23, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 60, 24, 24, 24, 23, 60, 59, 59, 23, 24, 24, 65, 65, 107, 107, 107, 107, 107, 107, 71, 65, 65, 65, 65, 65, 65, 65, 65, 65, 102],
    [0, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 72, 107, 107, 107, 107, 107, 107, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 65, 107, 107, 107, 71, 71, 71, 65, 65, 65, 66, 66, 66, 66, 66, 66, 66, 66, 66],
    [0, 0, 0, 0, 0, 0, 0, 0, 108, 107, 108, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 71, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 72, 107, 107, 107],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 107, 101, 65, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 101, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107, 107],
]

# Fallback for basic 8-/16-color terminals: 0/1 = dim/bold green
# (head+neck), 2/3 = dim/bold cyan (mane/tail/waves).
KELPIE_LOGO_COLORS = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 1, 1, 1, 2, 3, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 3, 1, 1, 3, 2, 2, 3, 3, 3, 2, 2, 2, 1, 3],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 3, 3, 3, 2, 2, 2, 2, 2, 3, 3, 3, 2, 3, 3, 3, 3],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 3, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 0, 0, 0, 0, 0, 0, 0, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 3, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1],
    [0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 1, 1, 1, 1, 1, 1, 1, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
]


class TUIWizard:
    """Curses-based terminal UI: arrow-key menus and a crude directory
    picker, for headless boxes with no display but a real TTY. Data
    collection happens entirely inside curses; the actual privileged
    apply step runs after curses tears down, so pkexec/sudo prompts and
    plain status prints behave normally."""

    def __init__(self):
        self.wizard = SMBWizard()
        self._color_ok = False
        self._color_mode = "mono"
        self._pair_256 = {}

    def run(self):
        os.environ.setdefault("ESCDELAY", "25")
        # _main returns after every menu action, since applying/deleting/etc.
        # may need an elevation prompt that can't happen while curses owns
        # the terminal. It returns an empty result only when the user chose
        # Exit or backed all the way out (Esc/q) - anything else means an
        # action needs to run outside curses, after which we re-enter the
        # menu instead of ending the whole program.
        while True:
            result = {}
            curses.wrapper(self._main, result)

            if not result:
                return

            share_data = result.get("share_data")
            if share_data:
                self._apply_outside_curses(share_data)
                input("\nPress Enter to return to the menu...")
                continue

            delete_action = result.get("delete_share")
            if delete_action:
                self._delete_outside_curses(delete_action["name"], delete_action.get("delete_folder", False))
                input("\nPress Enter to return to the menu...")
                continue

            add_user = result.get("add_user")
            if add_user:
                self._add_user_outside_curses(add_user)
                input("\nPress Enter to return to the menu...")
                continue

            ug_action = result.get("users_groups_action")
            if ug_action:
                self._users_groups_action_outside_curses(ug_action)
                input("\nPress Enter to return to the menu...")
                continue

            if result.get("launch_gui"):
                self._launch_gui_outside_curses()
                continue

    def _init_colors(self):
        self._color_mode = "mono"
        self._pair_256 = {}
        try:
            curses.start_color()
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                bg = curses.COLOR_BLACK

            if curses.COLORS >= 256:
                # One curses color pair per distinct xterm-256 color actually
                # used in the logo (~15), matched to the real PNG's RGB.
                used = sorted({v for row in KELPIE_LOGO_COLORS_256 for v in row if v})
                for pair_num, color_idx in enumerate(used, start=1):
                    if pair_num >= curses.COLOR_PAIRS:
                        break
                    curses.init_pair(pair_num, color_idx, bg)
                    self._pair_256[color_idx] = pair_num
                self._color_mode = "256"
            else:
                curses.init_pair(1, curses.COLOR_GREEN, bg)
                curses.init_pair(2, curses.COLOR_CYAN, bg)
                self._color_mode = "8"
            self._color_ok = True
        except curses.error:
            self._color_ok = False
            self._color_mode = "mono"

    def _banner_attr(self, color_idx_8, color_idx_256=None):
        if not self._color_ok:
            return curses.A_BOLD
        if self._color_mode == "256" and color_idx_256 is not None:
            pair = self._pair_256.get(color_idx_256)
            if pair is not None:
                return curses.color_pair(pair)
            return curses.A_BOLD
        pair = curses.color_pair(1 if color_idx_8 in (0, 1) else 2)
        return pair | curses.A_BOLD if color_idx_8 in (1, 3) else pair

    def _apply_outside_curses(self, share_data):
        self.wizard.share_name = share_data["name"]
        self.wizard.share_path = share_data["path"]
        self.wizard.users = share_data["users"]
        print(f"\n--- Applying configuration for '{share_data['name']}' ---")
        if self.wizard.has_admin_privileges():
            self.wizard.dispatch_execution()
        else:
            self.wizard.elevate_and_apply(share_data)
        # Checked against live share state, not a return value - neither
        # dispatch_execution() nor elevate_and_apply() report success
        # directly, and this works the same regardless of which path ran.
        if any(s['name'] == share_data['name'] for s in self.wizard.list_shares()):
            for user in share_data["users"]:
                self._offer_qr_code_plain(share_data["name"], user["username"], user["password"])

    def _delete_outside_curses(self, name, delete_folder=False):
        print(f"\n--- Removing share '{name}' ---")
        if self.wizard.remove_share(name, delete_folder):
            print(f"Removed share: {name}" + (" (folder deleted too)" if delete_folder else ""))
        else:
            print("Failed to remove share (or elevation was cancelled).")

    def _add_user_outside_curses(self, action):
        share, username = action["share"], action["username"]
        print(f"\n--- Adding '{username}' to share '{share}' ---")
        if self.wizard.grant_share_access(share, username, action["password"], action.get("read_only", False)):
            print(f"Added '{username}' to share '{share}'.")
            self._offer_qr_code_plain(share, username, action["password"])
        else:
            print("Failed to add user (or elevation was cancelled).")

    def _offer_qr_code_plain(self, share_name, username, password):
        # Used when the real terminal is already available (curses has
        # already torn down - see run()) - only ever offered right when a
        # password was just set, since Kelpie never persists plaintext
        # passwords and so has no way to regenerate this later.
        confirm = input(f"\nShow a QR code for '{username}' for easy external configuration? [y/N] ").strip().lower()
        if confirm not in ('y', 'yes'):
            return
        try:
            import qrcode
        except ImportError:
            print("The 'qrcode' package isn't installed.")
            return
        print("Contains this user's password in plain sight - only display it somewhere private.")
        print("(Compatible with the LockNAS app's bridge QR scanner.)\n")
        payload = self.wizard.build_locknas_qr_payload(share_name, username, password)
        qr = qrcode.QRCode()
        qr.add_data(payload)
        qr.make()
        qr.print_ascii(tty=True)
        input("\nPress Enter to continue...")

    def _offer_qr_codes_in_curses(self, stdscr, share_name, users):
        for user in users:
            choice = self._menu(stdscr, f"QR Code - {user['username']}", ["Show QR code", "Skip"])
            if choice == 0:
                self._show_qr_in_terminal(stdscr, share_name, user["username"], user["password"])

    def _show_qr_in_terminal(self, stdscr, share_name, username, password):
        # Rendering a QR code reliably needs the real terminal, not curses -
        # same "temporarily give up the terminal" pattern used elsewhere for
        # elevation prompts that need it, just scoped to endwin()/refresh()
        # instead of a full curses.wrapper teardown since nothing else here
        # needs to happen outside curses.
        curses.endwin()
        try:
            print(f"\n--- QR code for '{username}' ---")
            try:
                import qrcode
            except ImportError:
                print("The 'qrcode' package isn't installed.")
                input("\nPress Enter to continue...")
                return
            print("Contains this user's password in plain sight - only display it somewhere private.")
            print("(Compatible with the LockNAS app's bridge QR scanner.)\n")
            payload = self.wizard.build_locknas_qr_payload(share_name, username, password)
            qr = qrcode.QRCode()
            qr.add_data(payload)
            qr.make()
            qr.print_ascii(tty=True)
            input("\nPress Enter to continue...")
        finally:
            stdscr.refresh()

    def _users_groups_action_outside_curses(self, action):
        kind = action["action"]
        if kind == "create_user":
            print(f"\n--- Creating user '{action['username']}' ---")
            if self.wizard.add_user(action["username"], action["password"]):
                print(f"Created user '{action['username']}'.")
            else:
                print("Failed to create user (or elevation was cancelled).")
        elif kind == "revoke_access":
            print(f"\n--- Revoking '{action['username']}''s access to '{action['share']}' ---")
            if self.wizard.revoke_share_access(action["share"], action["username"]):
                print("Revoked access.")
            else:
                print("Failed to revoke access (or elevation was cancelled).")
        elif kind == "change_access":
            level = "read-only" if action["read_only"] else "read-write"
            print(f"\n--- Changing '{action['username']}''s access on '{action['share']}' to {level} ---")
            if self.wizard.change_share_access(action["share"], action["username"], action["read_only"]):
                print(f"'{action['username']}' is now {level} on '{action['share']}'.")
            else:
                print("Failed to change access level (or elevation was cancelled).")
        elif kind == "group_access":
            level = "read-only" if action["read_only"] else "read-write"
            print(f"\n--- Setting '{action['group']}''s members to {level} on '{action['share']}' ---")
            if self.wizard.change_group_access(action["group"], action["share"], action["read_only"]):
                print(f"'{action['group']}''s members are now {level} on '{action['share']}'.")
            else:
                print("Failed to change access level (or elevation was cancelled).")
        elif kind == "delete_user":
            print(f"\n--- Deleting user '{action['username']}' ---")
            if self.wizard.remove_user(action["username"]):
                print(f"Deleted user '{action['username']}'.")
            else:
                print("Failed to delete user (or elevation was cancelled).")
        elif kind == "delete_group":
            print(f"\n--- Deleting group '{action['name']}' ---")
            if self.wizard.remove_group(action["name"]):
                print(f"Deleted group '{action['name']}'.")
            else:
                print("Failed to delete group (or elevation was cancelled).")
        elif kind == "assign_group":
            print(f"\n--- Adding '{action['username']}' to group '{action['group']}' ---")
            if self.wizard.assign_user_to_group(action["username"], action["group"]):
                print(f"Added '{action['username']}' to group '{action['group']}'.")
            else:
                print("Failed to assign group (or elevation was cancelled).")
        elif kind == "revoke_group":
            print(f"\n--- Removing '{action['username']}' from group '{action['group']}' ---")
            if self.wizard.revoke_group_membership(action["username"], action["group"]):
                print(f"Removed '{action['username']}' from group '{action['group']}'.")
            else:
                print("Failed to remove from group (or elevation was cancelled).")
        elif kind == "create_group":
            print(f"\n--- Creating group '{action['name']}' ---")
            system_name = self.wizard.add_group(action["name"])
            if system_name:
                print(f"Created group '{system_name}'.")
            else:
                print("Failed to create group (or elevation was cancelled).")
        elif kind == "assign_group_share":
            level = "read-only" if action["read_only"] else "read-write"
            print(f"\n--- Assigning '{action['group']}' to '{action['share']}' ({level}) ---")
            if self.wizard.grant_group_share_access(action["group"], action["share"], action["read_only"]):
                print(f"Assigned '{action['group']}' to '{action['share']}'.")
            else:
                print("Failed to assign group to share (or elevation was cancelled).")
        elif kind == "unassign_group_share":
            print(f"\n--- Removing '{action['group']}''s access to '{action['share']}' ---")
            if self.wizard.revoke_group_share_access(action["group"], action["share"]):
                print(f"Removed '{action['group']}''s access to '{action['share']}'.")
            else:
                print("Failed to remove group's access (or elevation was cancelled).")

    def _launch_gui_outside_curses(self):
        try:
            from gui import GUIWizard
            GUIWizard().run()
        except Exception as e:
            print(f"Could not launch the desktop UI: {e}")

    # ---- in-curses privileged actions (already root, or elevation that
    # doesn't need the terminal - see elevation_needs_terminal()) ----

    def _apply_in_curses(self, share_data):
        self.wizard.share_name = share_data["name"]
        self.wizard.share_path = share_data["path"]
        self.wizard.users = share_data["users"]
        print(f"--- Applying configuration for '{share_data['name']}' ---")
        if self.wizard.has_admin_privileges():
            self.wizard.dispatch_execution()
        else:
            _, output = self.wizard._elevated_relaunch_capturing("--apply", share_data)
            print(output, end="")

    def _delete_in_curses(self, name, delete_folder=False):
        print(f"--- Removing share '{name}' ---")
        if self.wizard.has_admin_privileges():
            ok = self.wizard.delete_share(name, delete_folder)
        else:
            ok, output = self.wizard._elevated_relaunch_capturing(
                "--delete-share", {"name": name, "delete_folder": delete_folder}
            )
            print(output, end="")
        print(("Removed share: " + name + (" (folder deleted too)" if delete_folder else "")) if ok else "Failed to remove share.")

    def _add_user_in_curses(self, action):
        share, username, password = action["share"], action["username"], action["password"]
        read_only = action.get("read_only", False)
        print(f"--- Adding '{username}' to share '{share}' ---")
        if self.wizard.has_admin_privileges():
            ok = self.wizard.add_user_to_share(share, username, password, read_only)
        else:
            ok, output = self.wizard._elevated_relaunch_capturing(
                "--add-user", {"share": share, "username": username, "password": password, "read_only": read_only}
            )
            print(output, end="")
        print(f"Added '{username}' to share '{share}'." if ok else "Failed to add user.")

    def _users_groups_action_in_curses(self, action):
        kind = action["action"]
        if kind == "create_user":
            username, password = action["username"], action["password"]
            print(f"--- Creating user '{username}' ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.create_user(username, password)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing(
                    "--create-user", {"username": username, "password": password}
                )
                print(output, end="")
            print(f"Created user '{username}'." if ok else "Failed to create user.")
        elif kind == "change_access":
            share, username, read_only = action["share"], action["username"], action["read_only"]
            level = "read-only" if read_only else "read-write"
            print(f"--- Changing '{username}''s access on '{share}' to {level} ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.set_share_user_access(share, username, read_only)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing(
                    "--change-access", {"share": share, "username": username, "read_only": read_only}
                )
                print(output, end="")
            print(f"'{username}' is now {level} on '{share}'." if ok else "Failed to change access level.")
        elif kind == "group_access":
            group, share, read_only = action["group"], action["share"], action["read_only"]
            level = "read-only" if read_only else "read-write"
            print(f"--- Setting '{group}''s members to {level} on '{share}' ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.set_group_access_level(group, share, read_only)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing(
                    "--change-group-access", {"group": group, "share": share, "read_only": read_only}
                )
                print(output, end="")
            print(f"'{group}''s members are now {level} on '{share}'." if ok else "Failed to change access level.")
        elif kind == "revoke_access":
            share, username = action["share"], action["username"]
            print(f"--- Revoking '{username}''s access to '{share}' ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.remove_user_from_share(share, username)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing(
                    "--revoke-user", {"share": share, "username": username}
                )
                print(output, end="")
            print("Revoked access." if ok else "Failed to revoke access.")
        elif kind == "delete_user":
            username = action["username"]
            print(f"--- Deleting user '{username}' ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.delete_user(username)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing("--delete-user", {"username": username})
                print(output, end="")
            print(f"Deleted user '{username}'." if ok else "Failed to delete user.")
        elif kind == "delete_group":
            name = action["name"]
            print(f"--- Deleting group '{name}' ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.delete_group(name)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing("--delete-group", {"name": name})
                print(output, end="")
            print(f"Deleted group '{name}'." if ok else "Failed to delete group.")
        elif kind == "assign_group":
            username, group = action["username"], action["group"]
            print(f"--- Adding '{username}' to group '{group}' ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.add_user_to_group(username, group)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing(
                    "--assign-group", {"username": username, "group": group}
                )
                print(output, end="")
            print(f"Added '{username}' to group '{group}'." if ok else "Failed to assign group.")
        elif kind == "revoke_group":
            username, group = action["username"], action["group"]
            print(f"--- Removing '{username}' from group '{group}' ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.remove_user_from_group(username, group)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing(
                    "--revoke-group", {"username": username, "group": group}
                )
                print(output, end="")
            print(f"Removed '{username}' from group '{group}'." if ok else "Failed to remove from group.")
        elif kind == "create_group":
            name = action["name"]
            print(f"--- Creating group '{name}' ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.create_group(name)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing("--create-group", {"name": name})
                print(output, end="")
            print(f"Created group '{name}'." if ok else "Failed to create group.")
        elif kind == "assign_group_share":
            group, share, read_only = action["group"], action["share"], action["read_only"]
            level = "read-only" if read_only else "read-write"
            print(f"--- Assigning '{group}' to '{share}' ({level}) ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.assign_group_to_share(group, share, read_only)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing(
                    "--assign-group-share", {"group": group, "share": share, "read_only": read_only}
                )
                print(output, end="")
            print(f"Assigned '{group}' to '{share}'." if ok else "Failed to assign group to share.")
        elif kind == "unassign_group_share":
            group, share = action["group"], action["share"]
            print(f"--- Removing '{group}''s access to '{share}' ---")
            if self.wizard.has_admin_privileges():
                ok = self.wizard.unassign_group_from_share(group, share)
            else:
                ok, output = self.wizard._elevated_relaunch_capturing(
                    "--unassign-group-share", {"group": group, "share": share}
                )
                print(output, end="")
            print(f"Removed '{group}''s access to '{share}'." if ok else "Failed to remove group's access.")

    def _run_privileged_action(self, stdscr, busy_message, work_fn):
        # Runs work_fn() (which prints via stdout) in a background thread
        # while curses stays fully alive, showing a busy spinner, then
        # displays the captured output - only safe to use when
        # elevation_needs_terminal() is False (see callers in _main), since
        # this never gives up the terminal the way the sudo-fallback path
        # has to.
        state = {}

        def runner():
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    work_fn()
            except Exception as e:
                buf.write(f"\nUnexpected error: {e}\n")
            state["output"] = buf.getvalue()

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        frames = "|/-\\"
        i = 0
        stdscr.timeout(120)
        while thread.is_alive():
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            text = f"{busy_message} {frames[i % len(frames)]}"[:w - 4]
            try:
                stdscr.addstr(h // 2, max(2, (w - len(text)) // 2), text)
            except curses.error:
                pass
            stdscr.refresh()
            stdscr.getch()
            i += 1
        stdscr.timeout(-1)
        thread.join()

        self._message(stdscr, state.get("output", "").strip() or "(no output)")

    def _main(self, stdscr, result):
        curses.curs_set(0)
        self._init_colors()
        while True:
            options = ["Create New Share", "Manage Existing Shares", "Manage Users & Groups"]
            if self.wizard.gui_available():
                options.append("Launch Desktop UI")
            options.append("Exit")

            # The big ASCII wordmark stands in for the small bold title text -
            # title="" below skips that redundant line entirely.
            full_banner = KELPIE_TITLE + [''] + KELPIE_LOGO
            full_banner_colors = [None] * (len(KELPIE_TITLE) + 1) + KELPIE_LOGO_COLORS
            full_banner_colors_256 = [None] * (len(KELPIE_TITLE) + 1) + KELPIE_LOGO_COLORS_256
            choice = self._menu(
                stdscr, "", options,
                banner=full_banner, banner_colors=full_banner_colors, banner_colors_256=full_banner_colors_256
            )
            if choice is None:
                return
            selected = options[choice]

            if selected == "Exit":
                return
            elif selected == "Create New Share":
                share_data = self._create_share_flow(stdscr)
                if share_data:
                    if self.wizard.elevation_needs_terminal():
                        result["share_data"] = share_data
                        return
                    self._run_privileged_action(
                        stdscr, f"Creating share '{share_data['name']}'...",
                        lambda: self._apply_in_curses(share_data)
                    )
                    if any(s['name'] == share_data['name'] for s in self.wizard.list_shares()):
                        self._offer_qr_codes_in_curses(stdscr, share_data['name'], share_data['users'])
            elif selected == "Manage Existing Shares":
                # _manage_shares_flow only returns non-None when the action
                # genuinely needs the terminal (see its docstring) - the
                # fast in-curses path already applied and looped internally.
                action = self._manage_shares_flow(stdscr)
                if action:
                    if action["action"] == "delete":
                        result["delete_share"] = action
                    elif action["action"] == "add_user":
                        result["add_user"] = action
                    return
            elif selected == "Manage Users & Groups":
                # _users_groups_flow only returns non-None when the action
                # genuinely needs the terminal - the fast in-curses path
                # (via _apply_or_bubble) already applied and looped
                # internally.
                action = self._users_groups_flow(stdscr)
                if action:
                    result["users_groups_action"] = action
                    return
            elif selected == "Launch Desktop UI":
                result["launch_gui"] = True
                return

    # ---- generic widgets ----

    def _menu(self, stdscr, title, options, subtitle=None, banner=None, banner_colors=None,
              banner_colors_256=None, body=None):
        idx = 0
        curses.curs_set(0)
        stdscr.keypad(True)
        footer = "Up/Down: move  Enter: select  Esc/q: cancel"
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()

            show_banner = bool(
                banner and h >= len(banner) + len(options) + 8
                and w >= max(len(l) for l in banner) + 4
            )

            # Block-center the whole thing: one shared left margin (col) so
            # the banner/menu keep their internal alignment, and the block
            # as a whole sits centered both horizontally and vertically
            # instead of pinned to the top-left corner.
            content_width = max(
                [len(title)]
                + ([max(len(l) for l in banner)] if show_banner else [])
                + ([len(subtitle)] if subtitle else [])
                + ([max(len(l) for l in body)] if body else [])
                + [len(opt) + 2 for opt in options]
            )
            content_width = min(content_width, w - 4)
            col = max(2, (w - content_width) // 2)

            content_rows = (
                ((len(banner) + 1) if show_banner else 0)
                + 1  # title
                + (1 if subtitle else 0)
                + 1  # spacer before body/options
                + ((len(body) + 1) if body else 0)
                + len(options)
            )
            row = max(0, (h - content_rows - 2) // 2)

            if show_banner:
                for li, line in enumerate(banner):
                    colors = banner_colors[li] if banner_colors else None
                    colors256 = banner_colors_256[li] if banner_colors_256 else None
                    if colors:
                        for ci, ch in enumerate(line[:w - col - 2]):
                            c256 = colors256[ci] if colors256 else None
                            try:
                                stdscr.addch(row, col + ci, ch, self._banner_attr(colors[ci], c256))
                            except curses.error:
                                pass
                    else:
                        try:
                            stdscr.addstr(row, col, line[:w - col - 2], curses.A_BOLD)
                        except curses.error:
                            pass
                    row += 1
                row += 1

            try:
                stdscr.addstr(row, col, title[:w - col - 2], curses.A_BOLD)
            except curses.error:
                pass
            row += 1
            if subtitle:
                try:
                    stdscr.addstr(row, col, subtitle[:w - col - 2])
                except curses.error:
                    pass
                row += 1
            row += 1  # blank line before body/options

            if body:
                for line in body:
                    if row >= h - len(options) - 3:
                        break
                    try:
                        stdscr.addstr(row, col, line[:w - col - 2])
                    except curses.error:
                        pass
                    row += 1
                row += 1  # blank line before options

            for i, opt in enumerate(options):
                opt_row = row + i
                if opt_row >= h - 1:
                    break
                marker = ">" if i == idx else " "
                attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
                try:
                    stdscr.addstr(opt_row, col, f"{marker} {opt}"[:w - col - 2], attr)
                except curses.error:
                    pass
            try:
                footer_col = max(0, (w - len(footer)) // 2)
                stdscr.addstr(h - 1, footer_col, footer[:w - footer_col - 1], curses.A_DIM)
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord('k')):
                idx = (idx - 1) % len(options)
            elif key in (curses.KEY_DOWN, ord('j')):
                idx = (idx + 1) % len(options)
            elif key in (curses.KEY_ENTER, 10, 13):
                return idx
            elif key in (27, ord('q')):
                return None

    def _tree_menu(self, stdscr, title, items, subtitle=None):
        # Like _menu, but each entry can carry its own non-selectable,
        # indented context lines shown directly beneath it (e.g. a user's
        # groups/shares) - only the top-level entries are navigable, so
        # there's no separate options list duplicating the same names.
        # items: list of (label, sublines). Esc/q cancels - no "Back" entry
        # needed since that's exactly what Esc already does.
        idx = 0
        curses.curs_set(0)
        stdscr.keypad(True)
        footer = "Up/Down: move  Enter: select  Esc/q: cancel"
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()

            width_lines = [title] + ([subtitle] if subtitle else [])
            for label, sublines in items:
                width_lines.append(label)
                width_lines.extend(sublines)
            content_width = min(max(len(l) for l in width_lines), w - 4)
            col = max(2, (w - content_width) // 2)

            content_rows = 1 + (1 if subtitle else 0) + 1
            for label, sublines in items:
                content_rows += 1 + len(sublines) + 1
            row = max(0, (h - content_rows - 2) // 2)

            try:
                stdscr.addstr(row, col, title[:w - col - 2], curses.A_BOLD)
            except curses.error:
                pass
            row += 1
            if subtitle:
                try:
                    stdscr.addstr(row, col, subtitle[:w - col - 2])
                except curses.error:
                    pass
                row += 1
            row += 1

            for i, (label, sublines) in enumerate(items):
                if row >= h - 1:
                    break
                marker = ">" if i == idx else " "
                attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
                try:
                    stdscr.addstr(row, col, f"{marker} {label}"[:w - col - 2], attr)
                except curses.error:
                    pass
                row += 1
                for line in sublines:
                    if row >= h - 1:
                        break
                    try:
                        stdscr.addstr(row, col, ("    " + line)[:w - col - 2])
                    except curses.error:
                        pass
                    row += 1
                row += 1

            try:
                footer_col = max(0, (w - len(footer)) // 2)
                stdscr.addstr(h - 1, footer_col, footer[:w - footer_col - 1], curses.A_DIM)
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord('k')):
                idx = (idx - 1) % len(items)
            elif key in (curses.KEY_DOWN, ord('j')):
                idx = (idx + 1) % len(items)
            elif key in (curses.KEY_ENTER, 10, 13):
                return idx
            elif key in (27, ord('q')):
                return None

    def _text_input(self, stdscr, prompt, password=False):
        # Block-centered like _menu/_message - this used to hardcode
        # row 0, col 2 (top-left corner) regardless of screen size, the
        # only one of the TUI's screens that wasn't centered.
        curses.curs_set(1)
        stdscr.keypad(True)
        buf = []
        prompt_lines = prompt.split("\n")
        # Fixed to the prompt's own width, not the growing input text -
        # otherwise the whole block would shift horizontally as you type.
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            shown = ("*" * len(buf)) if password else "".join(buf)

            content_width = min(max(len(l) for l in prompt_lines), w - 4)
            col = max(2, (w - content_width) // 2)
            content_rows = len(prompt_lines) + 2  # prompt line(s) + spacer + input line
            row = max(1, (h - content_rows - 2) // 2)

            for i, line in enumerate(prompt_lines):
                try:
                    stdscr.addstr(row + i, col, line[:w - 4], curses.A_BOLD)
                except curses.error:
                    pass
            input_row = row + len(prompt_lines) + 1
            try:
                stdscr.addstr(input_row, col, shown[:w - 4])
                footer = "Enter: confirm  Esc: cancel"
                stdscr.addstr(h - 1, max(2, (w - len(footer)) // 2), footer[:w - 4], curses.A_DIM)
            except curses.error:
                pass
            stdscr.move(input_row, min(col + len(shown), w - 2))
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                curses.curs_set(0)
                return "".join(buf)
            elif key == 27:
                curses.curs_set(0)
                return None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if buf:
                    buf.pop()
            elif 32 <= key <= 126:
                buf.append(chr(key))

    def _message(self, stdscr, text):
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        wrap_width = max(20, min(70, w - 4))
        # Wrap each paragraph (split on \n for intentional line breaks)
        # to a readable column width, then block-center the result -
        # a single long unwrapped paragraph previously ran straight off
        # the right edge of the screen instead of reading as text.
        lines = []
        for paragraph in text.split("\n"):
            lines.extend(textwrap.wrap(paragraph, wrap_width) or [""])

        content_width = min(max((len(l) for l in lines), default=0), w - 4)
        col = max(2, (w - content_width) // 2)
        row = max(1, (h - len(lines) - 2) // 2)

        for i, line in enumerate(lines):
            if row + i >= h - 1:
                break
            try:
                stdscr.addstr(row + i, col, line[:w - 4])
            except curses.error:
                pass
        footer = "Press any key to continue..."
        try:
            stdscr.addstr(h - 1, max(2, (w - len(footer)) // 2), footer[:w - 4], curses.A_DIM)
        except curses.error:
            pass
        stdscr.refresh()
        stdscr.getch()

    # ---- directory picker ----

    def _directory_picker(self, stdscr, start_path):
        current = start_path if os.path.isdir(start_path) else self.wizard._real_home()
        while True:
            try:
                entries = sorted(
                    (e for e in os.listdir(current) if os.path.isdir(os.path.join(current, e)) and not e.startswith('.')),
                    key=str.lower
                )
            except (PermissionError, FileNotFoundError):
                entries = []

            # "Select this folder" is last and never pre-highlighted, so
            # mashing Enter while browsing can't accidentally pick the
            # starting directory (typically $HOME) before you've navigated
            # anywhere.
            options = [".. (up one level)"] + [f"{e}/" for e in entries] + ["[Select this folder]"]
            idx = self._menu(stdscr, "Select a folder", options, subtitle=current)
            if idx is None:
                return None
            if idx == len(options) - 1:
                return current
            elif idx == 0:
                parent = os.path.dirname(current.rstrip('/'))
                current = parent if parent else current
            else:
                current = os.path.join(current, entries[idx - 1])

    # ---- create-share flow ----

    def _pick_or_type_username(self, stdscr, title="Username:"):
        # Existing usernames one click away (no risk of a typo against a
        # name that already exists), but typing a new one still works -
        # this can create a brand-new user, unlike group membership which
        # requires an existing one. Skips straight to free text if there's
        # nothing to pick from yet.
        existing = [u["username"] for u in self.wizard.list_users()]
        if existing:
            options = existing + ["+ New user..."]
            idx = self._menu(stdscr, title, options, subtitle="Pick an existing user, or add a new one")
            if idx is None:
                return None
            if idx < len(existing):
                return existing[idx]
        return self._text_input(stdscr, title)

    def _manage_users(self, stdscr):
        users = []
        while True:
            options = [
                f"{u['username']}{' (read-only)' if u.get('read_only') else ''}  (select to remove)"
                for u in users
            ] + ["+ Add User", "Done"]
            idx = self._menu(stdscr, "Users", options, subtitle=f"{len(users)} configured")
            if idx is None or idx == len(options) - 1:
                return users
            if idx == len(options) - 2:
                username = self._pick_or_type_username(stdscr)
                if not username:
                    continue
                password = self._text_input(stdscr, f"Password for {username}:", password=True)
                if not password:
                    continue
                access_choice = self._menu(
                    stdscr, f"Access level for {username}", ["Read-write", "Read-only"]
                )
                if access_choice is None:
                    continue
                users.append({"username": username, "password": password, "read_only": access_choice == 1})
            else:
                del users[idx]

    def _create_share_flow(self, stdscr):
        name = self._text_input(stdscr, "1. Share name:")
        if not name:
            return None

        default_path = self.wizard.default_share_path(name)
        choice = self._menu(stdscr, "2. Share path", [
            f"Use default: {default_path}",
            "Browse for a folder (cursor keys)",
            "Type a path manually",
        ])
        if choice is None:
            return None
        if choice == 0:
            path = default_path
        elif choice == 1:
            picked = self._directory_picker(stdscr, self.wizard._real_home())
            if not picked:
                return None
            path = picked
        else:
            path = self._text_input(stdscr, "3. Path:")
            if not path:
                return None

        users = self._manage_users(stdscr)

        self._message(stdscr, f"Now applying '{name}' to the system (leaving this screen)...")
        return {"name": name, "path": path, "users": users}

    def _manage_shares_flow(self, stdscr):
        # Returns an action dict to be applied outside curses, but only when
        # elevation_needs_terminal() is True. Otherwise the action is
        # applied right here and this loops back to the (refreshed) share
        # list, instead of bubbling out to the main menu after every single
        # action. Returns None once the user backs all the way out (Esc/q).
        while True:
            shares = self.wizard.list_shares()
            if not shares:
                self._message(stdscr, "No existing shares found.")
                return None
            items = [
                (
                    s["name"],
                    [f"path: {s.get('path', 'Unknown')}"]
                    + [
                        f"user: {u['username']}" + (" (read-only)" if u.get("read_only") else "")
                        for u in s.get("users", [])
                    ],
                )
                for s in shares
            ]
            idx = self._tree_menu(stdscr, "Manage Shares", items, subtitle="Select a share to manage")
            if idx is None:
                return None
            share = shares[idx]

            sub_options = ["Add user", "Delete share"]
            sub_idx = self._menu(stdscr, share['name'], sub_options, subtitle=share.get('path', ''))
            if sub_idx is None:
                continue
            choice = sub_options[sub_idx]

            if choice == "Delete share":
                confirm = self._menu(stdscr, f"Delete share '{share['name']}'?", ["Yes, delete", "Cancel"])
                if confirm != 0:
                    continue
                delete_folder = False
                if share.get('path'):
                    folder_choice = self._menu(
                        stdscr, "Also permanently delete the folder and everything in it?",
                        ["No, keep the folder", "Yes, delete the folder too"],
                        subtitle=share['path'], body=["This cannot be undone."],
                    )
                    if folder_choice is None:
                        continue
                    delete_folder = folder_choice == 1
                action = {"action": "delete", "name": share['name'], "delete_folder": delete_folder}
            else:
                username = self._pick_or_type_username(stdscr)
                if not username:
                    continue
                password = self._text_input(stdscr, f"Password for {username}:", password=True)
                if not password:
                    continue
                access_choice = self._menu(
                    stdscr, f"Access level for {username}", ["Read-write", "Read-only"]
                )
                if access_choice is None:
                    continue
                action = {
                    "action": "add_user", "share": share['name'], "username": username, "password": password,
                    "read_only": access_choice == 1,
                }

            if self.wizard.elevation_needs_terminal():
                return action

            if action["action"] == "delete":
                self._run_privileged_action(
                    stdscr, f"Removing share '{action['name']}'...",
                    lambda: self._delete_in_curses(action["name"], action.get("delete_folder", False))
                )
            else:
                self._run_privileged_action(
                    stdscr, f"Adding '{action['username']}' to '{action['share']}'...",
                    lambda: self._add_user_in_curses(action)
                )
                if any(
                    action["username"] in [u["username"] for u in s.get("users", [])]
                    for s in self.wizard.list_shares() if s["name"] == action["share"]
                ):
                    self._offer_qr_codes_in_curses(
                        stdscr, action["share"], [{"username": action["username"], "password": action["password"]}]
                    )
            # loop back and show the (refreshed) share list again

    def _users_groups_flow(self, stdscr):
        # Returns an action dict to apply outside curses (revoke_access /
        # delete_user / delete_group / assign_group / revoke_group /
        # create_group / assign_group_share / unassign_group_share), or
        # None once the user backs all the way out (Esc/q at any level -
        # no separate "Back" entries needed). Users and Groups are
        # deliberately separate screens - easier to look at one without the
        # other in the way, rather than one merged list.
        while True:
            choice = self._menu(stdscr, "Users & Groups", ["Users", "Groups"])
            if choice is None:
                return None
            action = self._users_screen_flow(stdscr) if choice == 0 else self._groups_screen_flow(stdscr)
            if action:
                return action
            # otherwise the user backed out of that screen - show the chooser again

    def _users_screen_flow(self, stdscr):
        while True:
            users = self.wizard.list_users()
            access_lookup = self.wizard.build_access_lookup(self.wizard.list_shares())

            # "+ New User" always available, even with no existing users -
            # a standalone account not attached to any share or group, so a
            # user doesn't have to create a throwaway share just to get an
            # account to exist.
            items = [("+ New User", [])] + [
                (
                    u["username"],
                    [f"group: {g}" for g in u["groups"]]
                    + [
                        f"share: {s}" + (" (read-only)" if access_lookup.get((s, u["username"])) else "")
                        for s in u["shares"]
                    ],
                )
                for u in users
            ]
            idx = self._tree_menu(stdscr, "Users", items, subtitle="Select a user to manage")
            if idx is None:
                return None

            if idx == 0:
                username = self._text_input(stdscr, "Username:")
                if not username:
                    continue
                password = self._text_input(stdscr, f"Password for {username}:", password=True)
                if not password:
                    continue
                action = self._apply_or_bubble(
                    stdscr, {"action": "create_user", "username": username, "password": password}
                )
                if action:
                    return action
                continue

            action = self._manage_user_flow(stdscr, users[idx - 1])
            if action:
                return action
            # otherwise back out of the submenu - show this screen again

    def _groups_screen_flow(self, stdscr):
        while True:
            groups = self.wizard.list_groups()

            # "+ New Group" always available, even with no existing groups -
            # a standalone access-control group, not tied to any share
            # until explicitly assigned via "Assign to share". Unlike a
            # share's own auto-created ownership group (filesystem-
            # permission-only), this is never created automatically.
            items = [("+ New Group", [])] + [
                (g["name"], [f"user: {m}" for m in g["members"]] + [f"share: {s}" for s in g["shares"]])
                for g in groups
            ]
            idx = self._tree_menu(stdscr, "Groups", items, subtitle="Select a group to manage")
            if idx is None:
                return None

            if idx == 0:
                name = self._text_input(stdscr, "Group name:")
                if not name:
                    continue
                action = self._apply_or_bubble(stdscr, {"action": "create_group", "name": name})
                if action:
                    return action
                continue

            action = self._manage_group_flow(stdscr, groups[idx - 1])
            if action:
                return action
            # otherwise back out of the submenu - show this screen again

    def _manage_user_flow(self, stdscr, user):
        # Returns an action dict only when it genuinely needs the terminal
        # (see elevation_needs_terminal()) - otherwise applies it right here
        # and returns None, same as backing out, so the caller's loop just
        # re-shows the (refreshed) list instead of bubbling to the main menu.
        sub_options = ["Assign to group"]
        if user["groups"]:
            sub_options.append("Remove from group")
        if user["shares"]:
            sub_options.append("Revoke share access")
            sub_options.append("Change Access Level")
            sub_options.append("Reset Password & Show QR")
        sub_options.append("Delete user")
        sub_idx = self._menu(
            stdscr, user["username"], sub_options,
            subtitle=f"groups: {', '.join(user['groups']) or '(none)'}  shares: {', '.join(user['shares']) or '(none)'}"
        )
        if sub_idx is None:
            return None
        choice = sub_options[sub_idx]

        if choice == "Change Access Level":
            if len(user["shares"]) == 1:
                share_name = user["shares"][0]
            else:
                sidx = self._menu(stdscr, "Change access level for which share?", user["shares"])
                if sidx is None:
                    return None
                share_name = user["shares"][sidx]
            share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
            share_user = next((u for u in (share or {}).get("users", []) if u["username"] == user["username"]), None)
            current_read_only = share_user.get("read_only", False) if share_user else False
            current_label = "read-only" if current_read_only else "read-write"
            new_label = "read-write" if current_read_only else "read-only"
            confirm = self._menu(
                stdscr, f"'{user['username']}' is {current_label} on '{share_name}'. Change to {new_label}?",
                ["Yes, change it", "Cancel"],
            )
            if confirm != 0:
                return None
            action = {"action": "change_access", "share": share_name, "username": user["username"],
                      "read_only": not current_read_only}
            if self.wizard.elevation_needs_terminal():
                return action
            self._run_privileged_action(
                stdscr, f"Changing '{user['username']}''s access level on '{share_name}'...",
                lambda: self._users_groups_action_in_curses(action)
            )
            return None

        elif choice == "Reset Password & Show QR":
            # Same underlying operation "Add user" (on the share side)
            # performs when the user already exists - grant_share_access
            # resets the password rather than failing. See
            # QR_PASSWORD_RESET_NOTE for why this is a reset, not a lookup:
            # a structural limitation of SMB/Samba/Windows, not a Kelpie
            # shortcoming, and Kelpie itself never saves any
            # username/password information.
            self._message(stdscr, QR_PASSWORD_RESET_NOTE)
            if len(user["shares"]) == 1:
                share_name = user["shares"][0]
            else:
                sidx = self._menu(stdscr, "Reset password & show QR for which share?", user["shares"])
                if sidx is None:
                    return None
                share_name = user["shares"][sidx]
            password = self._text_input(
                stdscr, f"New password for {user['username']}\n(replaces their current one):", password=True
            )
            if not password:
                return None
            # Preserve the user's current access level on this share - a
            # password reset shouldn't silently flip them back to read-write.
            share = next((s for s in self.wizard.list_shares() if s["name"] == share_name), None)
            share_user = next((u for u in (share or {}).get("users", []) if u["username"] == user["username"]), None)
            read_only = share_user.get("read_only", False) if share_user else False
            action = {
                "action": "add_user", "share": share_name, "username": user["username"], "password": password,
                "read_only": read_only,
            }
            if self.wizard.elevation_needs_terminal():
                return action
            self._run_privileged_action(
                stdscr, f"Resetting '{user['username']}''s password on '{share_name}'...",
                lambda: self._add_user_in_curses(action)
            )
            if any(
                user["username"] in [u["username"] for u in s.get("users", [])]
                for s in self.wizard.list_shares() if s["name"] == share_name
            ):
                self._offer_qr_codes_in_curses(
                    stdscr, share_name, [{"username": user["username"], "password": password}]
                )
            return None

        elif choice == "Assign to group":
            groups = self.wizard.list_groups()
            if not groups:
                self._message(stdscr, "No groups exist to assign to.")
                return None
            group_options = [g["name"] for g in groups]
            gidx = self._menu(stdscr, "Assign to which group?", group_options)
            if gidx is None:
                return None
            action = {"action": "assign_group", "username": user["username"], "group": group_options[gidx]}

        elif choice == "Remove from group":
            gidx = self._menu(stdscr, "Remove from which group?", user["groups"])
            if gidx is None:
                return None
            action = {"action": "revoke_group", "username": user["username"], "group": user["groups"][gidx]}

        elif choice == "Revoke share access":
            sidx = self._menu(stdscr, "Revoke access to which share?", user["shares"])
            if sidx is None:
                return None
            action = {"action": "revoke_access", "share": user["shares"][sidx], "username": user["username"]}

        else:
            confirm = self._menu(stdscr, f"Delete user '{user['username']}'?", ["Yes, delete", "Cancel"])
            if confirm != 0:
                return None
            action = {"action": "delete_user", "username": user["username"]}

        return self._apply_or_bubble(stdscr, action)

    def _manage_group_flow(self, stdscr, group):
        # Same contract as _manage_user_flow: only returns non-None when
        # elevation genuinely needs the terminal.
        sub_options = ["Add member"]
        if group["members"]:
            sub_options.append("Remove member")
        if group["members"] and group["shares"]:
            sub_options.append("Set Access Level")
        sub_options.append("Assign to share")
        if group["shares"]:
            sub_options.append("Remove from share")
        sub_options.append("Delete group")
        sub_idx = self._menu(
            stdscr, group["name"], sub_options,
            subtitle=f"members: {', '.join(group['members']) or '(none)'}  shares: {', '.join(group['shares']) or '(none)'}"
        )
        if sub_idx is None:
            return None
        choice = sub_options[sub_idx]

        if choice == "Set Access Level":
            # Bulk-applies to every CURRENT member of the group, right now -
            # not a persistent group-level grant. Someone added to the
            # group later doesn't automatically inherit this.
            if len(group["shares"]) == 1:
                share_name = group["shares"][0]
            else:
                sidx = self._menu(stdscr, "Set access level on which share?", group["shares"])
                if sidx is None:
                    return None
                share_name = group["shares"][sidx]
            level_choice = self._menu(
                stdscr, f"Set every current member of '{group['name']}' to:", ["Read-write", "Read-only"]
            )
            if level_choice is None:
                return None
            read_only = level_choice == 1
            confirm = self._menu(
                stdscr,
                f"Apply {'read-only' if read_only else 'read-write'} to all "
                f"{len(group['members'])} member(s) of '{group['name']}' on '{share_name}'?",
                ["Yes, apply", "Cancel"],
            )
            if confirm != 0:
                return None
            action = {"action": "group_access", "group": group["name"], "share": share_name, "read_only": read_only}

        elif choice == "Add member":
            # A pick-list of existing users, not free text: adding a member
            # requires an already-existing account (unlike a share's "Add
            # user", which can create one) - the underlying action validates
            # and refuses otherwise, so free text here could only ever fail.
            usernames = [u["username"] for u in self.wizard.list_users()]
            if not usernames:
                self._message(stdscr, "No existing users to add. Create one via a share's 'Add user' first.")
                return None
            uidx = self._menu(stdscr, f"Add which user to '{group['name']}'?", usernames)
            if uidx is None:
                return None
            action = {"action": "assign_group", "username": usernames[uidx], "group": group["name"]}

        elif choice == "Remove member":
            midx = self._menu(stdscr, "Remove which member?", group["members"])
            if midx is None:
                return None
            action = {"action": "revoke_group", "username": group["members"][midx], "group": group["name"]}

        elif choice == "Assign to share":
            # A real, persistent grant (unlike "Set Access Level" above) -
            # any current or future member of the group gets this access,
            # since Windows/Samba/macOS all resolve group membership live
            # at connect time.
            shares = [s["name"] for s in self.wizard.list_shares()]
            if not shares:
                self._message(stdscr, "No shares exist yet.")
                return None
            sidx = self._menu(stdscr, f"Grant '{group['name']}' access to which share?", shares)
            if sidx is None:
                return None
            share_name = shares[sidx]
            level_choice = self._menu(stdscr, f"Grant '{group['name']}' members:", ["Read-write", "Read-only"])
            if level_choice is None:
                return None
            read_only = level_choice == 1
            confirm = self._menu(
                stdscr,
                f"Grant '{group['name']}' {'read-only' if read_only else 'read-write'} access to '{share_name}'? "
                f"Every current and future member gets this access.",
                ["Yes, assign", "Cancel"],
            )
            if confirm != 0:
                return None
            action = {
                "action": "assign_group_share", "group": group["name"], "share": share_name, "read_only": read_only
            }

        elif choice == "Remove from share":
            if len(group["shares"]) == 1:
                share_name = group["shares"][0]
            else:
                sidx = self._menu(stdscr, f"Remove '{group['name']}' from which share?", group["shares"])
                if sidx is None:
                    return None
                share_name = group["shares"][sidx]
            confirm = self._menu(
                stdscr, f"Remove '{group['name']}''s access to '{share_name}'?", ["Yes, remove", "Cancel"]
            )
            if confirm != 0:
                return None
            action = {"action": "unassign_group_share", "group": group["name"], "share": share_name}

        else:
            confirm = self._menu(stdscr, f"Delete group '{group['name']}'?", ["Yes, delete", "Cancel"])
            if confirm != 0:
                return None
            action = {"action": "delete_group", "name": group["name"]}

        return self._apply_or_bubble(stdscr, action)

    def _apply_or_bubble(self, stdscr, action):
        # Shared by _manage_user_flow/_manage_group_flow: returns the action
        # dict when elevation needs the terminal (the caller bubbles it up
        # to run(), which applies it outside curses), otherwise applies it
        # right here in-curses and returns None - same as backing out, so
        # the caller's own loop just re-shows the refreshed list instead of
        # bubbling all the way to the main menu.
        if self.wizard.elevation_needs_terminal():
            return action
        busy = {
            "create_user": f"Creating user '{action.get('username')}'...",
            "revoke_access": f"Revoking access to '{action.get('share')}'...",
            "delete_user": f"Deleting user '{action.get('username')}'...",
            "delete_group": f"Deleting group '{action.get('name')}'...",
            "assign_group": f"Adding '{action.get('username')}' to '{action.get('group')}'...",
            "revoke_group": f"Removing '{action.get('username')}' from '{action.get('group')}'...",
            "group_access": f"Setting access level for '{action.get('group')}' on '{action.get('share')}'...",
            "create_group": f"Creating group '{action.get('name')}'...",
            "assign_group_share": f"Assigning '{action.get('group')}' to '{action.get('share')}'...",
            "unassign_group_share": f"Removing '{action.get('group')}''s access to '{action.get('share')}'...",
        }.get(action["action"], "Working...")
        self._run_privileged_action(stdscr, busy, lambda: self._users_groups_action_in_curses(action))
        return None


def main():
    TUIWizard().run()


if __name__ == "__main__":
    main()
