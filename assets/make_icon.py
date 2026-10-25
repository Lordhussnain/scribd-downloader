"""Regenerate assets/icon.png and assets/icon.ico.

Draws a simple, recognisable mark at 1024x1024 (teal rounded square, white
document, teal download arrow) and exports a multi-resolution Windows .ico.

Usage:  python assets/make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
TEAL = (12, 122, 120, 255)
TEAL_DARK = (9, 95, 94, 255)
WHITE = (255, 255, 255, 255)


def draw_icon(size=SIZE):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = size / 1024  # scale factor

    def box(x0, y0, x1, y1):
        return [x0 * s, y0 * s, x1 * s, y1 * s]

    # Rounded-square background with a subtle darker bottom edge.
    draw.rounded_rectangle(box(24, 24, 1000, 1000), radius=210 * s, fill=TEAL)
    draw.rounded_rectangle(box(24, 880, 1000, 1000), radius=60 * s, fill=TEAL_DARK)
    draw.rectangle(box(24, 800, 1000, 940), fill=TEAL)

    # Document page.
    draw.rounded_rectangle(box(272, 150, 752, 880), radius=42 * s, fill=WHITE)
    # Folded top-right corner.
    draw.polygon(
        [(752 * s, 150 * s), (752 * s, 300 * s), (602 * s, 150 * s)], fill=(214, 228, 228, 255)
    )

    # Download arrow (shaft + head + tray line), centred on the page.
    draw.rounded_rectangle(box(462, 330, 562, 585), radius=24 * s, fill=TEAL)
    draw.polygon(
        [(382 * s, 540 * s), (642 * s, 540 * s), (512 * s, 700 * s)], fill=TEAL
    )
    draw.rounded_rectangle(box(392, 740, 632, 782), radius=21 * s, fill=TEAL)
    return image


def main():
    assets = Path(__file__).resolve().parent
    icon = draw_icon()
    icon.save(assets / "icon.png")
    icon.save(
        assets / "icon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Wrote {assets / 'icon.png'} and {assets / 'icon.ico'}")


if __name__ == "__main__":
    main()
