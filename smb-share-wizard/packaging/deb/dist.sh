#!/bin/sh
# Build a self-contained, distributable install bundle: install.sh +
# preview.py + the .deb, so anyone downloading it only ever encounters
# install.sh as the way to install NASsie - never a bare .deb to run
# directly (which would skip our pre-install UI).
set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
VERSION="0.1.5"
OUT="$SCRIPT_DIR/nassie-linux-installer.tar.gz"
STAGE=$(mktemp -d)
BUNDLE_DIR="$STAGE/nassie-installer"

"$SCRIPT_DIR/build.sh"

mkdir -p "$BUNDLE_DIR"
cp "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/preview.py" "$SCRIPT_DIR/nassie_0.1.5_all.deb" "$BUNDLE_DIR/"
chmod +x "$BUNDLE_DIR/install.sh"

cat > "$BUNDLE_DIR/README.txt" <<EOF
NASsie $VERSION - Linux installer

To install:

    ./install.sh

This shows what will be installed and asks for confirmation before
touching your system. Don't run "apt install nassie_*.deb" or
"dpkg -i" directly on the .deb in this folder - that skips the
explanation/confirmation step install.sh provides.
EOF

tar -C "$STAGE" -czf "$OUT" nassie-installer
rm -rf "$STAGE"

(cd "$SCRIPT_DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")

echo "Built $OUT"
echo "Checksum: $SCRIPT_DIR/$(basename "$OUT").sha256"
