"""Bakes NASsie's icons into fixed PNG assets so Windows and Linux render
byte-identical images instead of relying on whichever emoji font each OS
happens to ship (Segoe UI Emoji vs Noto Color Emoji), which differ in both
size and color (reported live: Windows and Linux visibly not matching).

Dev-only authoring tool - not imported or shipped by the app itself (see
gui.py's build.sh/build.ps1 entries, which bundle this directory's *.png
output, not this script). Run once per icon set change, from this
directory: `python3 render_icons.py`. Output is committed to the repo and
loaded at runtime via plain tk.PhotoImage (see gui.py's _load_icon()).

Requires Pillow with a color-emoji-capable font available locally (tested
against Debian/Ubuntu's fonts-noto-color-emoji package) - neither is a
runtime dependency of the shipped app, only of running this script.
"""
import os
from PIL import Image, ImageDraw, ImageFont

EMOJI_FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
TEXT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
STRIKE = 109  # NotoColorEmoji's native fixed strike size
# Generous canvas: NotoColorEmoji's bitmaps are wider than the 109px strike
# (~136px), so a tight canvas clipped glyphs like the chain link and key.
CANVAS = STRIKE * 2
OUT_SIZE = 96  # gui.py's _load_icon() subsample()s this down - matches nassie_icon.png's own pattern
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

emoji_font = ImageFont.truetype(EMOJI_FONT, size=STRIKE)


def render_glyph(canvas, glyph, x, y):
    d = ImageDraw.Draw(canvas)
    d.text((x, y), glyph, font=emoji_font, embedded_color=True)


WHITE = (255, 255, 255)
NASSIE_GREEN = (114, 175, 82)  # the logo's own #72af52


def tint(img, rgb):
    """Flat-color silhouette: keep the alpha, replace every pixel's RGB.
    Icons ship white on transparent; gui_qt._icon() re-tints at runtime."""
    r = Image.new("RGBA", img.size, rgb + (255,))
    r.putalpha(img.getchannel("A"))
    return r


def finish(canvas, name, pad_frac=0.1):
    bbox = canvas.getbbox()
    if bbox is None:
        raise ValueError(f"{name}: nothing rendered")
    cropped = canvas.crop(bbox)
    side = max(cropped.width, cropped.height)
    pad = int(side * pad_frac)
    square = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), (0, 0, 0, 0))
    square.paste(cropped, ((square.width - cropped.width) // 2, (square.height - cropped.height) // 2), cropped)
    final = square.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
    final = tint(final, NASSIE_GREEN if name == "icon_add" else WHITE)
    final.save(os.path.join(OUT_DIR, f"{name}.png"))
    print(f"{name}: source bbox {bbox}, saved {OUT_SIZE}x{OUT_SIZE}")


SIMPLE = {
    "icon_cancel": "✖",
    "icon_ok": "✔",
    "icon_browse": "\U0001F4C2",    # 📂
    "icon_key": "\U0001F511",       # 🔑
    "icon_delete": "\U0001F5D1",    # 🗑
    "icon_back": "◀",
    "icon_readonly": "\U0001F4D6",  # 📖
    "icon_readwrite": "\U0001F4DD", # 📝
    "icon_qr": "\U0001F4F7",        # 📷
    "icon_detach": "➖",
    "icon_attach": "\U0001F517",    # 🔗
    "icon_users": "\U0001F464",     # 👤
    "icon_log": "\U0001F4DC",       # 📜
    "icon_add": "➕",
}

for name, glyph in SIMPLE.items():
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    render_glyph(canvas, glyph, 20, 20)
    finish(canvas, name)

# "+👤" (New User) - a plain "+" has no glyph in the color-emoji font, so
# it's drawn from a bundled sans-bold font instead, composed to the left
# of the same person emoji used for icon_users, matching how the two
# characters currently sit side-by-side as button text.
canvas = Image.new("RGBA", (CANVAS + 80, CANVAS), (0, 0, 0, 0))
plus_font = ImageFont.truetype(TEXT_FONT, size=72)
d = ImageDraw.Draw(canvas)
d.text((20, 38), "+", font=plus_font, fill=(51, 51, 51, 255))
render_glyph(canvas, "\U0001F464", 80, 20)
finish(canvas, "icon_new_user")


# --- Hand-drawn line icons ---------------------------------------------
# The emoji versions of these collapse into featureless blobs once
# flattened to a single color (a solid square for read/write, a blob for
# the QR/camera), so they're drawn as simple shapes instead.
S = 384  # drawn at 4x, downsampled to OUT_SIZE


def _save_line_icon(name, mask):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.putalpha(mask)
    img = tint(img, WHITE)
    img.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS).save(os.path.join(OUT_DIR, f"{name}.png"))
    print(f"{name}: hand-drawn")


def _new_mask():
    m = Image.new("L", (S, S), 0)
    return m, ImageDraw.Draw(m)


# QR code: three finder squares plus data modules.
m, d = _new_mask()
for x, y in ((40, 40), (232, 40), (40, 232)):
    d.rectangle([x, y, x + 112, y + 112], outline=255, width=24)
    d.rectangle([x + 40, y + 40, x + 72, y + 72], fill=255)
for x, y in ((232, 232), (296, 232), (232, 296), (296, 296), (264, 264)):
    d.rectangle([x, y, x + 40, y + 40], fill=255)
_save_line_icon("icon_qr", m)

# Read-only: an eye.
m, d = _new_mask()
d.ellipse([28, 106, 356, 278], outline=255, width=30)
d.ellipse([142, 122, 242, 262], fill=255)
_save_line_icon("icon_readonly", m)

# Read/write: a pencil.
m, d = _new_mask()
ux, uy = 0.7071, -0.7071   # pencil axis, pointing up-right
nx, ny = 0.7071, 0.7071
tip = (58, 300)
def pt(t, w): return (tip[0] + ux * t + nx * w, tip[1] + uy * t + ny * w)
d.polygon([pt(0, 0), pt(80, 42), pt(340, 42), pt(340, -42), pt(80, -42)], fill=255)
d.line([pt(285, -60), pt(285, 60)], fill=0, width=16)
d.line([(40, 352), (344, 352)], fill=255, width=24)
_save_line_icon("icon_readwrite", m)
_save_line_icon("icon_edit", m)  # same pencil, used by the share row's Edit button

# Back: a chevron.
m, d = _new_mask()
d.line([(250, 60), (120, 192), (250, 324)], fill=255, width=48, joint="curve")
for cx, cy in ((250, 60), (250, 324)):
    d.ellipse([cx - 24, cy - 24, cx + 24, cy + 24], fill=255)
_save_line_icon("icon_back", m)

# Log: a page of text lines.
m, d = _new_mask()
d.rounded_rectangle([72, 36, 312, 348], radius=28, outline=255, width=26)
for y in (128, 192, 256):
    d.line([(124, y), (260, y)], fill=255, width=22)
_save_line_icon("icon_log", m)

print("done:", sorted(f for f in os.listdir(OUT_DIR) if f.endswith(".png")))
