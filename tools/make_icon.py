"""Generate the NetWatch brand icons.

Written into custom_components/netwatch/brand/, where HACS validation looks for
them; the same files are what a home-assistant/brands PR needs.

Drawn at 4x and downsampled, because PIL does not antialias strokes: a ring
drawn straight at 256px has visibly stepped edges, while the same ring drawn at
1024 and reduced with LANCZOS comes out clean.

The mark is a radar ping - a node with rings leaving it - with the outer ring
broken into two arcs so it reads as a sweep rather than a static target. Colours
sit mid-tone on purpose: integration icons appear on white cards in light mode
and dark ones in dark mode, so neither near-black nor near-white survives both.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SUPERSAMPLE = 1024
CENTRE = SUPERSAMPLE // 2

CORE = "#0B84F3"
RING = "#2E9BF5"
SWEEP = "#63B8F8"

OUT_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "netwatch" / "brand"


def _bbox(radius: int) -> tuple[int, int, int, int]:
    return (
        CENTRE - radius,
        CENTRE - radius,
        CENTRE + radius,
        CENTRE + radius,
    )


def build() -> Image.Image:
    image = Image.new("RGBA", (SUPERSAMPLE, SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # The node being followed.
    draw.ellipse(_bbox(102), fill=CORE)

    # First echo: a closed ring.
    draw.ellipse(_bbox(238), outline=RING, width=60)

    # Second echo: broken at top and bottom, which suggests rotation. Angles
    # run clockwise from 3 o'clock.
    for start, end in ((-68, 68), (112, 248)):
        draw.arc(_bbox(392), start=start, end=end, fill=SWEEP, width=60)

    return image


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    master = build()

    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        master.resize((size, size), Image.LANCZOS).save(
            OUT_DIR / name, "PNG", optimize=True
        )
        print(f"wrote {name} ({size}x{size})")


if __name__ == "__main__":
    main()
