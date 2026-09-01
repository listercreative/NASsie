"""Pre-install preview screen: shows what NASsie will install before apt
touches the system. Intentionally self-contained (no import from src/) so
this file + install.sh + the .deb are a fully relocatable install bundle -
a user only needs these three files, not the whole source tree."""
import curses
import sys

# Block-letter "NASSIE" wordmark shown above the sea-serpent banner.
# pyfiglet's stock "univers" font, unedited. Keep in sync with
# src/tui.py's copy (this file is intentionally self-contained, see the
# module docstring, so it can't just import it) - this fell out of sync
# across several rounds of font changes there before being caught here.
NASSIE_TITLE = [
    '888b      88        db        ad88888ba   ad88888ba  88 88888888888  ',
    '8888b     88       d88b      d8"     "8b d8"     "8b 88 88           ',
    "88 `8b    88      d8'`8b     Y8,         Y8,         88 88           ",
    "88  `8b   88     d8'  `8b    `Y8aaaaa,   `Y8aaaaa,   88 88aaaaa      ",
    '88   `8b  88    d8YaaaaY8b     `"""""8b,   `"""""8b, 88 88"""""      ',
    '88    `8b 88   d8""""""""8b          `8b         `8b 88 88           ',
    "88     `8888  d8'        `8b Y8a     a8P Y8a     a8P 88 88           ",
    '88      `888 d8\'          `8b "Y88888P"   "Y88888P"  88 88888888888  ',
]

NASSIE_LOGO = [
    '                                           :=*%%#+-.',
    '                                        -*@@@@@@@@@@+.',
    '                                      .#@@@@@@@@@@@@@@%+.',
    '                                      %@@@@@@@@@@@@@@@@@=',
    '                                     =@@@@@@*--===++**+:',
    '                                     *@@@@@*',
    '                                     *@@@@@*',
    '                                     =@@@@@@.',
    '                                     .@@@@@@%',
    '                                      %@@@@@@*',
    '                                     .@@@@@@@@+',
    '                                ..:-+%@@@@@@@@@:',
    '                  .-=+*###%%%@@@@@@@@@@@@@@@@@@=',
    '           .:=+*#@@@#***##%@@@@@#**%@@@@@@@@@@@=',
    '    ..:-+*##*++=====+*###+-.:=+#%@%+=-=+#%@@@@@:',
    ':--====--=+*#####%%#*+-. .-+=. ::.-=*#%*+---==-      .::-::.',
    '  :--===-:.       .-=*%@@#*=:.  .=+*+=-:-------=+*#%#*+=-----:',
    '                       .:=+*%@%*+=::-=+*#%@@@@@#+-.',
    '                              :-=+**##%%%#*+=:.',
]

NASSIE_LOGO_COLORS_256 = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 37, 37, 37, 37, 37],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 37, 73, 72, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 37, 73, 108, 108, 108, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 73, 108, 108],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 73, 108, 108],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 37, 108, 108, 107],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 31, 37, 37, 37, 37, 72, 108, 108],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 73, 108, 108, 108],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 37, 37, 108, 108, 108, 108],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 72, 108, 108, 108, 107],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 108, 108, 108, 108, 108],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 31, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 72, 108, 108, 108, 108, 108],
    [0, 0, 0, 0, 31, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 37, 73, 108, 108, 108, 108, 108, 108, 107],
    [108, 108, 108, 72, 73, 73, 72, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 0, 37, 37, 37, 37, 37, 0, 107, 108, 72, 37, 37, 37, 37, 37, 37, 37, 37, 73, 108, 108, 108, 108, 0, 0, 0, 0, 0, 0, 107, 108, 108, 108, 108, 108, 107],
    [0, 0, 108, 108, 108, 108, 108, 108, 108, 108, 108, 0, 0, 0, 0, 0, 0, 0, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 72, 0, 0, 108, 108, 108, 108, 108, 108, 108, 108, 72, 72, 72, 72, 72, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 107],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 107, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 108, 107],
]

NASSIE_LOGO_COLORS_8 = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
]

EXPLANATION = [
    "This will install:",
    "",
    "  - nassie   (this wizard)",
    "  - samba    (the SMB/CIFS file-sharing server)",
    "",
    "Each time you create a share afterward, with your permission",
    "at that point, NASsie will also create a dedicated Linux user",
    "and a Unix group per share, write to /etc/samba/smb.conf, and",
    "restart the Samba services.",
]


def init_colors():
    color_ok, color_mode, pair_256 = False, "mono", {}
    try:
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK

        if curses.COLORS >= 256:
            used = sorted({v for row in NASSIE_LOGO_COLORS_256 for v in row if v})
            for pair_num, color_idx in enumerate(used, start=1):
                if pair_num >= curses.COLOR_PAIRS:
                    break
                curses.init_pair(pair_num, color_idx, bg)
                pair_256[color_idx] = pair_num
            color_mode = "256"
        else:
            curses.init_pair(1, curses.COLOR_GREEN, bg)
            curses.init_pair(2, curses.COLOR_CYAN, bg)
            color_mode = "8"
        color_ok = True
    except curses.error:
        pass
    return color_ok, color_mode, pair_256


def banner_attr(color_ok, color_mode, pair_256, idx8, idx256):
    if not color_ok:
        return curses.A_BOLD
    if color_mode == "256":
        pair = pair_256.get(idx256)
        return curses.color_pair(pair) if pair is not None else curses.A_BOLD
    pair = curses.color_pair(1 if idx8 in (0, 1) else 2)
    return pair | curses.A_BOLD if idx8 in (1, 3) else pair


def main(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    color_ok, color_mode, pair_256 = init_colors()

    title = "NASsie needs to install a few things"
    footer = "Up/Down: move  Enter: select  Esc/q: cancel"
    options = ["Continue with install", "Cancel"]
    idx = 0
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        show_banner = bool(
            h >= len(NASSIE_TITLE) + 1 + len(NASSIE_LOGO) + len(options) + len(EXPLANATION) + 6
            and w >= max(len(l) for l in NASSIE_LOGO) + 4
        )

        # Block-center the whole thing: one shared left margin (col) so the
        # banner/text keep their internal alignment, and the block as a
        # whole sits centered both horizontally and vertically instead of
        # pinned to the top-left corner.
        content_width = max(
            [len(title)]
            + ([max(len(l) for l in NASSIE_TITLE + NASSIE_LOGO)] if show_banner else [])
            + [len(l) for l in EXPLANATION]
            + [len(opt) + 2 for opt in options]
        )
        content_width = min(content_width, w - 4)
        col = max(2, (w - content_width) // 2)

        content_rows = (
            ((len(NASSIE_TITLE) + 1 + len(NASSIE_LOGO) + 1) if show_banner else 0)
            + 2  # title
            + len(EXPLANATION) + 1
            + len(options)
        )
        row = max(0, (h - content_rows - 2) // 2)

        if show_banner:
            for line in NASSIE_TITLE:
                try:
                    stdscr.addstr(row, col, line[:w - col - 2], curses.A_BOLD)
                except curses.error:
                    pass
                row += 1
            row += 1

            for li, line in enumerate(NASSIE_LOGO):
                c8 = NASSIE_LOGO_COLORS_8[li]
                c256 = NASSIE_LOGO_COLORS_256[li]
                for ci, ch in enumerate(line[:w - col - 2]):
                    try:
                        stdscr.addch(row, col + ci, ch, banner_attr(color_ok, color_mode, pair_256, c8[ci], c256[ci]))
                    except curses.error:
                        pass
                row += 1
            row += 1

        try:
            stdscr.addstr(row, col, title[:w - col - 2], curses.A_BOLD)
        except curses.error:
            pass
        row += 2

        for line in EXPLANATION:
            if row >= h - len(options) - 3:
                break
            try:
                stdscr.addstr(row, col, line[:w - col - 2])
            except curses.error:
                pass
            row += 1
        row += 1

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
            return idx == 0
        elif key in (27, ord('q')):
            return False


if __name__ == "__main__":
    try:
        proceed = curses.wrapper(main)
    except curses.error as e:
        print(f"Terminal UI unavailable ({e}); this preview needs a real terminal.", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if proceed else 1)
