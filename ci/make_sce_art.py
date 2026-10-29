#!/usr/bin/env python3
"""The home screen art for the app's sce_sys: icon0.png (512x512) and
pic0.dds / pic1.dds (3840x2160 BC7, as the PS5 takes them).

    make_sce_art.py <sce_sys dir> [--icon <picture>] [--background <picture>]
                    [--title-font <ttf>]

With --icon / --background (the user's own art, kept out of git:
MaxPayne3Logo.jpg and MaxPayne3Background.jpg) those pictures are used:
the icon squared to 512x512, the background cut to 16:9 and scaled to 4K,
encoded as BC7 mode 6 (two endpoints and 16 levels per 4x4 block; needs
numpy). "RePrescribed" goes under each one's MAX PAYNE in red, in
--title-font (Impact is closest to the game's lettering; narrowed to its
width): on the background under the red title (found by its colour), on
the icon under the cover's white title. Without them a title card is drawn with Pillow and stored with one
colour per 4x4 block (no numpy needed: the CI build).
"""
import argparse

from PIL import Image, ImageDraw, ImageFont

TITLE = "MAX PAYNE 3"
SUB = "RePrescribed"
BG_TOP = (18, 10, 10)
BG_BOTTOM = (70, 8, 8)
FG = (235, 225, 210)
ACCENT = (200, 30, 30)

# DDS header (DX10, BC7_UNORM 98, 3840x2160, one mip) as the PS5 apps ship it.
DDS_HEADER = bytes.fromhex(
    "444453207c0000000710080070080000000f000000907e00000000000100000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000002000000004000000445831300000000000000000000000000000000000000000"
    "001000000000000000000000000000000000000062000000030000000000000001000000"
    "00000000")
W, H = 3840, 2160


def font(size):
    for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def picture(w, h, title_size, sub_size):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    tf, sf = font(title_size), font(sub_size)
    tw = d.textlength(TITLE, font=tf)
    sw = d.textlength(SUB, font=sf)
    ty = h * 0.40
    d.text(((w - tw) / 2, ty), TITLE, font=tf, fill=FG)
    d.rectangle([(w - tw) / 2, ty + title_size * 1.15, (w + tw) / 2, ty + title_size * 1.15 + max(2, h // 120)],
                fill=ACCENT)
    d.text(((w - sw) / 2, ty + title_size * 1.4), SUB, font=sf, fill=FG)
    return img


# The icon's (the cover's) MAX PAYNE, as fractions of the picture: left, top, right, bottom.
ICON_TITLE = (0.012, 0.545, 0.875, 0.69)


def red_title_box(img):
    """The background's red MAX PAYNE: the box round its pure red pixels."""
    import numpy as np

    a = np.asarray(img.convert("RGB")).astype(int)
    ys, xs = np.nonzero((a[..., 0] > 150) & (a[..., 1] < 60) & (a[..., 2] < 60))
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def add_subtitle(img, box, font_path, cap, outline=False):
    """SUB in red under the title in `box` (left-aligned with it), its capitals
    `cap` of the title's height: the title's top-to-bottom red shading, the
    letters narrowed to 86% (the game's condensed lettering); `outline`: a dark
    edge and shadow, for a busy picture."""
    img = img.convert("RGB")
    x0, y0, x1, y1 = box
    th = y1 - y0
    probe = ImageFont.truetype(font_path, 200)
    cap_px = probe.getbbox("R")[3] - probe.getbbox("R")[1]
    fnt = ImageFont.truetype(font_path, max(8, round(200 * cap * th / cap_px)))
    l, t, r, b = fnt.getbbox(SUB)
    pad = max(4, th // 8)
    mask = Image.new("L", (r - l + 2 * pad, b - t + 2 * pad))
    ImageDraw.Draw(mask).text((pad - l, pad - t), SUB, font=fnt, fill=255)
    mask = mask.resize((round(mask.width * 0.86), mask.height), Image.LANCZOS)
    # the title's shading: darker at the top and bottom, full red in the middle
    fill = Image.new("RGB", mask.size)
    d = ImageDraw.Draw(fill)
    for y in range(mask.height):
        f = min(1.0, max(0.0, (y - pad) / max(1, mask.height - 2 * pad)))
        k = 0.72 + 0.28 * min(1.0, 1 - abs(f - 0.45) * 2.2) if f < 0.45 else 0.86 + 0.14 * (1 - (f - 0.45) / 0.55)
        d.line([(0, y), (mask.width, y)], fill=(int(253 * k), 0, int(4 * k)))
    pos = (x0 + round(th * 0.02) - pad, y1 + round(th * 0.20) - pad)
    if outline:
        from PIL import ImageFilter

        edge = mask.filter(ImageFilter.MaxFilter(2 * max(1, th // 40) + 1))
        shadow = edge.filter(ImageFilter.GaussianBlur(max(2, th // 14)))
        img.paste((0, 0, 0), (pos[0] + th // 30, pos[1] + th // 25), shadow)
        img.paste((10, 4, 4), pos, edge)
    img.paste(fill, pos, mask)
    return img


def cover(img, w, h):
    """`img` scaled to cover w x h and cut to it (centred)."""
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    big = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    x, y = (big.width - w) // 2, (big.height - h) // 2
    return big.crop((x, y, x + w, y + h))


# ---- BC7 ----

def bc7_mode6_solid(r, g, b, a=255):
    """One BC7 block, every texel (r, g, b, a): mode 6, both endpoints equal."""
    p = (r + g + b) // 3 & 1  # one shared low bit per endpoint
    c = [r >> 1, g >> 1, b >> 1, a >> 1]
    bits, n = 1 << 6, 7  # mode 6: bit 6 set
    for ch in c:
        for _ in range(2):  # endpoint 0 and 1
            bits |= ch << n
            n += 7
    bits |= p << n
    bits |= p << (n + 1)
    return bits.to_bytes(16, "little")  # indices all 0


def bc7_flat(img):
    """The picture at a quarter size, one colour per block (no numpy)."""
    small = img.resize((W // 4, H // 4), Image.LANCZOS)
    px = small.load()
    out = bytearray()
    cache = {}
    for y in range(small.height):
        for x in range(small.width):
            c = px[x, y]
            blk = cache.get(c)
            if blk is None:
                blk = cache[c] = bc7_mode6_solid(*c)
            out += blk
    return bytes(out)


def bc7_mode6(img):
    """BC7 mode 6, opaque: per block, the colours' range along their main axis
    as the two endpoints (odd 8-bit values: p-bit 1, so alpha is 255) and each
    texel's nearest of the 16 levels between them."""
    import numpy as np

    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    bh, bw = H // 4, W // 4
    blocks = a.reshape(bh, 4, bw, 4, 3).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 3)
    mean = blocks.mean(axis=1, keepdims=True)
    cen = blocks - mean
    # the main axis: a few power iterations on the covariance
    cov = np.einsum("nki,nkj->nij", cen, cen)
    axis = np.ones((len(blocks), 3), np.float32)
    for _ in range(6):
        axis = np.einsum("nij,nj->ni", cov, axis)
        axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-6)
    t = np.einsum("nki,ni->nk", cen, axis)
    e0 = mean[:, 0] + axis * t.min(axis=1, keepdims=True)
    e1 = mean[:, 0] + axis * t.max(axis=1, keepdims=True)
    # endpoints as 7 bits + p-bit 1: 8-bit value 2v+1
    q0 = np.clip(np.round((e0 - 1) / 2), 0, 127).astype(np.int64)
    q1 = np.clip(np.round((e1 - 1) / 2), 0, 127).astype(np.int64)
    c0, c1 = (q0 * 2 + 1)[:, None, :], (q1 * 2 + 1)[:, None, :]
    weights = np.array([0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64], np.int64)
    pal = ((64 - weights)[None, :, None] * c0 + weights[None, :, None] * c1 + 32) >> 6  # n,16,3
    idx = np.empty((len(blocks), 16), np.int64)
    for s in range(0, len(blocks), 65536):
        d = ((blocks[s:s + 65536, :, None, :] - pal[s:s + 65536, None, :, :].astype(np.float32)) ** 2).sum(-1)
        idx[s:s + 65536] = d.argmin(-1)
    # the first texel's index must have its top bit clear: swap the endpoints where it hasn't
    swap = idx[:, 0] >= 8
    q0[swap], q1[swap] = q1[swap].copy(), q0[swap].copy()
    idx[swap] = 15 - idx[swap]

    out = np.zeros((len(blocks), 2), np.uint64)  # low and high 64 bits
    def put(value, pos, nbits):
        value = value.astype(np.uint64)
        if pos + nbits <= 64:
            out[:, 0] |= value << np.uint64(pos)
        elif pos >= 64:
            out[:, 1] |= value << np.uint64(pos - 64)
        else:
            k = 64 - pos
            out[:, 0] |= (value & np.uint64((1 << k) - 1)) << np.uint64(pos)
            out[:, 1] |= value >> np.uint64(k)
        return pos + nbits
    pos = put(np.full(len(blocks), 1 << 6), 0, 7)
    for ch in range(3):
        pos = put(q0[:, ch], pos, 7)
        pos = put(q1[:, ch], pos, 7)
    pos = put(np.full(len(blocks), 127), pos, 7)  # alpha
    pos = put(np.full(len(blocks), 127), pos, 7)
    pos = put(np.ones(len(blocks)), pos, 1)  # p-bits
    pos = put(np.ones(len(blocks)), pos, 1)
    pos = put(idx[:, 0], pos, 3)
    for i in range(1, 16):
        pos = put(idx[:, i], pos, 4)
    assert pos == 128
    return out.astype("<u8").tobytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--icon")
    ap.add_argument("--background")
    ap.add_argument("--title-font")
    args = ap.parse_args()
    if args.icon:
        icon = Image.open(args.icon).convert("RGB")
        if args.title_font:
            w, h = icon.size
            box = tuple(round(f * s) for f, s in zip(ICON_TITLE, (w, h, w, h)))
            icon = add_subtitle(icon, box, args.title_font, 0.42, outline=True)
        cover(icon, 512, 512).save(f"{args.out}/icon0.png", optimize=True)
    else:
        picture(512, 512, 78, 26).save(f"{args.out}/icon0.png")
    if args.background:
        bg = Image.open(args.background).convert("RGB")
        if args.title_font:
            bg = add_subtitle(bg, red_title_box(bg), args.title_font, 0.42)
        data = bc7_mode6(cover(bg, W, H))
    else:
        data = bc7_flat(picture(1920, 1080, 190, 52))
    for name in ("pic0.dds", "pic1.dds"):
        with open(f"{args.out}/{name}", "wb") as f:
            f.write(DDS_HEADER + data)
    print("icon0.png, pic0.dds, pic1.dds")


if __name__ == "__main__":
    main()
