# Linux packaging — building the installer tarball

This directory builds `nassie-linux-installer.tar.gz`: a self-contained
bundle you can host anywhere (e.g. your website) for people to download
and run on Ubuntu/Debian. It contains `install.sh`, `preview.py`, the built
`.deb`, and a short `README.txt` — nothing else is needed, and there's no
bare `.deb` for someone to accidentally `dpkg -i` directly (which would
skip the preview/confirmation screen).

## Prerequisites

Just `dpkg-deb`, which is part of the base `dpkg` package on any
Debian/Ubuntu system — nothing extra to install. Building must be done on
Linux (or WSL); it isn't cross-buildable from macOS/Windows.

## Build

From this directory:

```sh
./dist.sh
```

This rebuilds the `.deb` from current source (`build.sh`) and then bundles
it into `nassie-linux-installer.tar.gz` in this same directory, alongside a
`nassie-linux-installer.tar.gz.sha256` checksum file. Upload both — the
tarball is what people install, the checksum lets them verify the download
wasn't corrupted or tampered with:

```sh
sha256sum -c nassie-linux-installer.tar.gz.sha256
```

## What downloaders actually do

```sh
tar -xzf nassie-linux-installer.tar.gz
cd nassie-installer
./install.sh
```

`install.sh` shows a preview of what will be installed (curses UI if
there's a real terminal, plain text otherwise), asks for confirmation,
then installs `nassie` + `samba` via `apt`. It also removes and cleanly
reinstalls automatically if NASsie is already present, so re-running it
against a newer tarball works as an update path.

Either of these requires a terminal - most Linux file managers don't
execute shell scripts on double-click by default, and even where one
does, it typically runs with no visible terminal attached, which
`install.sh` needs (it checks for a real tty to decide whether to show
the curses preview, and its plain-text fallback still needs to *read*
your y/N answer). There's no reliable double-click path for either the
tarball or `install.sh` itself.

## One-line install (curl | sh)

```sh
curl -fsSL https://raw.githubusercontent.com/listercreative/NASsie/main/smb-share-wizard/packaging/deb/bootstrap.sh | sh
```

`bootstrap.sh` clones the repo, builds the `.deb` from current source, and
runs `install.sh` - same preview/confirmation step as above, this only
automates *fetching* the files. It explicitly redirects `install.sh` to
`/dev/tty` rather than inherited stdin/stdout, since a `curl | sh`
invocation's own stdin is the piped script source, not the terminal - the
same reason `postinst` does the same redirect for NASsie's first-run
launch after install.

## Double-click alternative (no terminal)

Unlike shell scripts, a bare `.deb` file *is* something most desktop
Linux file managers hand off to a graphical package installer (GNOME
Software, KDE Discover, gdebi) on double-click. But offering the bare
`.deb` on its own means bypassing `install.sh` entirely - which means
losing the fix for the very first bug this project hit: modern Ubuntu
(22.04+) defaults new users' home directories to `750`, and `apt`'s
download sandbox (the unprivileged `_apt` user) can't read a file sitting
inside one - `install.sh` works around this by staging the `.deb` in
`/tmp` first. A GUI installer launched by double-clicking the file
straight out of `~/Downloads` has no such workaround, and can hit that
exact same "Permission denied" failure on a lot of real, default-config
Ubuntu systems. Not recommended as a primary path without that caveat
being clearly stated wherever it's offered.

## Releasing an update

**Bump the version before rebuilding**, in two places:

- `nassie/DEBIAN/control` — the `Version:` field
- `dist.sh` — the `VERSION` variable (cosmetic, only used in the bundled
  `README.txt`'s text)

This isn't optional cosmetics — `apt` compares versions to decide whether
there's anything to do. If you rebuild with the **same** version number,
`apt install` on a machine that already has NASsie installed will report
"already the newest version" and silently do nothing, even though the
package's file contents changed. `install.sh`'s `apt install --reinstall`
covers the *install* side of that gap (forces a reinstall of a
same-version package), but a real version bump is still what makes the
change show up as an actual upgrade rather than a same-version reinstall,
and is expected practice for any package intended for real distribution.

Also note `build.sh`/`dist.sh` currently hardcode the filename
`nassie_0.1.0_all.deb` — if you bump the version, update that filename
references in both scripts to match (or the build will look for/produce a
file under the old name).

## Files in this directory

| File | Purpose |
|---|---|
| `build.sh` | Rebuilds `nassie_0.1.0_all.deb` from `../../src/*.py` |
| `dist.sh` | Runs `build.sh`, then bundles the distributable tarball and its `.sha256` checksum |
| `install.sh` | What end users run — preview, confirm, `apt install` |
| `bootstrap.sh` | `curl \| sh` one-liner: clones the repo, builds, runs `install.sh` |
| `preview.py` | The pre-install curses preview screen (self-contained, no imports from `src/`, so the tarball needs only these files) |
| `nassie/` | The `.deb`'s staged file tree (`DEBIAN/control`, `postinst`, etc.) |
