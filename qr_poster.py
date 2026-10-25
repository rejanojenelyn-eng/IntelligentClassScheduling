"""
QR code poster for the landing page's Share popup.

  • qr_png(url)     – the QR code alone (PUP seal in the centre), for the popup preview.
  • poster_png(url) – the printable "Scan to visit" poster behind Download QR Code.

The QR code is built from the URL it is given (the deployed public URL), never a
stored image. It uses error correction level H (~30% of the symbol may be lost)
and the seal covers well under that, so the code still scans with the logo on
it and after printing. Both images depend only on the URL, so they are cached.
"""
import io
import os
from functools import lru_cache

import segno
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

_BASE = os.path.dirname(os.path.abspath(__file__))
_SEAL = os.path.join(_BASE, 'static', 'landing', 'assets', 'seal-hd.png')
_CAMPUS = os.path.join(_BASE, 'static', 'landing', 'assets', 'campus', 'campus-building.jpg')
_FONTS = os.path.join(_BASE, 'static', 'fonts', 'poster')

MAROON = (128, 0, 0)
MAROON_DARK = (90, 0, 0)
MAROON_MID = (155, 28, 28)
GOLD = (242, 194, 48)
GOLD_DEEP = (201, 150, 20)
INK = (43, 29, 29)

# Poster: 8 x 10 in at 300 dpi.
POSTER_W, POSTER_H, DPI = 2400, 3000, 300
# Seal width as a share of the QR symbol (without quiet zone). ~6% of the area
# with its white pad – far inside what level H can recover.
SEAL_RATIO = 0.24
SS = 4  # supersampling factor for anti-aliased shapes


def _font(name, size, variation=None):
    f = ImageFont.truetype(os.path.join(_FONTS, name), size)
    if variation:
        f.set_variation_by_name(variation)
    return f


def _aa_mask(size, draw_fn):
    """Anti-aliased L mask: draw_fn(draw, scale) paints at SS x, then downsample."""
    w, h = size
    big = Image.new('L', (w * SS, h * SS), 0)
    draw_fn(ImageDraw.Draw(big), SS)
    return big.resize((w, h), Image.LANCZOS)


def _paste_color(img, color, mask, xy=(0, 0)):
    img.paste(Image.new('RGB', mask.size, color), xy, mask)


def _rounded(img, box, radius, color):
    x0, y0, x1, y1 = box
    mask = _aa_mask((x1 - x0, y1 - y0),
                    lambda d, s: d.rounded_rectangle((0, 0, (x1 - x0) * s - 1, (y1 - y0) * s - 1),
                                                     radius * s, fill=255))
    _paste_color(img, color, mask, (x0, y0))


def _spaced_text(draw, center_x, y, text, font, fill, tracking):
    """Text with letter spacing, horizontally centred on center_x."""
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = center_x - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + tracking
    return total


def _spaced_width(draw, text, font, tracking):
    return sum(draw.textlength(ch, font=font) for ch in text) + tracking * (len(text) - 1)


def _qr_image(url, size):
    """Square RGB QR code (quiet zone included) about `size` px wide with the seal centred."""
    qr = segno.make(url, error='h', micro=False, boost_error=False)
    matrix = [list(row) for row in qr.matrix]
    n = len(matrix)
    quiet = 4
    total = n + 2 * quiet
    module = max(1, size // total)   # whole pixels per module keeps edges crisp
    side = total * module

    img = Image.new('RGB', (side, side), 'white')
    draw = ImageDraw.Draw(img)
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                x, y = (c + quiet) * module, (r + quiet) * module
                draw.rectangle((x, y, x + module - 1, y + module - 1), fill=(0, 0, 0))

    # Seal on a white disc, centred on the symbol.
    seal_d = int(n * module * SEAL_RATIO)
    pad = max(module, int(seal_d * 0.06))
    disc_d = seal_d + 2 * pad
    disc = _aa_mask((disc_d, disc_d), lambda d, s: d.ellipse((0, 0, disc_d * s - 1, disc_d * s - 1), fill=255))
    cx = side // 2
    _paste_color(img, (255, 255, 255), disc, (cx - disc_d // 2, cx - disc_d // 2))
    seal = Image.open(_SEAL).convert('RGBA').resize((seal_d, seal_d), Image.LANCZOS)
    img.paste(seal, (cx - seal_d // 2, cx - seal_d // 2), seal)
    return img


def _png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, 'PNG', dpi=(DPI, DPI), optimize=True)
    return buf.getvalue()


@lru_cache(maxsize=8)
def qr_png(url, size=720):
    return _png_bytes(_qr_image(url, size))


def _campus_layer(top, height):
    """Grayscale campus photo, faded to white at the top and bottom edges."""
    photo = ImageOps.grayscale(Image.open(_CAMPUS))
    photo = ImageOps.fit(photo, (POSTER_W, height), Image.LANCZOS, centering=(0.5, 0.35))
    photo = photo.filter(ImageFilter.GaussianBlur(1.2)).convert('RGB')
    fade = Image.new('L', (1, height))
    edge = int(height * 0.28)
    for y in range(height):
        a = min(1.0, y / edge, (height - 1 - y) / edge)
        fade.putpixel((0, y), int(255 * 0.16 * max(0.0, a)))  # 16% opacity at most
    fade = fade.resize((POSTER_W, height))
    return photo, fade, (0, top)


def _bottom_waves(img):
    """Crimson sweep, a gold ribbon rising left to right, and a maroon base along the bottom."""
    import math

    top = POSTER_H - 400
    w, h = POSTER_W, POSTER_H - top

    def curve(base, amp, phase=0.0):
        # y = base + amp*cos(pi*t + phase) for t in 0..1 across the width
        return lambda s: [(w * i / 240 * s, (base + amp * math.cos(math.pi * i / 240 + phase)) * s)
                          for i in range(241)]

    def band(upper, lower=None):
        def paint(d, s):
            pts = upper(s)
            pts += list(reversed(lower(s))) if lower else [(w * s, h * s), (0, h * s)]
            d.polygon(pts, fill=255)
        return paint

    gold_top, gold_bot = curve(200, 120), curve(226, 120)

    region = img.crop((0, top, w, POSTER_H))
    _paste_color(region, MAROON_MID, _aa_mask((w, h), band(curve(150, 70, 0.5))))
    _paste_color(region, GOLD, _aa_mask((w, h), band(gold_top, gold_bot)))
    _paste_color(region, MAROON, _aa_mask((w, h), band(gold_bot)))
    _paste_color(region, GOLD_DEEP, _aa_mask((w, h), band(curve(262, 110), curve(268, 110))))
    _paste_color(region, MAROON_DARK, _aa_mask((w, h), band(curve(345, -25, 0.3))))
    img.paste(region, (0, top))


@lru_cache(maxsize=8)
def poster_png(url):
    img = Image.new('RGB', (POSTER_W, POSTER_H), 'white')
    cx = POSTER_W // 2

    photo, fade, at = _campus_layer(780, 1800)
    img.paste(photo, at, fade)

    draw = ImageDraw.Draw(img)

    # ── Header: seal beside PUP LOPEZ / ACADEMIC SCHEDULING SYSTEM ──
    seal_d = 560
    title_f = _font('LibreBaskerville-VF.ttf', 236, 'Bold')
    sub_f = _font('Poppins-Medium.ttf', 66)
    sub_track = 15
    title = 'PUP LOPEZ'
    subtitle = 'ACADEMIC SCHEDULING SYSTEM'
    title_w = draw.textlength(title, font=title_f)
    sub_w = _spaced_width(draw, subtitle, sub_f, sub_track)
    text_w = max(title_w, sub_w)
    gap = 80
    left = cx - (seal_d + gap + text_w) // 2
    top = 190

    seal = Image.open(_SEAL).convert('RGBA').resize((seal_d, seal_d), Image.LANCZOS)
    img.paste(seal, (int(left), top), seal)

    tx = left + seal_d + gap
    t_box = draw.textbbox((0, 0), title, font=title_f)
    title_y = top + 70 - t_box[1]
    draw.text((tx + (text_w - title_w) / 2, title_y), title, font=title_f, fill=MAROON)
    sub_y = top + 70 + (t_box[3] - t_box[1]) + 70
    _spaced_text(draw, tx + text_w / 2, sub_y, subtitle, sub_f, INK, sub_track)
    rule_y = sub_y + 120
    draw.rectangle((tx, rule_y, tx + text_w, rule_y + 5), fill=MAROON)

    # ── QR code in a rounded maroon frame ──
    frame = 1320
    border = 24
    fx0 = cx - frame // 2
    fy0 = 880
    _rounded(img, (fx0 + 10, fy0 + 18, fx0 + frame + 10, fy0 + frame + 18), 96, (226, 218, 218))  # soft shadow
    _rounded(img, (fx0, fy0, fx0 + frame, fy0 + frame), 96, MAROON)
    _rounded(img, (fx0 + border, fy0 + border, fx0 + frame - border, fy0 + frame - border), 72, (255, 255, 255))
    qr = _qr_image(url, frame - 2 * border - 40)
    img.paste(qr, (cx - qr.width // 2, fy0 + frame // 2 - qr.height // 2))

    # ── SCAN TO VISIT banner ──
    draw = ImageDraw.Draw(img)
    bw, bh = 1040, 176
    by0 = fy0 + frame + 70
    _rounded(img, (cx - bw // 2, by0, cx + bw // 2, by0 + bh), bh // 2, MAROON)
    banner_f = _font('Poppins-SemiBold.ttf', 96)
    b_box = draw.textbbox((0, 0), 'SCAN TO VISIT', font=banner_f)
    b_y = by0 + (bh - (b_box[3] - b_box[1])) / 2 - b_box[1]
    _spaced_text(draw, cx, b_y, 'SCAN TO VISIT', banner_f, (255, 255, 255), 8)

    # ── PUPLC SCHEDULING SYSTEM ──
    caption_f = _font('Poppins-Medium.ttf', 64)
    _spaced_text(draw, cx, by0 + bh + 44, 'PUPLC SCHEDULING SYSTEM', caption_f, (74, 44, 44), 14)

    _bottom_waves(img)
    return _png_bytes(img)
