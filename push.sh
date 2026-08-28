#!/bin/bash
# Bump NASsie's version everywhere it's hardcoded, commit, push to main, and
# publish a GitHub release - which triggers release-checksums.yml to build
# the Linux tarball and the Windows MSI and attach both (plus checksums.txt)
# to that release automatically.
#
# Usage:
#   ./push.sh                              # auto-bump patch version, prompt for a commit message
#   ./push.sh 0.2.0                        # use this version, prompt for a commit message
#   ./push.sh 0.2.0 "Add the GUI walkthrough"
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

WXS="smb-share-wizard/packaging/windows/nassie.wxs"
DIST_SH="smb-share-wizard/packaging/deb/dist.sh"
BUILD_SH="smb-share-wizard/packaging/deb/build.sh"
INSTALL_SH="smb-share-wizard/packaging/deb/install.sh"
CONTROL="smb-share-wizard/packaging/deb/nassie/DEBIAN/control"

command -v gh >/dev/null || { echo "gh CLI not found - install it first: https://cli.github.com" >&2; exit 1; }

BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$BRANCH" != "main" ]; then
    echo "You're on branch '$BRANCH', not main - switch to main before releasing." >&2
    exit 1
fi

CURRENT_VERSION=$(grep -oP 'Version="\K[0-9]+\.[0-9]+\.[0-9]+(?=\.0")' "$WXS")

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    IFS='.' read -r major minor patch <<< "$CURRENT_VERSION"
    VERSION="$major.$minor.$((patch + 1))"
    echo "No version given - auto-bumping $CURRENT_VERSION -> $VERSION"
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Version must look like X.Y.Z (got '$VERSION')" >&2
    exit 1
fi

MESSAGE="${2:-}"
if [ -z "$MESSAGE" ]; then
    read -rp "Commit message (also used as the release notes): " MESSAGE
fi
if [ -z "$MESSAGE" ]; then
    echo "A commit message is required." >&2
    exit 1
fi

OLD_DEB="nassie_${CURRENT_VERSION}_all.deb"
NEW_DEB="nassie_${VERSION}_all.deb"

# Every place the version string is baked in - there's no single source of
# truth for it, so each has to be updated in lockstep or the .deb's
# filename, its own internal Version:, and the MSI's ProductVersion drift
# out of sync with the git tag.
sed -i "s/Version=\"${CURRENT_VERSION}\.0\"/Version=\"${VERSION}.0\"/" "$WXS"
sed -i "s/VERSION=\"${CURRENT_VERSION}\"/VERSION=\"${VERSION}\"/" "$DIST_SH"
sed -i "s/${OLD_DEB}/${NEW_DEB}/g" "$DIST_SH" "$BUILD_SH" "$INSTALL_SH"
sed -i "s/^Version: ${CURRENT_VERSION}\$/Version: ${VERSION}/" "$CONTROL"
sed -i "s/${OLD_DEB}/${NEW_DEB}/g" .gitignore

grep -q "Version=\"${VERSION}.0\"" "$WXS" || { echo "Failed to update $WXS - aborting." >&2; exit 1; }
grep -q "^Version: ${VERSION}\$" "$CONTROL" || { echo "Failed to update $CONTROL - aborting." >&2; exit 1; }

echo "Bumped version: $CURRENT_VERSION -> $VERSION"

git add -A
git commit -m "$MESSAGE"
git push

TAG="v${VERSION}"
gh release create "$TAG" --target main --title "$TAG" --notes "$MESSAGE"

echo "Released $TAG - CI will build the Linux tarball and MSI and attach them shortly."
