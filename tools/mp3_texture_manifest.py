#!/usr/bin/env python3
"""The HD texture pack as a list, for the PS5 app to build it from the user's own PC files.

    mp3_texture_manifest.py --x360 DVD1 --x360 DVD2 --pc <Nexus texture pack> [--pc <PC game>]
                            --out mp3-textures.txt [--keys .] [--check PPSA95454/textures]

The same matching as mp3_texture_pack.py, but instead of DDS files it writes
where each one's pixels are in the PC files: only the archives' tables of
contents are encrypted, so the app reads them straight from those offsets with
no key. The list holds no game data (paths, sizes, offsets, hashes):

    S <source> <folder> <size> <path>                  a PC file (folder 0: the first --pc, ...)
    R <resource> <source> <step>;<step>;...            how to reach a dictionary resource:
                                                       steps "<offset>:<size>:<z>" into an archive in
                                                       an archive (z 1: deflated), the last the resource
    T <resource> <hash> <w> <h> <mips> <fourcc> <offset> <length> <xxh3>
                                                       a texture: <hash>.dds from the resource's
                                                       physical segment at <offset>, <length> bytes
                                                       (their XXH3-64 <xxh3>)

All numbers are hex except w, h and mips. With --check, every texture's pixels
are compared with the built pack's DDS.
"""
import argparse
import os
import struct
import sys
import time

import xxhash

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mp3_texture_pack as pack  # noqa: E402
import mp3_textures as mt  # noqa: E402
import rpf4  # noqa: E402


def dictionaries_with_chains(root, key):
    """(stem, (file path, chain), entry): the chain reaches the entry's archive from the file."""
    stack = []
    for rel, a in mt.archives(root, key):
        stack.append((rel, [], a))
    while stack:
        rel, chain, a = stack.pop()
        for path, e in a.walk():
            low = path.lower()
            if low.endswith(".rpf"):
                compressed = 1 if (e.flags & 0x40000000) else 0
                try:
                    inner = rpf4.Archive(key, data=e.data(), label=path)
                except Exception:
                    continue
                stack.append((rel, chain + [(e.offset, e.size, compressed)], inner))
            elif low.endswith(mt.TEXTURE_EXTS):
                yield os.path.splitext(os.path.basename(low))[0], (rel, chain), e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x360", action="append", required=True)
    ap.add_argument("--pc", action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keys", default=".")
    ap.add_argument("--check")
    args = ap.parse_args()
    key_pc = bytes.fromhex(open(os.path.join(args.keys, "rpf-key-pc.txt")).read().strip())
    key_360 = bytes.fromhex(open(os.path.join(args.keys, "rpf-key-360.txt")).read().strip())

    # the PC side, with where each dictionary is
    by_dict, by_name = {}, {}
    where = {}  # id(entry) -> (folder, rel, chain, entry)
    for fi, folder in enumerate(args.pc):
        t0 = time.time()
        n = 0
        for stem, (rel, chain), e in dictionaries_with_chains(folder, key_pc):
            try:
                v, ph = e.data()
                textures = mt.pc_dictionary(v, ph)
            except Exception as ex:
                print(f"  PC {rel}: {ex}")
                continue
            where[id(e)] = (fi, rel, chain, e)
            for ti, t in enumerate(textures):
                ref = (fi, rel, e, ti, t.width, t.height, t.fourcc)
                by_dict.setdefault((stem, t.name), ref)
                by_name.setdefault(t.name, []).append(ref)
                n += 1
        print(f"PC {folder}: {n} textures ({time.time() - t0:.0f} s)", flush=True)

    # the 360 side: which PC texture each hash gets (as mp3_texture_pack.py)
    wanted = {}
    for folder in args.x360:
        t0 = time.time()
        for stem, label, e in mt.dictionaries(folder, key_360):
            try:
                v, ph = e.data()
                textures = pack.x360_dictionary(v, ph)
            except Exception as ex:
                print(f"  360 {label}: {ex}")
                continue
            for name, fmt, w, h, tiled, hsh in textures:
                if hsh is None:
                    continue
                ref = by_dict.get((stem, name))
                if ref is None and len(by_name.get(name, [])) == 1:
                    ref = by_name[name][0]
                if ref is None or ref[6] != pack.FAMILIES[fmt]:
                    continue
                if ref[4] <= w or ref[5] <= h or ref[4] * h != ref[5] * w:
                    continue
                wanted.setdefault(hsh, ref)
        print(f"360 {folder}: {len(wanted)} so far ({time.time() - t0:.0f} s)", flush=True)

    # the list, a resource at a time
    by_entry = {}
    for hsh, ref in wanted.items():
        by_entry.setdefault(id(ref[2]), []).append((hsh, ref[3]))
    sources, lines_s, lines_rt = {}, [], []
    per_folder = [0] * len(args.pc)
    checked = mismatched = 0
    for ri, (eid, items) in enumerate(by_entry.items()):
        fi, rel, chain, e = where[eid]
        skey = (fi, rel)
        if skey not in sources:
            sources[skey] = len(sources)
            size = os.path.getsize(os.path.join(args.pc[fi], rel))
            lines_s.append(f"S {sources[skey]:x} {fi} {size:x} {rel}")
        steps = [f"{o:x}:{s:x}:{z}" for o, s, z in chain] + [f"{e.offset:x}:{e.size:x}:r"]
        lines_rt.append(f"R {ri:x} {sources[skey]:x} {';'.join(steps)}")
        v, ph = e.data()
        textures = mt.pc_dictionary(v, ph)
        for hsh, ti in items:
            t = textures[ti]
            data = bytes(t.data())
            offset = len(ph) - len(t.pixels)  # (the pixels' offset in the physical segment)
            lines_rt.append(f"T {ri:x} {hsh:016x} {t.width} {t.height} {t.mips} {t.fourcc.decode()} "
                            f"{offset:x} {len(data):x} {xxhash.xxh3_64_intdigest(data):016x}")
            per_folder[fi] += 1
            if args.check:
                name = f"{hsh:016x}"
                path = os.path.join(args.check, name[:2], name + ".dds")
                if os.path.exists(path):
                    checked += 1
                    with open(path, "rb") as f:
                        if f.read()[128:] != data:
                            mismatched += 1
    with open(args.out, "w", newline="\n") as f:
        f.write("# Max Payne 3 RePrescribed HD textures (tools/mp3_texture_manifest.py)\n")
        for i, folder in enumerate(args.pc):
            f.write(f"# folder {i}: {os.path.basename(os.path.normpath(folder))}\n")
        f.write("\n".join(lines_s + lines_rt) + "\n")
    print(f"{len(wanted)} textures from {len(sources)} files, by folder {per_folder}; wrote {args.out}")
    if args.check:
        print(f"checked against {args.check}: {checked} found, {mismatched} different")


if __name__ == "__main__":
    main()
