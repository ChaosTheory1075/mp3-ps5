#!/usr/bin/env python3
"""Find Max Payne 3's RPF4 archive key in its executable.

    find_rpf_key.py <MaxPayne3.exe or a dumped 360 image> <an .rpf of the same platform> [--step N]

An RPF4's table of contents (at 0x800, its size in the header) is AES-256-ECB
encrypted and padded with zeros, so its last 16 bytes decrypt to zeros with
the right key. Every --step-aligned 32 bytes of the executable is tried, as
one pass and as 16 passes (as GTA IV's archives). Prints the key in hex.
"""
import struct
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    step = 4
    if "--step" in sys.argv:
        step = int(sys.argv[sys.argv.index("--step") + 1])
        args = [a for a in args if a != str(step)]
    exe = open(args[0], "rb").read()
    with open(args[1], "rb") as f:
        head = f.read(0x800)
        toc_size = struct.unpack_from("<I", head, 4)[0]
        f.seek(0x800 + toc_size - 16)
        last = f.read(16)
    zero = bytes(16)
    print(f"{len(exe)} bytes, step {step}, last TOC block {last.hex()}", flush=True)
    for off in range(0, len(exe) - 32, step):
        key = exe[off:off + 32]
        if key.count(0) > 8:  # (a key isn't mostly zeros)
            continue
        dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        block = dec.update(last)
        if block == zero:
            print(f"KEY (1 pass) at {off:#x}: {key.hex()}")
            return 0
        for _ in range(15):
            block = dec.update(block)
        if block == zero:
            print(f"KEY (16 passes) at {off:#x}: {key.hex()}")
            return 0
        if off % 0x200000 < step:
            print(f"  {off:#x}", flush=True)
    print("no key found")
    return 1


if __name__ == "__main__":
    sys.exit(main())
