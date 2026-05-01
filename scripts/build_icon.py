"""Generate Ask.icns from a single procedurally-drawn 1024x1024 PNG.

The mark is a stack of three soft-rounded chat bubbles in a warm violet→
indigo gradient — readable down to 16px because the silhouette is the
identity, not the gradient. Output: assets/Ask.icns + an .iconset folder
that py2app can ingest directly.

Run with the py3.12 venv that has Pillow:
    source venv312/bin/activate && python scripts/build_icon.py
"""
from __future__ import annotations
import math
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICONSET = ASSETS / "Ask.iconset"

# Apple's required sizes for an .icns iconset.
SIZES = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def _rounded_rect_mask(size: int, corner_pct: float = 0.225) -> Image.Image:
    """Apple's macOS Big Sur+ icon corner radius is ~22.5% of the side."""
    radius = int(size * corner_pct)
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return img


def _gradient(size: int) -> Image.Image:
    """Warm violet → deep indigo diagonal gradient."""
    base = Image.new("RGB", (size, size), (0, 0, 0))
    px = base.load()
    top = (167, 130, 255)   # warm violet
    bot = (76, 56, 178)     # deep indigo
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)  # 0..1 along diagonal
            r = int(top[0] + (bot[0] - top[0]) * t)
            g = int(top[1] + (bot[1] - top[1]) * t)
            b = int(top[2] + (bot[2] - top[2]) * t)
            px[x, y] = (r, g, b)
    return base


def _bubble(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color: tuple[int, int, int, int]) -> None:
    """A chat-bubble silhouette: rounded rect + small tail."""
    w, h = r * 2.0, r * 1.45
    box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    radius = h * 0.32
    draw.rounded_rectangle(box, radius=radius, fill=color)
    # Tail at lower-left
    tail = [
        (cx - w * 0.25, cy + h / 2 - 1),
        (cx - w * 0.42, cy + h / 2 + h * 0.20),
        (cx - w * 0.10, cy + h / 2 - 1),
    ]
    draw.polygon(tail, fill=color)


def render_master(size: int = 1024) -> Image.Image:
    bg = _gradient(size).convert("RGBA")
    draw = ImageDraw.Draw(bg)
    # Subtle highlight: a faint white radial top-left
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse((-size * 0.4, -size * 0.4, size * 0.7, size * 0.7), fill=(255, 255, 255, 60))
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.10))
    bg = Image.alpha_composite(bg, glow)
    draw = ImageDraw.Draw(bg)

    # Three stacked bubbles, slight offset for depth
    bubble_color = (255, 255, 255, 235)
    cx = size * 0.50
    cy = size * 0.52
    r = size * 0.22
    _bubble(draw, cx + size * 0.06, cy - size * 0.16, r * 0.85, (255, 255, 255, 130))
    _bubble(draw, cx - size * 0.08, cy - size * 0.04, r * 0.95, (255, 255, 255, 180))
    _bubble(draw, cx + size * 0.02, cy + size * 0.10, r, bubble_color)

    # Apply the standard rounded-square mask so it fits the macOS grid.
    mask = _rounded_rect_mask(size).resize((size, size), Image.LANCZOS)
    final = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    final.paste(bg, (0, 0), mask=mask)
    return final


def main() -> None:
    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir(parents=True)
    master = render_master(1024)
    master_path = ASSETS / "Ask-1024.png"
    master.save(master_path, "PNG")

    for filename, size in SIZES:
        img = master.resize((size, size), Image.LANCZOS)
        img.save(ICONSET / filename, "PNG")

    icns_path = ASSETS / "Ask.icns"
    # iconutil ships with macOS — turns the .iconset folder into a single .icns.
    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(icns_path)],
        check=True,
    )
    print(f"wrote {icns_path}")


if __name__ == "__main__":
    main()
