# Windows packaging (.msi)

`kelpie.wxs` builds an MSI using WiX v5's `WixUI_InstallDir` UI —
the standard native Windows Installer wizard (Welcome → License →
install-directory chooser → Progress → Finish). Windows Installer itself
triggers the UAC elevation prompt for the per-machine `Program Files`
install; nothing extra is needed for that part.

The MSI wraps a single `Kelpie.exe` produced by PyInstaller from
`src/main.py` (bundling `core.py`/`cli.py`/`gui.py`/`tui.py`, `rich`, and
`tkinter`), so the target machine does **not** need Python installed. It's
built `--windowed`, so launching it (Start Menu shortcut, installed by this
MSI) opens the GUI directly with no console window.

## Build

On a real Windows machine, from this directory in PowerShell:

```powershell
.\build.ps1
```

This runs PyInstaller, then compiles the MSI with the WiX v5 CLI. See the
prerequisites at the top of `build.ps1`.

## Verification status

I wrote and validated the structure of `kelpie.wxs` against the
real WiX v5.0.2 compiler (installed locally via the cross-platform `wix`
.NET global tool), but **could not fully compile it in this environment**:
WiX explicitly only supports Windows, and on Linux its directory-path
validation (`Directory/@Name` rootedness check) fails even on the most
minimal possible `.wxs` file — confirmed via an isolated minimal
reproduction, so it's a tooling limitation, not a bug in this file. The
XML itself follows the documented WiX v5 schema. First real build/test
needs to happen on Windows (or a Windows CI runner) — treat that as the
next verification step, not an assumption that this already works.

`core.py`'s elevation logic was updated to detect a PyInstaller-frozen
build (`sys.frozen`) and re-invoke the bundled exe directly with
`--apply <file>` instead of assuming a separate `python.exe main.py`
pair, since that's what a real installed copy looks like.

## Branding

`banner.bmp` (493x58) and `dialog.bmp` (493x312) are generated from
`assets/kelpie_icon.png`, flattened onto a white background (BMP has no
alpha channel). To regenerate them after changing the source logo:

```python
from PIL import Image
logo = Image.open("assets/kelpie_icon.png").convert("RGBA")

banner = Image.new("RGBA", (493, 58), (255, 255, 255, 255))
banner.alpha_composite(logo.resize((46, 46), Image.LANCZOS), (10, 6))
banner.convert("RGB").save("packaging/windows/banner.bmp")

dialog = Image.new("RGBA", (493, 312), (255, 255, 255, 255))
dialog.alpha_composite(logo.resize((140, 140), Image.LANCZOS), (12, 86))
dialog.convert("RGB").save("packaging/windows/dialog.bmp")
```
