#!/bin/sh
# Rebuild nassie_0.1.37_all.deb from current source. Run from anywhere;
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

# Sync src/ into the package tree (see sync.sh).
sh "$SCRIPT_DIR/sync.sh"

find "$PKG" -type d -exec chmod 755 {} \;
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm" "$PKG/DEBIAN/postrm" "$PKG/usr/bin/nassie"
chmod 644 "$PKG/DEBIAN/control" \
          "$PKG/usr/share/applications/nassie.desktop" \
          "$PKG/usr/share/doc/nassie/copyright" \
          "$PKG/usr/share/pixmaps/nassie.png"
chmod +x "$SCRIPT_DIR/install.sh"
chmod 644 "$SCRIPT_DIR/preview.py"

dpkg-deb --build --root-owner-group "$PKG" "$SCRIPT_DIR/nassie_0.1.37_all.deb"
echo "Built $SCRIPT_DIR/nassie_0.1.37_all.deb"
