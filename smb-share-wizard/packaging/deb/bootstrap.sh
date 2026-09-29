#!/bin/sh
# One-line install (and update - just run it again):
#   curl -fsSL <raw-url-to-this-file> | sh
#
# Downloads the LATEST RELEASE's installer bundle (the same
# nassie-linux-installer.tar.gz CI attaches to every GitHub release),
# verifies its SHA-256 checksum, and runs the bundled install.sh - this
# only automates *fetching* the files, it doesn't skip install.sh's own
# preview/confirmation step. Nothing is cloned or built on this machine, and
# unreleased commits on main are never installed.
set -e

BASE_URL="https://github.com/listercreative/NASsie/releases/latest/download"
BUNDLE="nassie-linux-installer.tar.gz"

if command -v curl >/dev/null 2>&1; then
    fetch() { curl -fsSL -o "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
    fetch() { wget -q -O "$2" "$1"; }
else
    echo "Need either curl or wget installed to download NASsie." >&2
    exit 1
fi
if ! command -v sha256sum >/dev/null 2>&1; then
    echo "Need sha256sum (part of coreutils) to verify the download." >&2
    exit 1
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading the latest NASsie release..."
fetch "$BASE_URL/$BUNDLE" "$TMPDIR/$BUNDLE"
fetch "$BASE_URL/$BUNDLE.sha256" "$TMPDIR/$BUNDLE.sha256"

echo "Verifying checksum..."
if ! (cd "$TMPDIR" && sha256sum -c "$BUNDLE.sha256" >/dev/null 2>&1); then
    echo "Checksum verification FAILED - the download is incomplete or has been tampered with." >&2
    echo "Nothing was installed." >&2
    exit 1
fi

tar -xzf "$TMPDIR/$BUNDLE" -C "$TMPDIR"
cd "$TMPDIR/nassie-installer"

# < /dev/tty explicitly: this script may itself have been invoked as
# `curl | sh`, in which case stdin is the piped script source, not the
# terminal - install.sh needs the real terminal for its preview screen and
# y/N prompt, same reason postinst redirects the same way.
./install.sh < /dev/tty > /dev/tty 2> /dev/tty
