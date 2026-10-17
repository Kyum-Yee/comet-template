"""UI primitives as PNG (Pillow): panels, icon buttons, invisible hit areas, padding, 16:9 thumbnails.

Everything is drawn at 4x and downsampled, so edges stay clean at the size the layer uses.
Image layers always draw with object-fit 'contain', so make each PNG at the layer's aspect ratio.
"""
from __future__ import annotations

import math

SS = 4  # supersampling factor


def _rgba(c: str):
    from PIL import ImageColor
    c = c.strip()
    if c.startswith("#") and len(c) == 9:
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5, 7))
    rgb = ImageColor.getrgb(c)
    return rgb if len(rgb) == 4 else (*rgb, 255)


def panel(out: str, w: int, h: int, fill="#000000B3", radius=24, border=None, border_width=2, gradient_to=None):
    """Rounded rectangle. gradient_to: fill colour at the bottom edge (vertical gradient)."""
    from PIL import Image, ImageDraw
    W, H = w * SS, h * SS
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1], radius=radius * SS, fill=255)
    top = _rgba(fill)
    if gradient_to:
        bot = _rgba(gradient_to)
        col = Image.new("RGBA", (1, H))
        for y in range(H):
            t = y / max(1, H - 1)
            col.putpixel((0, y), tuple(round(top[i] + (bot[i] - top[i]) * t) for i in range(4)))
        body = col.resize((W, H))
    else:
        body = Image.new("RGBA", (W, H), top)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.paste(body, (0, 0), mask)
    # keep the fill's own alpha inside the mask
    a = Image.composite(body.getchannel("A"), Image.new("L", (W, H), 0), mask)
    img.putalpha(a)
    if border:
        bw = border_width * SS
        ImageDraw.Draw(img).rounded_rectangle([bw // 2, bw // 2, W - 1 - bw // 2, H - 1 - bw // 2],
                                              radius=max(0, radius * SS - bw // 2), outline=_rgba(border), width=bw)
    img.resize((w, h), Image.LANCZOS).save(out)


ICONS = ("arrow-left", "arrow-right", "chevron-left", "chevron-right", "play", "pause", "menu", "close", "plus",
         "minus", "dot", "none")


def _icon(d, kind: str, cx: float, cy: float, r: float, color, stroke: float):
    def line(pts):
        d.line(pts, fill=color, width=round(stroke), joint="curve")
        for p in (pts[0], pts[-1]):
            d.ellipse([p[0] - stroke / 2, p[1] - stroke / 2, p[0] + stroke / 2, p[1] + stroke / 2], fill=color)

    if kind in ("chevron-left", "chevron-right"):
        s = -1 if kind == "chevron-left" else 1
        line([(cx - s * r * 0.25, cy - r * 0.5), (cx + s * r * 0.25, cy), (cx - s * r * 0.25, cy + r * 0.5)])
    elif kind in ("arrow-left", "arrow-right"):
        s = -1 if kind == "arrow-left" else 1
        line([(cx - s * r * 0.55, cy), (cx + s * r * 0.55, cy)])
        line([(cx + s * r * 0.05, cy - r * 0.45), (cx + s * r * 0.55, cy), (cx + s * r * 0.05, cy + r * 0.45)])
    elif kind == "play":
        d.polygon([(cx - r * 0.3, cy - r * 0.5), (cx + r * 0.5, cy), (cx - r * 0.3, cy + r * 0.5)], fill=color)
    elif kind == "pause":
        for dx in (-0.25, 0.25):
            d.rounded_rectangle([cx + dx * r - r * 0.1, cy - r * 0.45, cx + dx * r + r * 0.1, cy + r * 0.45], radius=r * 0.05, fill=color)
    elif kind == "menu":
        for dy in (-0.35, 0, 0.35):
            line([(cx - r * 0.5, cy + dy * r), (cx + r * 0.5, cy + dy * r)])
    elif kind == "close":
        line([(cx - r * 0.4, cy - r * 0.4), (cx + r * 0.4, cy + r * 0.4)])
        line([(cx - r * 0.4, cy + r * 0.4), (cx + r * 0.4, cy - r * 0.4)])
    elif kind in ("plus", "minus"):
        line([(cx - r * 0.45, cy), (cx + r * 0.45, cy)])
        if kind == "plus":
            line([(cx, cy - r * 0.45), (cx, cy + r * 0.45)])
    elif kind == "dot":
        d.ellipse([cx - r * 0.3, cy - r * 0.3, cx + r * 0.3, cy + r * 0.3], fill=color)


def button(out: str, size: int = 96, icon="chevron-right", shape="circle", fill="#FFFFFF26", fg="#FFFFFF",
           border=None, pad: int = 0, stroke: float | None = None):
    """Icon button; pad adds a transparent touch margin on every side (the image grows by 2*pad)."""
    from PIL import Image, ImageDraw
    if icon not in ICONS:
        raise ValueError(f"icon은 {', '.join(ICONS)}")
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    box = [0, 0, S - 1, S - 1]
    if shape == "circle":
        d.ellipse(box, fill=_rgba(fill), outline=_rgba(border) if border else None, width=2 * SS if border else 0)
    elif shape == "square":
        d.rounded_rectangle(box, radius=S * 0.22, fill=_rgba(fill), outline=_rgba(border) if border else None, width=2 * SS if border else 0)
    elif shape != "none":
        raise ValueError("shape은 circle|square|none")
    _icon(d, icon, S / 2, S / 2, S / 2, _rgba(fg), stroke * SS if stroke else max(2 * SS, S * 0.07))
    img = img.resize((size, size), Image.LANCZOS)
    if pad:
        big = Image.new("RGBA", (size + 2 * pad, size + 2 * pad), (0, 0, 0, 0))
        big.paste(img, (pad, pad))
        img = big
    img.save(out)


def hit(out: str, w: int = 16, h: int = 9):
    """Fully transparent PNG: an invisible click area. Click tests use the layer box, not pixels."""
    from PIL import Image
    Image.new("RGBA", (w, h), (0, 0, 0, 0)).save(out)


def pad(src: str, out: str, margin: int):
    from PIL import Image
    im = Image.open(src).convert("RGBA")
    big = Image.new("RGBA", (im.width + 2 * margin, im.height + 2 * margin), (0, 0, 0, 0))
    big.paste(im, (margin, margin))
    big.save(out)


def thumbnail(src: str | None, out: str, title: str | None = None, font: str | None = None, bg="#14161F"):
    """16:9, 1280x720. Center-crops src; draws title on a bottom band when given."""
    from PIL import Image, ImageDraw, ImageFont
    from .render import _font_path
    W, H = 1280, 720
    if src:
        im = Image.open(src).convert("RGB")
        r = W / H
        if im.width / im.height > r:
            nw = round(im.height * r)
            im = im.crop(((im.width - nw) // 2, 0, (im.width - nw) // 2 + nw, im.height))
        else:
            nh = round(im.width / r)
            im = im.crop((0, (im.height - nh) // 2, im.width, (im.height - nh) // 2 + nh))
        im = im.resize((W, H), Image.LANCZOS)
    else:
        im = Image.new("RGB", (W, H), _rgba(bg)[:3])
    if title:
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        for y in range(H // 2, H):
            a = int(200 * ((y - H / 2) / (H / 2)) ** 1.4)
            d.line([(0, y), (W, y)], fill=(0, 0, 0, a))
        fp = _font_path(font)
        f = ImageFont.truetype(fp, 84) if fp else ImageFont.load_default()
        d.text((72, H - 72), title, font=f, fill=(255, 255, 255, 255), anchor="ls")
        im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    im.save(out)


def needs_16x9(path: str) -> bool:
    from PIL import Image
    with Image.open(path) as im:
        return not math.isclose(im.width / im.height, 16 / 9, rel_tol=0.01)
