#!/usr/bin/env python3
"""Follows package/mp3-textures.txt the way the PS5 app does (mp3_textures_install.cpp): no keys,
just the offsets in the user's files. Checks every texture's XXH3 (or a sample).

    mp3_texture_manifest_check.py mp3-textures.txt <folder 0: Nexus pack> [<folder 1: PC game>] [--sample N]
"""
import argparse
import os
import random
import struct
import zlib

import xxhash


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args()
    sources, resources = {}, {}
    for line in open(args.manifest, encoding="utf-8"):
        if line.startswith("S "):
            _, idx, folder, size, rel = line.rstrip("\n").split(" ", 4)
            sources[idx] = (int(folder), int(size, 16), rel)
        elif line.startswith("R "):
            _, idx, src, steps = line.split()
            resources[idx] = (src, [s.split(":") for s in steps.split(";")], [])
        elif line.startswith("T "):
            f = line.split()
            resources[f[1]][2].append(f[2:])
    items = list(resources.items())
    if args.sample:
        random.seed(1)
        items = random.sample(items, min(args.sample, len(items)))
    ok = bad = missing = 0
    for idx, (src, steps, textures) in items:
        folder, size, rel = sources[src]
        if folder >= len(args.folders):
            missing += len(textures)
            continue
        path = os.path.join(args.folders[folder], rel)
        if not os.path.exists(path) or os.path.getsize(path) != size:
            missing += len(textures)
            continue
        with open(path, "rb") as fh:
            base, buf = 0, None
            for off, sz, kind in steps[:-1]:
                off, sz = int(off, 16), int(sz, 16)
                if buf is None and kind == "0":
                    base += off
                    continue
                part = buf[off:off + sz] if buf is not None else (fh.seek(base + off), fh.read(sz))[1]
                buf = zlib.decompressobj(-15).decompress(part) if kind == "1" else part
            off, sz, _ = steps[-1]
            off, sz = int(off, 16), int(sz, 16)
            raw = buf[off:off + sz] if buf is not None else (fh.seek(base + off), fh.read(sz))[1]
        flags = struct.unpack_from("<I", raw, 8)[0]
        if flags & 0x7FF == 0 and struct.unpack_from(">I", raw, 8)[0]:
            flags = struct.unpack_from(">I", raw, 8)[0]
        virt = (flags & 0x7FF) << (((flags >> 11) & 0xF) + 8)
        body = raw[12:]
        out = zlib.decompress(body) if body[:1] == b"\x78" else zlib.decompressobj(-15).decompress(body)
        for hsh, w, h, mips, fourcc, poff, length, xxh in textures:
            at = virt + int(poff, 16)
            data = out[at:at + int(length, 16)]
            if xxhash.xxh3_64_intdigest(data) == int(xxh, 16):
                ok += 1
            else:
                bad += 1
    print(f"{ok} textures match, {bad} don't, {missing} not checked (their files missing)")


if __name__ == "__main__":
    main()
