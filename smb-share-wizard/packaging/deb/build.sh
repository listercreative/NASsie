#!/bin/sh
# Rebuild nassie_0.1.33_all.deb from current source. Run from anywhere;
# paths are resolved relative to this script's location.
#
# Requires: dpkg-deb (part of the base `dpkg` package on any Debian/Ubuntu
# system - nothing extra to install).
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

find "$PKG" -type d -exec chmod 755 {} \;
chmod 644 "$PKGLIB"/*.py "$PKGLIB/nassie_icon.png"
find "$PKGLIB/icons" -type f -exec chmod 644 {} \;
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm" "$PKG/DEBIAN/postrm" "$PKG/usr/bin/nassie"
chmod 644 "$PKG/DEBIAN/control" \
          "$PKG/usr/share/applications/nassie.desktop" \
          "$PKG/usr/share/doc/nassie/copyright" \
          "$PKG/usr/share/pixmaps/nassie.png"
chmod +x "$SCRIPT_DIR/install.sh"
chmod 644 "$SCRIPT_DIR/preview.py"

dpkg-deb --build --root-owner-group "$PKG" "$SCRIPT_DIR/nassie_0.1.33_all.deb"
echo "Built $SCRIPT_DIR/nassie_0.1.33_all.deb"
