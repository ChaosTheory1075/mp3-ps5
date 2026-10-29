#!/usr/bin/env python3
"""Build the PS5 app's HD texture folder from PC Max Payne 3 textures.

    mp3_texture_pack.py --x360 DVD1 [--x360 DVD2 ...] --pc <Nexus pack> [--pc <PC game>]
                        --out PPSA95454/textures [--keys <folder with rpf-key-pc.txt, rpf-key-360.txt>]

For every Xbox 360 texture dictionary (.xtd), each texture's PC version (the
first --pc folder that has it, by dictionary and texture name, else by a name
only one PC texture has) is written as <out>/<xx>/<hash>.dds when it's bigger
than the 360's and in the same format family. <hash> is what the PS5's
renderer computes for the 360 texture's base level as the game loads it
(hd_textures.cpp): XXH3-64 of the base level's bytes, its data extent as the
texture cache measures it, seeded with the format, size and tiling.
"""
import argparse
import os
import struct
import sys
import time

import xxhash

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dds  # noqa: E402
import mp3_textures as mt  # noqa: E402
import rpf4  # noqa: E402

# Xenos formats -> the PC FourCC of the same blocks
FAMILIES = {18: b"DXT1", 19: b"DXT3", 20: b"DXT5", 49: b"ATI2"}
BYTES_PER_BLOCK = {18: 8, 19: 16, 20: 16, 49: 16}


def tiled_offset_2d(x, y, pitch, bpb_log2):
    pitch = (pitch + 31) & ~31
    macro = ((x >> 5) + (y >> 5) * (pitch >> 5)) << (bpb_log2 + 7)
    micro = ((x & 7) + ((y & 0xE) << 2)) << bpb_log2
    offset = macro + ((micro & ~0xF) << 1) + (micro & 0xF) + ((y & 1) << 4)
    return (((offset & ~0x1FF) << 3) + ((y & 16) << 7) + ((offset & 0x1C0) << 2) +
            (((((y & 8) >> 2) + (x >> 3)) & 3) << 6) + (offset & 0x3F))


def base_extent(fmt, width, height, pitch_texels, tiled):
    """The base level's data extent (texture_util::GetGuestTextureLayout, 2D, unpacked)."""
    bpb = BYTES_PER_BLOCK[fmt]
    xb, yb = (width + 3) // 4, (height + 3) // 4
    pitch_blocks = (((pitch_texels + 3) // 4) + 31) & ~31
    if tiled:
        log2 = bpb.bit_length() - 1
        bound = tiled_offset_2d((xb - 1) & ~31, (yb - 1) & ~31, pitch_blocks, log2)
        return bound + {0: 0xA00, 1: 0xC00}.get(log2, 0x400 << log2)
    row = pitch_blocks * bpb
    return row * (yb - 1) + bpb * xb


def x360_dictionary(virtual, physical):
    """(name, format, width, height, tiled, hash) of each texture."""
    tex_ptr, count = struct.unpack_from(">IH", virtual, 0x18)
    p = lambda x: x & 0x0FFFFFFF  # noqa: E731
    out = []
    for i in range(count):
        t = p(struct.unpack_from(">I", virtual, p(tex_ptr) + 4 * i)[0])
        name_ptr, d3d = struct.unpack_from(">II", virtual, t + 0x18)
        name = mt.texture_name(mt._cstr(virtual, p(name_ptr)))
        dw0, dw1, dw2 = struct.unpack_from(">III", virtual, p(d3d) + 0x1C)
        fmt = dw1 & 0x3F
        tiled = dw0 >> 31
        pitch = ((dw0 >> 22) & 0x1FF) * 32
        base = p((dw1 >> 12) << 12)
        width, height = (dw2 & 0x1FFF) + 1, ((dw2 >> 13) & 0x1FFF) + 1
        h = None
        if fmt in FAMILIES and width >= 32 and height >= 32:
            size = base_extent(fmt, width, height, pitch, tiled)
            if base + size <= len(physical):
                seed = fmt | width << 8 | height << 24 | tiled << 40
                h = xxhash.xxh3_64_intdigest(physical[base:base + size], seed)
        out.append((name, fmt, width, height, tiled, h))
    return out


def load_pc(folders, key):
    """{(dictionary, name): (folder index, entry, texture index)} and {name: [...]}"""
    by_dict, by_name = {}, {}
    for fi, folder in enumerate(folders):
        t0 = time.time()
        n = 0
        for stem, label, e in mt.dictionaries(folder, key):
            try:
                v, ph = e.data()
                textures = mt.pc_dictionary(v, ph)
            except Exception as ex:
                print(f"  PC {label}: {ex}")
                continue
            for ti, t in enumerate(textures):
                ref = (fi, label, e, ti, t.width, t.height, t.fourcc)
                by_dict.setdefault((stem, t.name), ref)
                by_name.setdefault(t.name, []).append(ref)
                n += 1
        print(f"PC {folder}: {n} textures ({time.time() - t0:.0f} s)", flush=True)
    return by_dict, by_name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x360", action="append", required=True)
    ap.add_argument("--pc", action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keys", default=".")
    args = ap.parse_args()
    key_pc = bytes.fromhex(open(os.path.join(args.keys, "rpf-key-pc.txt")).read().strip())
    key_360 = bytes.fromhex(open(os.path.join(args.keys, "rpf-key-360.txt")).read().strip())

    by_dict, by_name = load_pc(args.pc, key_pc)

    # what to write: hash -> PC texture (the 360 side first, so each PC dictionary is read once)
    wanted = {}
    stats = dict(x360=0, matched=0, not_bigger=0, other_format=0, no_pc=0, unhashed=0)
    for folder in args.x360:
        t0 = time.time()
        for stem, label, e in mt.dictionaries(folder, key_360):
            try:
                v, ph = e.data()
                textures = x360_dictionary(v, ph)
            except Exception as ex:
                print(f"  360 {label}: {ex}")
                continue
            for name, fmt, w, h, tiled, hsh in textures:
                stats["x360"] += 1
                if hsh is None:
                    stats["unhashed"] += 1
                    continue
                ref = by_dict.get((stem, name))
                if ref is None and len(by_name.get(name, [])) == 1:
                    ref = by_name[name][0]
                if ref is None:
                    stats["no_pc"] += 1
                    continue
                if ref[6] != FAMILIES[fmt]:
                    stats["other_format"] += 1
                    continue
                if ref[4] <= w or ref[5] <= h or ref[4] * h != ref[5] * w:
                    stats["not_bigger"] += 1
                    continue
                stats["matched"] += 1
                wanted.setdefault(hsh, ref)
        print(f"360 {folder}: {stats} ({time.time() - t0:.0f} s)", flush=True)

    # write them, a PC dictionary at a time
    by_entry = {}
    for hsh, ref in wanted.items():
        by_entry.setdefault(id(ref[2]), (ref[2], []))[1].append((hsh, ref[3]))
    written = 0
    total = 0
    for e, items in by_entry.values():
        v, ph = e.data()
        textures = mt.pc_dictionary(v, ph)
        for hsh, ti in items:
            t = textures[ti]
            name = f"{hsh:016x}"
            os.makedirs(os.path.join(args.out, name[:2]), exist_ok=True)
            data = bytes(t.data())
            dds.write(os.path.join(args.out, name[:2], name + ".dds"), t.width, t.height, t.mips, t.fourcc, data)
            written += 1
            total += len(data)
    print(f"wrote {written} textures ({total >> 20} MB) to {args.out}")


if __name__ == "__main__":
    main()
