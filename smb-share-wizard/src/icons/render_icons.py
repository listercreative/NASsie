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
CANVAS = STRIKE + 20
OUT_SIZE = 96  # gui.py's _load_icon() subsample()s this down - matches nassie_icon.png's own pattern
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

emoji_font = ImageFont.truetype(EMOJI_FONT, size=STRIKE)


def render_glyph(canvas, glyph, x, y):
    d = ImageDraw.Draw(canvas)
    d.text((x, y), glyph, font=emoji_font, embedded_color=True)


def finish(canvas, name, pad_frac=0.08):
    bbox = canvas.getbbox()
    if bbox is None:
        raise ValueError(f"{name}: nothing rendered")
    cropped = canvas.crop(bbox)
    side = max(cropped.width, cropped.height)
    pad = int(side * pad_frac)
    square = Image.new("RGBA", (side + 2 * pad, side + 2 * pad), (0, 0, 0, 0))
    square.paste(cropped, ((square.width - cropped.width) // 2, (square.height - cropped.height) // 2), cropped)
    final = square.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
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
    render_glyph(canvas, glyph, 10, 10)
    finish(canvas, name)

# "+👤" (New User) - a plain "+" has no glyph in the color-emoji font, so
# it's drawn from a bundled sans-bold font instead, composed to the left
# of the same person emoji used for icon_users, matching how the two
# characters currently sit side-by-side as button text.
canvas = Image.new("RGBA", (CANVAS + 60, CANVAS), (0, 0, 0, 0))
plus_font = ImageFont.truetype(TEXT_FONT, size=72)
d = ImageDraw.Draw(canvas)
d.text((0, 18), "+", font=plus_font, fill=(51, 51, 51, 255))
render_glyph(canvas, "\U0001F464", 60, 10)
finish(canvas, "icon_new_user")

print("done:", sorted(f for f in os.listdir(OUT_DIR) if f.endswith(".png")))
