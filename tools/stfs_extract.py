#!/usr/bin/env python3
"""Extract files from an Xbox 360 STFS package (LIVE / PIRS / CON).

    stfs_extract.py <package> <out dir>          extract every file
    stfs_extract.py <package> --list             list files and the header

Used for Max Payne 3's title update (default.xexp) and its DLC packages.
Handles read-only (LIVE/PIRS) packages, consecutive and chained files.
"""
import os
import struct
import sys

BLOCK = 0x1000


class Stfs:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.size = os.path.getsize(path)
        hdr = self.f.read(0x1000 * 0x10)
        self.magic = hdr[0:4]
        if self.magic not in (b"LIVE", b"PIRS", b"CON "):
            raise ValueError("not an STFS package")
        self.header_size = struct.unpack_from(">I", hdr, 0x340)[0]
        self.content_type = struct.unpack_from(">I", hdr, 0x344)[0]
        self.media_id = struct.unpack_from(">I", hdr, 0x354)[0]
        self.version = struct.unpack_from(">I", hdr, 0x358)[0]
        self.base_version = struct.unpack_from(">I", hdr, 0x35C)[0]
        self.title_id = struct.unpack_from(">I", hdr, 0x360)[0]
        self.display_name = hdr[0x411:0x411 + 0x80].decode("utf-16-be", "replace").split("\0")[0]
        vd = 0x379
        self.block_sep = hdr[vd + 2]
        self.ft_blocks = struct.unpack_from("<H", hdr, vd + 3)[0]
        self.ft_start = int.from_bytes(hdr[vd + 5:vd + 8], "little")
        self.total_blocks = struct.unpack_from(">I", hdr, vd + 0x1C)[0]
        self.first = (self.header_size + 0xFFF) & ~0xFFF
        # Read-only packages keep one hash table per level; read-write keep two.
        self.shift = 0 if (((self.header_size + 0xFFF) & 0xF000) >> 12) == 0xB else 1
        if self.block_sep & 1:
            self.shift = 0

    def backing(self, block):
        s = self.shift
        r = (((block + 0xAA) // 0xAA) << s) + block
        if block < 0xAA:
            return r
        if block < 0x70E4:
            return r + (((block + 0x70E4) // 0x70E4) << s)
        return (1 << s) + r + (((block + 0x70E4) // 0x70E4) << s)

    def hash_block(self, block):
        """Backing block of the level-0 hash table that covers `block`."""
        s = self.shift
        if block < 0xAA:
            return 0
        r = (block // 0xAA) * (0xAB << s)
        if block >= 0x70E4:
            r += ((block // 0x70E4) + 1) << s
        else:
            r += 1 << s
        return r

    def read_block(self, block):
        self.f.seek(self.first + self.backing(block) * BLOCK)
        return self.f.read(BLOCK)

    def next_block(self, block):
        self.f.seek(self.first + self.hash_block(block) * BLOCK + (block % 0xAA) * 0x18 + 0x15)
        return int.from_bytes(self.f.read(3), "big")

    def chain(self, start, count, consecutive):
        b = start
        for _ in range(count):
            yield b
            b = b + 1 if consecutive else self.next_block(b)

    def entries(self):
        raw = b"".join(self.read_block(b) for b in self.chain(self.ft_start, self.ft_blocks, False))
        out = []
        for i in range(0, len(raw), 0x40):
            e = raw[i:i + 0x40]
            flags = e[0x28]
            nlen = flags & 0x3F
            if nlen == 0:
                break
            out.append({
                "name": e[:nlen].decode("ascii", "replace"),
                "dir": bool(flags & 0x80),
                "consecutive": bool(flags & 0x40),
                "blocks": int.from_bytes(e[0x29:0x2C], "little"),
                "start": int.from_bytes(e[0x2F:0x32], "little"),
                "parent": struct.unpack_from(">h", e, 0x32)[0],
                "size": struct.unpack_from(">I", e, 0x34)[0],
            })
        return out

    def path_of(self, entries, i):
        parts = []
        while i != -1 and i < len(entries):
            parts.append(entries[i]["name"])
            i = entries[i]["parent"]
        return "/".join(reversed(parts))

    def extract(self, out_dir):
        ents = self.entries()
        for i, e in enumerate(ents):
            p = os.path.join(out_dir, self.path_of(ents, i))
            if e["dir"]:
                os.makedirs(p, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            left = e["size"]
            with open(p, "wb") as o:
                for b in self.chain(e["start"], e["blocks"], e["consecutive"]):
                    data = self.read_block(b)
                    o.write(data[:min(left, BLOCK)])
                    left -= BLOCK
                    if left <= 0:
                        break
            print(f"{e['size']:>12}  {self.path_of(ents, i)}")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    s = Stfs(sys.argv[1])
    print(f"{s.magic.decode()} title={s.title_id:08X} type={s.content_type:08X} "
          f"version={s.version:08X} base={s.base_version:08X} name={s.display_name!r}")
    if sys.argv[2] == "--list":
        for i, e in enumerate(s.entries()):
            print(f"{e['size']:>12} {'d' if e['dir'] else '-'}{'c' if e['consecutive'] else '-'}  {s.path_of(s.entries(), i)}")
    else:
        s.extract(sys.argv[2])


if __name__ == "__main__":
    main()
