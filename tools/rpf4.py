"""Max Payne 3's RPF4 archives (PC and Xbox 360) and the resources in them.

The table of contents at 0x800 (its size and entry count in the header,
little-endian on both platforms) is AES-256-ECB encrypted, 16 passes, with the
platform's key (tools/find_rpf_key.py finds it). Entries are 16 bytes:

    name offset, size on disk, offset (8-byte units; a resource's low byte is
    its version), flags (a directory: 0x80000000 | first entry in the offset
    field, the count here; a file: bit 30 compressed; a resource: its RSC
    flags, which give the virtual and physical sizes).

A resource is stored as "RSC" 0x05, its version, its flags, then its data
compressed (zlib on the PC; the Xbox 360's is checked in Resource.data).
"""
import struct
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def decrypt(key, data):
    d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    data = data[: len(data) & ~15]
    for _ in range(16):
        data = d.update(data)
    return data


def rsc_sizes(flags):
    virtual = (flags & 0x7FF) << (((flags >> 11) & 0xF) + 8)
    physical = ((flags >> 15) & 0x7FF) << (((flags >> 26) & 0xF) + 8)
    return virtual, physical


class Entry:
    def __init__(self, archive, name, fields):
        self.archive = archive
        self.name = name
        self.name_offset, self.size, self.offset_field, self.flags = fields
        self.is_dir = bool(self.offset_field & 0x80000000)
        self.is_resource = not self.is_dir and (self.offset_field & 0xFF) != 0
        self.offset = (self.offset_field & 0x7FFFFF00) * 8 if not self.is_dir else 0

    def raw(self):
        return self.archive.read(self.offset, self.size)

    def data(self):
        """The file's bytes (decompressed); a resource's: (virtual, physical)."""
        raw = self.raw()
        if self.is_resource:
            return Resource(raw, self.flags).segments()
        if self.flags & 0x40000000:
            return zlib.decompressobj(-15).decompress(raw)
        return raw


class Resource:
    def __init__(self, raw, flags):
        self.raw = raw
        self.flags = flags

    def segments(self):
        raw = self.raw
        if raw[:3] != b"RSC":
            raise ValueError("not a resource")
        flags = struct.unpack_from("<I", raw, 8)[0]
        if flags & 0x7FF == 0 and struct.unpack_from(">I", raw, 8)[0]:
            flags = struct.unpack_from(">I", raw, 8)[0]
        virtual, physical = rsc_sizes(flags)
        body = raw[12:]
        if body[:1] == b"\x78":
            out = zlib.decompress(body)
        elif body[:4] == b"\x0f\xf5\x12\xf1":
            from xcompress import decompress  # (Xbox 360 LZX; tools/xcompress.py)
            out = decompress(body, virtual + physical)
        else:
            out = zlib.decompressobj(-15).decompress(body)
        return out[:virtual], out[virtual:virtual + physical]


class Archive:
    """An RPF4 in a file, or in another archive's bytes."""

    def __init__(self, key, path=None, data=None, label=None):
        self.path = path
        self.buf = data
        self.label = label or str(path)
        head = self.read(0, 0x14)
        if head[:4] != b"RPF4":
            raise ValueError(f"{self.label}: not an RPF4")
        toc_size, count = struct.unpack_from("<II", head, 4)
        toc = decrypt(key, self.read(0x800, toc_size))
        names = toc[count * 16:]
        self.entries = []
        for i in range(count):
            fields = struct.unpack_from("<4I", toc, i * 16)
            end = names.index(b"\0", fields[0])
            self.entries.append(Entry(self, names[fields[0]:end].decode("latin-1"), fields))

    def read(self, offset, size):
        if self.buf is not None:
            return self.buf[offset:offset + size]
        with open(self.path, "rb") as f:
            f.seek(offset)
            return f.read(size)

    def walk(self, index=0, prefix=""):
        """(path, entry) of every file."""
        e = self.entries[index]
        first = e.offset_field & 0x7FFFFFFF
        for i in range(first, first + e.flags):
            child = self.entries[i]
            path = f"{prefix}/{child.name}" if prefix else child.name
            if child.is_dir:
                yield from self.walk(i, path)
            else:
                yield path, child
