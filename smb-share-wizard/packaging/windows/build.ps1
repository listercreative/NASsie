# Build NASsie.msi on a real Windows machine.
# Run from this directory (packaging\windows) in PowerShell.
#
# Prerequisites:
#   - Python 3.8+ on PATH
#   - .NET SDK (for the WiX v5 CLI): https://dotnet.microsoft.com/download
#
# One-time tool setup:
#   dotnet tool install --global wix --version 5.0.2
#   wix extension add --global WixToolset.UI.wixext/5.0.2
#   wix extension add --global WixToolset.Util.wixext/5.0.2

$ErrorActionPreference = "Stop"

# $ErrorActionPreference only catches PowerShell-native errors, not a
# non-zero exit code from an external .exe - check that explicitly after
# each native tool invocation so a failed step can't silently fall through
# to "Built NASsie.msi".
function Assert-LastExitCode($what) {
    if ($LASTEXITCODE -ne 0) {
        throw "$what failed (exit code $LASTEXITCODE)"
    }
}

# Resolve everything from the script's own folder, not the caller's working
# directory: PyInstaller resolves --add-data's relative source path against
# its --specpath (build\), not the invocation directory, so a plain
# "..\..\src\..." here lands one level short. Absolute paths sidestep that
# entirely, and also let this script work no matter where it's invoked from.
# PyInstaller's own build also leaves the process's CWD changed afterward,
# which matters for `wix build`: WiX's extension store is looked up
# relative to CWD unless the extension was added with --global, so pin the
# CWD back before that step regardless.
$RepoSrc = Resolve-Path (Join-Path $PSScriptRoot "..\..\src")

python -m pip install --upgrade pip
python -m pip install pyinstaller rich "qrcode[pil]" PySide6

# Verify the exact same Python that ran pip above can actually import
# everything NASsie needs bundled, before PyInstaller ever runs. Windows
# commonly has more than one Python on PATH (python.org install, Microsoft
# Store stub, Anaconda, ...) - "pip install X" and the bare "pyinstaller"
# command can silently resolve to two different interpreters, so X being
# installed doesn't guarantee PyInstaller's scan will ever see it. This
# turns that class of bug into a loud build failure instead of a
# ModuleNotFoundError inside the shipped .exe.
python -c "import PyInstaller, rich, qrcode, PIL, PySide6"
Assert-LastExitCode "Dependency check (pip install and PyInstaller may be seeing different Pythons - check 'where.exe python')"

# Invoked as "python -m PyInstaller", not the bare "pyinstaller" command -
# guarantees this runs under the exact interpreter just verified above,
# rather than whatever "pyinstaller" happens to resolve to on PATH.
#
# --collect-all=PIL: Pillow registers its image codecs (PNG included) by
# dynamically scanning and importing its own package at runtime
# (PIL.Image.init()) - invisible to PyInstaller's static import analysis,
# so without this the QR feature's PNG save silently fails and leaves a
# blank dialog on screen instead of a visible error.
#
# gui_qt/tour_qt + --collect-all=PySide6.Qt{Core,Gui,Widgets}: the
# in-progress PySide6 rewrite, reachable only via the explicit
# `NASsie.exe --gui-qt` flag (main.py never launches it by default) -
# bundled into this SAME exe rather than a separate build, same as
# gui/tour already are for the default Tk path, so `--gui-qt` works out
# of the box with nothing extra to install. A PLAIN --collect-all=PySide6
# (no submodule suffix, what this used to say) was tried first and
# confirmed live to bloat NASsie.msi past 250MB - it bundles the ENTIRE
# PySide6 package regardless of what's actually imported, QtWebEngine
# (its own bundled Chromium), Qt3D, QtQml/QtQuick, QtMultimedia, QtPdf,
# and dozens of language translation files included, none of which
# gui_qt.py/tour_qt.py import (confirmed via grep - only QtCore, QtGui,
# QtWidgets). Scoped to just those three submodules instead: each
# --collect-all=PySide6.<X> below still pulls in that submodule's own
# required plugin DLLs (platform backend, image formats, ...) exactly
# the way the unscoped flag did - it's PySide6 in full that's the
# unnecessary part, not --collect-all itself - so this should still
# avoid the missing-plugin-DLL failure an earlier no-collect-all attempt
# hit (see the migration plan for that), just without pulling in the
# ~10 unrelated multi-hundred-MB modules alongside it.
python -m PyInstaller `
  --onefile `
  --windowed `
  --uac-admin `
  --name NASsie `
  --icon (Join-Path $PSScriptRoot "nassie_icon.ico") `
  --add-data "$(Join-Path $RepoSrc 'nassie_icon.png');." `
  --add-data "$(Join-Path $RepoSrc 'nassie_ttk');nassie_ttk" `
  --add-data "$(Join-Path $RepoSrc 'icons\*.png');icons" `
  --hidden-import=core --hidden-import=cli --hidden-import=gui --hidden-import=gui_qt --hidden-import=tui --hidden-import=tour --hidden-import=tour_qt --hidden-import=nassie_ttk --hidden-import=window_corners --hidden-import=anim_debug `
  --collect-all=rich `
  --collect-all=qrcode `
  --collect-all=PIL `
  --collect-all=PySide6.QtCore `
  --collect-all=PySide6.QtGui `
  --collect-all=PySide6.QtWidgets `
  --distpath $PSScriptRoot `
  --workpath (Join-Path $PSScriptRoot "build") `
  --specpath (Join-Path $PSScriptRoot "build") `
  (Join-Path $RepoSrc "main.py")
Assert-LastExitCode "pyinstaller"

Set-Location $PSScriptRoot
wix build (Join-Path $PSScriptRoot "nassie.wxs") -ext WixToolset.UI.wixext -ext WixToolset.Util.wixext -out (Join-Path $PSScriptRoot "NASsie.msi")
Assert-LastExitCode "wix build"

Write-Host "Built NASsie.msi"
