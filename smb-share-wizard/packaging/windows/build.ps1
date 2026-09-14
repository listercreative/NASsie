# Build NASsie.msi on a real Windows machine.
# Run from this directory (packaging\windows) in PowerShell.
#
# Prerequisites:
#   - Python 3.8+ on PATH
#   - .NET SDK (for the WiX v5 CLI): https://dotnet.microsoft.com/download
#   - UPX on PATH, optional but strongly recommended - PyInstaller detects
#     and uses it automatically with no flag needed here, compressing the
#     bundled Qt/Tcl-Tk DLLs and .pyd files (confirmed live: this is where
#     virtually all of NASsie.exe's size actually lives - the MSI itself
#     wraps nothing else, see nassie.wxs's single <File>). Skipping this
#     silently ships an uncompressed - and noticeably larger - .exe, not a
#     build failure, so it's easy to not notice it's missing.
#     https://github.com/upx/upx/releases - unzip and put upx.exe on PATH.
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

if (-not (Get-Command upx.exe -ErrorAction SilentlyContinue)) {
    Write-Warning "upx.exe not found on PATH - building without compression. NASsie.exe will be noticeably larger than a UPX-compressed build. See this script's own header comment for where to get it."
}

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
# gui_qt/tour_qt + --collect-all=PySide6.Qt{Core,Gui,Widgets}: PySide6
# is the only desktop GUI now - the original Tk GUIWizard (gui.py),
# its tour classes (tour.py), theme package (nassie_ttk/), and window-
# chrome helpers (window_corners.py, anim_debug.py) were removed from
# the repo entirely (see gui_qt.py's own module docstring and the
# migration plan's Phase 7), not just excluded from this build - so
# there's nothing left to exclude for them here either. A PLAIN
# --collect-all=PySide6 (no submodule suffix, what this used to say)
# was tried first and confirmed live to bloat NASsie.msi past 250MB -
# it bundles the ENTIRE PySide6 package regardless of what's actually
# imported, QtWebEngine (its own bundled Chromium), Qt3D, QtQml/
# QtQuick, QtMultimedia, QtPdf, and dozens of language translation
# files included, none of which gui_qt.py/tour_qt.py import (confirmed
# via grep - only QtCore, QtGui, QtWidgets). Scoped to just those three
# submodules instead: each --collect-all=PySide6.<X> below still pulls
# in that submodule's own required plugin DLLs (platform backend, image
# formats, ...) exactly the way the unscoped flag did - it's PySide6 in
# full that's the unnecessary part, not --collect-all itself.
#
# --exclude-module=tkinter: nothing left in the codebase imports it at
# all (confirmed live - splitting tour_state.py/x11_error_handler.py
# out of the old tour.py/window_corners.py, before those were deleted,
# is what actually broke the last transitive pulls), so this is a cheap
# defensive backstop against ever silently reintroducing it, not a
# fix for a current pull.
python -m PyInstaller `
  --onefile `
  --windowed `
  --uac-admin `
  --name NASsie `
  --icon (Join-Path $PSScriptRoot "nassie_icon.ico") `
  --add-data "$(Join-Path $RepoSrc 'nassie_icon.png');." `
  --add-data "$(Join-Path $RepoSrc 'icons\*.png');icons" `
  --hidden-import=core --hidden-import=cli --hidden-import=gui_qt --hidden-import=tui --hidden-import=tour_state --hidden-import=tour_qt --hidden-import=x11_error_handler --hidden-import=tty_debug `
  --exclude-module=tkinter `
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
