"""Xbox 360 XMemCompress (LZX) streams, through tools/lzx/lzx.dll (lzx.c).

A Max Payne 3 360 resource's data: 0x0FF512F1, a big-endian u32 compressed
size, then the chunk stream lzx.c decodes.
"""
import ctypes
import os
import struct

_dll = ctypes.CDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lzx", "lzx.dll"))
_dll.lzx_xmem.restype = ctypes.c_int
_dll.lzx_xmem.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                          ctypes.c_void_p]
_dll.lzx_work_size.restype = ctypes.c_uint32
_work = ctypes.create_string_buffer(_dll.lzx_work_size())

WINDOW_BITS = 17


def decompress(data, out_len, window_bits=None):
    if data[:4] != b"\x0f\xf5\x12\xf1":
        raise ValueError("not an XMemCompress stream")
    size = struct.unpack_from(">I", data, 4)[0]
    stream = bytes(data[8:8 + size])
    out = ctypes.create_string_buffer(out_len)
    rc = _dll.lzx_xmem(stream, len(stream), out, out_len, window_bits or WINDOW_BITS, _work)
    if rc:
        raise ValueError(f"LZX error {rc}")
    return out.raw
