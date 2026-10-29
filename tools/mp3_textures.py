"""Max Payne 3's texture dictionaries (.wtd on the PC, .xtd on the Xbox 360).

A dictionary resource's virtual segment (pointers 0x5XXXXXXX) holds the
structures, its physical segment (0x6XXXXXXX) the pixels. The PC layout:

    dictionary +0x18  textures (pointer, u16 count)
    texture    +0x18  name ("pack:/<name>.dds"), +0x20 u16 width, height,
               depth, row pitch, +0x28 FourCC, +0x2D mips, +0x50 pixels
"""
import os
import struct

import rpf4

TEXTURE_EXTS = (".wtd", ".xtd")


def _ptr(p):
    return p & 0x0FFFFFFF


def _cstr(buf, off):
    return buf[off:buf.index(b"\0", off)].decode("latin-1")


def texture_name(name):
    name = name.split(":/")[-1].lower()
    return name[:-4] if name.endswith(".dds") else name


class PcTexture:
    def __init__(self, name, width, height, fourcc, mips, pixels):
        self.name, self.width, self.height = name, width, height
        self.fourcc, self.mips, self.pixels = fourcc, mips, pixels

    def level_sizes(self):
        bpb = {b"DXT1": 8, b"DXT3": 16, b"DXT5": 16, b"ATI2": 16}.get(self.fourcc)
        for m in range(self.mips):
            w, h = max(self.width >> m, 1), max(self.height >> m, 1)
            yield (max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bpb) if bpb else w * h * 4

    def data(self):
        return self.pixels[: sum(self.level_sizes())]


def pc_dictionary(virtual, physical):
    tex_ptr, count = struct.unpack_from("<IH", virtual, 0x18)
    out = []
    for i in range(count):
        t = _ptr(struct.unpack_from("<I", virtual, _ptr(tex_ptr) + 4 * i)[0])
        name = texture_name(_cstr(virtual, _ptr(struct.unpack_from("<I", virtual, t + 0x18)[0])))
        w, h = struct.unpack_from("<HH", virtual, t + 0x20)
        fourcc = bytes(virtual[t + 0x28:t + 0x2C])
        mips = max(virtual[t + 0x2D], 1)
        pixels = _ptr(struct.unpack_from("<I", virtual, t + 0x50)[0])
        out.append(PcTexture(name, w, h, fourcc, mips, memoryview(physical)[pixels:]))
    return out


def archives(root, key, exts=(".rpf",)):
    """Every RPF4 under root (files on disk), with the archives inside them."""
    for dirpath, _, files in os.walk(root):
        for f in sorted(files):
            if not f.lower().endswith(".rpf"):
                continue
            path = os.path.join(dirpath, f)
            try:
                a = rpf4.Archive(key, path)
            except Exception as e:  # (an archive of another kind)
                print(f"  skip {path}: {e}")
                continue
            yield os.path.relpath(path, root).replace("\\", "/"), a


def dictionaries(root, key):
    """(dictionary key, entry) of every texture dictionary: the key is the
    dictionary's file name without its extension, lower case."""
    stack = list(archives(root, key))
    while stack:
        label, a = stack.pop()
        for path, e in a.walk():
            low = path.lower()
            if low.endswith(".rpf"):
                try:
                    stack.append((f"{label}::{path}", rpf4.Archive(key, data=e.data(), label=path)))
                except Exception:
                    pass
            elif low.endswith(TEXTURE_EXTS):
                yield os.path.splitext(os.path.basename(low))[0], f"{label}::{path}", e
