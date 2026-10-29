"""Disassemble Max Payne 3's decrypted Xbox 360 image (the REX_DUMP_IMAGE dump,
base 0x82000000) around a guest address, and list who calls a function.

  python3 ppc_dis.py image.bin dis <address hex> [count]
  python3 ppc_dis.py image.bin callers <function address hex>
  python3 ppc_dis.py image.bin func <function address hex>   (to its blr)

Needs capstone (pip install capstone). No game code is kept in the repo: the
image is read from wherever the local build put it.
"""
import struct
import sys

from capstone import CS_ARCH_PPC, CS_MODE_64, CS_MODE_BIG_ENDIAN, Cs

BASE = 0x82000000


def main():
    data = open(sys.argv[1], "rb").read()
    cmd = sys.argv[2]
    md = Cs(CS_ARCH_PPC, CS_MODE_64 | CS_MODE_BIG_ENDIAN)

    def word(a):
        o = a - BASE
        return struct.unpack(">I", data[o:o + 4])[0]

    def dis(a, n):
        for i in range(n):
            ad = a + 4 * i
            o = ad - BASE
            ins = list(md.disasm(data[o:o + 4], ad))
            txt = f"{ins[0].mnemonic} {ins[0].op_str}" if ins else f".long 0x{word(ad):08X}"
            print(f"  {ad:08X}  {word(ad):08X}  {txt}")
            if cmd == "func" and txt.startswith("blr"):
                return

    if cmd in ("dis", "func"):
        dis(int(sys.argv[3], 16), int(sys.argv[4]) if len(sys.argv) > 4 else (4000 if cmd == "func" else 40))
    elif cmd == "callers":
        target = int(sys.argv[3], 16)
        for o in range(0, len(data) - 3, 4):
            w = struct.unpack(">I", data[o:o + 4])[0]
            if (w >> 26) == 18 and (w & 2) == 0:  # bl / b
                li = w & 0x03FFFFFC
                if li & 0x02000000:
                    li -= 0x04000000
                if BASE + o + li == target:
                    print(f"  {'bl' if w & 1 else 'b '} from {BASE + o:08X}")


if __name__ == "__main__":
    main()
