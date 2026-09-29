#!/bin/sh
# Copy the current source (src/*.py, icons, app icon) into the .deb's
# package tree so the tracked copy under nassie/usr/lib/nassie never lags
# behind src/. Called by build.sh, and by push.sh before every release
# commit. Does NOT build the .deb itself.
set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
SRC="$PROJECT_ROOT/src"
PKG="$SCRIPT_DIR/nassie"
PKGLIB="$PKG/usr/lib/nassie"

# gui_qt.py/tour_qt.py - PySide6, now the only desktop GUI (the
# original Tk gui.py/tour.py/nassie_ttk/window_corners.py/anim_debug.py
# were removed entirely - see gui_qt.py's own module docstring for why
# and when). Reachable via the explicit `nassie --gui-qt` flag or the
# TUI's/basic CLI's own "Launch Desktop UI" entry.
cp "$SRC/main.py" "$SRC/core.py" "$SRC/cli.py" "$SRC/gui_qt.py" "$SRC/tui.py" "$SRC/tour_state.py" "$SRC/tour_qt.py" "$SRC/x11_error_handler.py" "$SRC/tty_debug.py" "$SRC/nassie_icon.png" "$PKGLIB/"
cp "$PROJECT_ROOT/assets/nassie_icon.png" "$PKG/usr/share/pixmaps/nassie.png"

# icons/ - baked PNG icon assets (see gui_qt.py's _icon()). Only the
# *.png output ships - render_icons.py is the dev-only authoring script
# that generates them, not something the running app ever imports.
rm -rf "$PKGLIB/icons"
mkdir -p "$PKGLIB/icons"
cp "$SRC"/icons/*.png "$PKGLIB/icons/"

chmod 644 "$PKGLIB"/*.py "$PKGLIB/nassie_icon.png"
find "$PKGLIB/icons" -type f -exec chmod 644 {} \;
