#!/bin/sh
# One-line install: curl -fsSL <raw-url-to-this-file> | sh
#
# Downloads the current NASsie source from GitHub, builds the .deb fresh,
# and runs the normal install.sh - this only automates *fetching* the
# files, it doesn't skip install.sh's own preview/confirmation step.
set -e

REPO_URL="https://github.com/listercreative/NASsie.git"
TARBALL_URL="https://github.com/listercreative/NASsie/archive/refs/heads/main.tar.gz"

if ! command -v git >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1; then
    echo "Need either git or curl installed to download NASsie." >&2
    exit 1
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading NASsie..."
if command -v git >/dev/null 2>&1; then
    git clone --depth 1 "$REPO_URL" "$TMPDIR/NASsie" >/dev/null 2>&1
    REPO_DIR="$TMPDIR/NASsie"
else
    curl -fsSL "$TARBALL_URL" | tar -xz -C "$TMPDIR"
    REPO_DIR="$TMPDIR/NASsie-main"
fi

cd "$REPO_DIR/smb-share-wizard/packaging/deb"
./build.sh

# < /dev/tty explicitly: this script may itself have been invoked as
# `curl | sh`, in which case stdin is the piped script source, not the
# terminal - install.sh needs the real terminal for its preview screen and
# y/N prompt, same reason postinst redirects the same way.
./install.sh < /dev/tty > /dev/tty 2> /dev/tty
