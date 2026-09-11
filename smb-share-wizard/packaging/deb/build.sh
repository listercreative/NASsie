#!/bin/sh
# Rebuild nassie_0.1.29_all.deb from current source. Run from anywhere;
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

# gui_qt.py/tour_qt.py - the in-progress PySide6 rewrite, reachable only
# via the explicit `nassie --gui-qt` flag (main.py never launches it by
# default - see that file's own run_gui_qt() comment) - still shipped
# unconditionally here, same as gui.py/tour.py, since main.py's dispatch
# unconditionally imports whichever one the flag it got actually asked
# for; leaving them out landed as a live ModuleNotFoundError the moment
# `--gui-qt` was used from an installed package, not a graceful "not
# supported here" message.
cp "$SRC/main.py" "$SRC/core.py" "$SRC/cli.py" "$SRC/gui.py" "$SRC/gui_qt.py" "$SRC/tui.py" "$SRC/tour.py" "$SRC/tour_qt.py" "$SRC/window_corners.py" "$SRC/anim_debug.py" "$SRC/nassie_icon.png" "$PKGLIB/"
cp "$PROJECT_ROOT/assets/nassie_icon.png" "$PKG/usr/share/pixmaps/nassie.png"

# nassie_ttk/ is a real package (theme/*.tcl, theme/*.png, sv.tcl,
# LICENSE, __init__.py) - a plain cp of individual files (like the *.py
# list above) won't pick up its subdirectory. Removed and re-copied
# rather than merged, so a regenerated theme's deleted/renamed files
# don't linger as stale copies in the package dir.
rm -rf "$PKGLIB/nassie_ttk"
cp -r "$SRC/nassie_ttk" "$PKGLIB/"
# A local py_compile/import of nassie_ttk leaves __pycache__ inside
# src/nassie_ttk/ itself (unlike the top-level *.py files above, which
# are copied individually and so never sweep one in) - cp -r happily
# copies it straight into the package if present.
find "$PKGLIB/nassie_ttk" -name "__pycache__" -exec rm -rf {} +

# icons/ - baked PNG icon assets (see gui.py's _load_icon()). Only the
# *.png output ships - render_icons.py is the dev-only authoring script
# that generates them, not something the running app ever imports.
rm -rf "$PKGLIB/icons"
mkdir -p "$PKGLIB/icons"
cp "$SRC"/icons/*.png "$PKGLIB/icons/"

find "$PKG" -type d -exec chmod 755 {} \;
chmod 644 "$PKGLIB"/*.py "$PKGLIB/nassie_icon.png"
find "$PKGLIB/nassie_ttk" -type f -exec chmod 644 {} \;
find "$PKGLIB/icons" -type f -exec chmod 644 {} \;
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm" "$PKG/usr/bin/nassie"
chmod 644 "$PKG/DEBIAN/control" \
          "$PKG/usr/share/applications/nassie.desktop" \
          "$PKG/usr/share/doc/nassie/copyright" \
          "$PKG/usr/share/pixmaps/nassie.png"
chmod +x "$SCRIPT_DIR/install.sh"
chmod 644 "$SCRIPT_DIR/preview.py"

dpkg-deb --build --root-owner-group "$PKG" "$SCRIPT_DIR/nassie_0.1.29_all.deb"
echo "Built $SCRIPT_DIR/nassie_0.1.29_all.deb"
