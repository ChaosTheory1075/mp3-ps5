#!/usr/bin/env python3
"""One captured frame (logs/mp3_frame_<s>.bin, see ci/patch_framework.py) as
images and a pass listing.

    mp3_frame.py mp3_frame_150.bin out_folder

out_folder/frame.txt lists every draw and resolve in order: render target
and depth formats, the shaders, the textures sampled, and for resolves the
destination format, size and brightness (mean, max) of what was resolved.
Each resolve's image is copy_<seq>_<fmt>_<w>x<h>.png (floats tone-mapped
by a fixed exposure, so a dark pass looks dark).
"""
import os
import struct
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mp3_texture_pack import tiled_offset_2d  # noqa: E402

REGS = ["SURFACE_INFO", "COLOR_INFO", "COLOR1_INFO", "COLOR2_INFO", "COLOR3_INFO", "DEPTH_INFO",
        "COLOR_MASK", "DEPTHCONTROL", "MODECONTROL", "BLENDCONTROL0", "COPY_CONTROL",
        "COPY_DEST_INFO", "COPY_DEST_PITCH", "COPY_DEST_BASE"]
RT_FORMATS = {0: "8888", 1: "8888_GAMMA", 2: "2_10_10_10", 3: "7e3", 4: "16_16", 5: "16x4",
              6: "16_16_F", 7: "16x4_F", 10: "2_10_10_10_AS_10", 12: "7e3_AS_16", 14: "32_F",
              15: "32_32_F"}
# Texture (resolve destination) formats: bytes per pixel.
TEX_BPP = {2: 1, 6: 4, 7: 4, 10: 2, 16: 4, 17: 4, 24: 2, 25: 4, 26: 8, 30: 2, 31: 4, 32: 8,
           33: 4, 36: 4, 37: 8, 38: 16, 50: 4, 54: 4}
TEX_NAMES = {2: "8", 6: "8888", 7: "2_10_10_10", 10: "8_8", 16: "10_11_11", 17: "11_11_10",
             24: "16", 25: "16_16", 26: "16x4", 30: "16_F", 31: "16_16_F", 32: "16x4_F",
             33: "32", 36: "32_F", 37: "32_32_F", 38: "32x4_F", 50: "8888_AS16",
             54: "2_10_10_10_AS16"}


def bits(v, lo, n):
    return (v >> lo) & ((1 << n) - 1)


def sbits(v, lo, n):
    x = bits(v, lo, n)
    return x - (1 << n) if x >> (n - 1) else x


def swap(data, endian):
    a = np.frombuffer(data, dtype=np.uint8)
    if endian == 1:  # 8in16
        return a.reshape(-1, 2)[:, ::-1].tobytes()
    if endian == 2:  # 8in32
        return a.reshape(-1, 4)[:, ::-1].tobytes()
    if endian == 3:  # 16in32
        return a.reshape(-1, 2, 2)[:, ::-1, :].tobytes()
    return data


def untile(data, width, height, pitch, bpp):
    """Linear pixels from a 2D tiled resolve destination."""
    log2 = bpp.bit_length() - 1
    out = bytearray(width * height * bpp)
    for y in range(height):
        for x0 in range(0, width, 8):
            for x in range(x0, min(x0 + 8, width)):
                o = tiled_offset_2d(x, y, pitch, log2)
                if o + bpp <= len(data):
                    d = (y * width + x) * bpp
                    out[d:d + bpp] = data[o:o + bpp]
    return bytes(out)


def to_rgb(pixels, fmt, width, height, dest_swap):
    """float32 HxWx4 linear values."""
    if fmt in (6, 50):
        a = np.frombuffer(pixels, np.uint8).reshape(height, width, 4).astype(np.float32) / 255
    elif fmt in (7, 54):
        v = np.frombuffer(pixels, "<u4").reshape(height, width)
        a = np.stack([(v & 1023) / 1023, ((v >> 10) & 1023) / 1023, ((v >> 20) & 1023) / 1023,
                      (v >> 30) / 3], -1).astype(np.float32)
    elif fmt in (16, 17):
        v = np.frombuffer(pixels, "<u4").reshape(height, width)
        a = np.stack([(v & 2047) / 2047, ((v >> 11) & 2047) / 2047, (v >> 22) / 1023,
                      np.ones_like(v, dtype=np.float32)], -1).astype(np.float32)
    elif fmt in (30, 31, 32):
        n = {30: 1, 31: 2, 32: 4}[fmt]
        a = np.frombuffer(pixels, "<f2").reshape(height, width, n).astype(np.float32)
    elif fmt in (36, 37, 38):
        n = {36: 1, 37: 2, 38: 4}[fmt]
        a = np.frombuffer(pixels, "<f4").reshape(height, width, n).astype(np.float32)
    elif fmt in (24, 25, 26):
        n = {24: 1, 25: 2, 26: 4}[fmt]
        a = np.frombuffer(pixels, "<u2").reshape(height, width, n).astype(np.float32) / 65535
    elif fmt == 33:
        a = (np.frombuffer(pixels, "<u4").reshape(height, width, 1) >> 8).astype(np.float32) / 0xFFFFFF
    elif fmt == 10:
        a = np.frombuffer(pixels, np.uint8).reshape(height, width, 2).astype(np.float32) / 255
    elif fmt == 2:
        a = np.frombuffer(pixels, np.uint8).reshape(height, width, 1).astype(np.float32) / 255
    else:
        return None
    if a.shape[2] < 3:
        a = np.concatenate([a] + [a[:, :, :1]] * (3 - a.shape[2]), -1)
    a = a[:, :, :3]
    if dest_swap:
        a = a[:, :, ::-1]
    return a


def read(path):
    f = open(path, "rb").read()
    assert f[:4] == b"MP3F", "not a frame capture"
    frame = struct.unpack_from("<Q", f, 8)[0]
    p = 16
    shaders, records = {}, []
    while p < len(f):
        tag = f[p:p + 1]
        p += 1
        if tag == b"S":
            h, kind, n = struct.unpack_from("<QII", f, p)
            p += 16
            shaders[h] = (kind, struct.unpack_from("<%dI" % n, f, p))
            p += 4 * n
        elif tag == b"D":
            seq = struct.unpack_from("<I", f, p)[0]
            regs = dict(zip(REGS, struct.unpack_from("<14I", f, p + 4)))
            prim, count, vs, ps = struct.unpack_from("<IIQQ", f, p + 60)
            consts = np.frombuffer(f, "<f4", 1024, p + 84).reshape(256, 4)
            p += 84 + 4096
            n = struct.unpack_from("<I", f, p)[0]
            p += 4
            fetches = []
            for _ in range(n):
                fc = struct.unpack_from("<I", f, p)[0]
                fetches.append((fc, struct.unpack_from("<6I", f, p + 4)))
                p += 28
            records.append(("D", seq, regs, dict(prim=prim, count=count, vs=vs, ps=ps,
                                                  consts=consts, fetches=fetches)))
        elif tag == b"C":
            seq = struct.unpack_from("<I", f, p)[0]
            regs = dict(zip(REGS, struct.unpack_from("<14I", f, p + 4)))
            ok, addr, length = struct.unpack_from("<III", f, p + 60)
            p += 72
            records.append(("C", seq, regs, dict(ok=ok, addr=addr, data=f[p:p + length])))
            p += length
        else:
            raise SystemExit(f"bad record {tag!r} at {p - 1}")
    return frame, shaders, records


def rt_text(regs):
    surf = regs["SURFACE_INFO"]
    parts = [f"pitch {bits(surf, 0, 14)} msaa {bits(surf, 16, 2)}"]
    mask = regs["COLOR_MASK"]
    for i, name in enumerate(["COLOR_INFO", "COLOR1_INFO", "COLOR2_INFO", "COLOR3_INFO"]):
        if (mask >> (4 * i)) & 0xF:
            v = regs[name]
            parts.append(f"rt{i} {RT_FORMATS.get(bits(v, 16, 4), bits(v, 16, 4))}@{bits(v, 0, 12)}"
                         f"{' bias%d' % sbits(v, 20, 6) if sbits(v, 20, 6) else ''}")
    dc = regs["DEPTHCONTROL"]
    if dc & 6:
        d = regs["DEPTH_INFO"]
        parts.append(f"depth {'f24' if bits(d, 16, 1) else 'd24'}@{bits(d, 0, 12)}")
    return " ".join(parts)


def fetch_text(fc, dw):
    fmt = dw[1] & 0x3F
    base = (dw[1] >> 12) << 12
    w, h = (dw[2] & 0x1FFF) + 1, ((dw[2] >> 13) & 0x1FFF) + 1
    kind = dw[0] & 3
    return f"t{fc}:{TEX_NAMES.get(fmt, fmt)} {w}x{h} @{base:08X}{'' if kind == 2 else ' type%d' % kind}"


def main():
    frame, shaders, records = read(sys.argv[1])
    out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    lines = [f"frame {frame}: {sum(r[0] == 'D' for r in records)} draws, "
             f"{sum(r[0] == 'C' for r in records)} resolves, {len(shaders)} shaders"]
    resolved = {}
    last_rt = None
    for kind, seq, regs, x in records:
        if kind == "D":
            rt = rt_text(regs)
            if rt != last_rt:
                lines.append(f"  -- {rt}")
                last_rt = rt
            tex = " ".join(fetch_text(fc, dw) for fc, dw in x["fetches"])
            src = []
            for fc, dw in x["fetches"]:
                b = (dw[1] >> 12) << 12
                for a, name in resolved.items():
                    if a == b:
                        src.append(f"t{fc}<-{name}")
            lines.append(f"  D{seq} prim {x['prim']} n {x['count']} vs {x['vs']:016x} ps {x['ps']:016x} "
                         f"{tex} {' '.join(src)}")
            continue
        info, pitch_reg = regs["COPY_DEST_INFO"], regs["COPY_DEST_PITCH"]
        ctl = regs["COPY_CONTROL"]
        fmt = bits(info, 7, 6)
        endian, dswap = bits(info, 0, 3), bits(info, 24, 1)
        width, height = bits(pitch_reg, 0, 14), bits(pitch_reg, 16, 14)
        base = regs["COPY_DEST_BASE"]
        src = bits(ctl, 0, 3)
        surf_src = (f"depth {'f24' if bits(regs['DEPTH_INFO'], 16, 1) else 'd24'}" if src == 4 else
                    f"rt{src} " + RT_FORMATS.get(bits(regs[['COLOR_INFO', 'COLOR1_INFO', 'COLOR2_INFO',
                                                        'COLOR3_INFO'][src & 3]], 16, 4), "?"))
        name = f"C{seq}"
        resolved[base & ~0xFFF] = name
        line = (f"  C{seq} {surf_src} -> {TEX_NAMES.get(fmt, fmt)} {width}x{height} @{base:08X} "
                f"endian {endian}{' swap' if dswap else ''} exp {sbits(info, 16, 6)} "
                f"cmd {bits(ctl, 20, 2)} clear {bits(ctl, 8, 2)} ok {x['ok']} bytes {len(x['data'])}")
        bpp = TEX_BPP.get(fmt)
        data = x["data"]
        if bpp and data and width and height:
            skip = x["addr"] - base
            buf = b"\0" * max(0, skip) + data[max(0, -skip):]
            buf = swap(buf[:len(buf) // 4 * 4], endian)
            rgb = to_rgb(untile(buf, width, height, width, bpp), fmt, width, height, dswap)
            if rgb is not None:
                finite = np.isfinite(rgb)
                vals = np.where(finite, rgb, 0)
                line += (f" | mean {vals.mean():.4f} max {vals.max():.3f} nan/inf {(~finite).sum()}"
                         f" zero {(vals.max(-1) == 0).mean() * 100:.0f}%")
                img = np.clip(vals, 0, None)
                if fmt in (30, 31, 32, 36, 37, 38):
                    img = img / (1 + img)
                img = (np.clip(img, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
                Image.fromarray(img).save(os.path.join(
                    out, f"copy_{seq:05d}_{TEX_NAMES.get(fmt, fmt)}_{width}x{height}.png"))
        lines.append(line)
    open(os.path.join(out, "frame.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(l for l in lines if l.lstrip().startswith(("C", "frame"))))


if __name__ == "__main__":
    main()
