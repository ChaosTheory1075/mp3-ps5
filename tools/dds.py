"""DDS files for the HD texture replacements (legacy FourCC headers: DXT1,
DXT3, DXT5, ATI2; or 32-bit RGBA)."""
import struct

FOURCC_FORMATS = (b"DXT1", b"DXT3", b"DXT5", b"ATI2")


def write(path, width, height, mips, fourcc, data):
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000  # caps, height, width, pixel format, mip count
    caps = 0x1000 | (0x400008 if mips > 1 else 0)
    if fourcc in FOURCC_FORMATS:
        bpb = 8 if fourcc == b"DXT1" else 16
        linear = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * bpb
        pf = struct.pack("<II4sIIIII", 32, 0x4, fourcc, 0, 0, 0, 0, 0)
        flags |= 0x80000
    else:
        linear = width * 4
        pf = struct.pack("<II4sIIIII", 32, 0x41, b"\0\0\0\0", 32, 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)
        flags |= 0x8
    header = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height, width, linear, 0, mips) + bytes(44) + pf
    header += struct.pack("<IIIII", caps, 0, 0, 0, 0)
    assert len(header) == 128
    with open(path, "wb") as f:
        f.write(header)
        f.write(data)
