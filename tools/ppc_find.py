"""Find 32-bit big-endian words (or float / double values) in the decrypted
Xbox 360 image (base 0x82000000), and the code that builds a constant with
lis/ori or lis/addi.

  python3 ppc_find.py image.bin word <hex>...
  python3 ppc_find.py image.bin float <value>...
  python3 ppc_find.py image.bin double <value>...
  python3 ppc_find.py image.bin imm <hex 32-bit>       (lis + ori/addi pairs)
"""
import struct
import sys

BASE = 0x82000000


def main():
    data = open(sys.argv[1], "rb").read()
    kind = sys.argv[2]
    for v in sys.argv[3:]:
        if kind == "word":
            pat = struct.pack(">I", int(v, 16))
        elif kind == "float":
            pat = struct.pack(">f", float(v))
        elif kind == "double":
            pat = struct.pack(">d", float(v))
        elif kind == "imm":
            x = int(v, 16)
            hi, lo = x >> 16, x & 0xFFFF
            his = (hi + (1 if lo & 0x8000 else 0)) & 0xFFFF  # (addi sign-extends)
            hits = []
            for o in range(0, len(data) - 3, 4):
                w = struct.unpack(">I", data[o:o + 4])[0]
                if w >> 26 == 15 and (w >> 16) & 31 == 0 and (w & 0xFFFF) in (hi, his):  # lis rD
                    rd = (w >> 21) & 31
                    for k in range(1, 6):
                        w2 = struct.unpack(">I", data[o + 4 * k:o + 4 * k + 4])[0]
                        op = w2 >> 26
                        if op in (24, 14) and (w2 >> 16) & 31 == rd and (w2 & 0xFFFF) == lo:
                            if (op == 24 and (w & 0xFFFF) == hi) or (op == 14 and (w & 0xFFFF) == his):
                                hits.append(BASE + o)
            print(f"imm {v}: {len(hits)} at " + " ".join(f"{h:08X}" for h in hits[:60]))
            continue
        else:
            sys.exit("kind: word, float, double or imm")
        hits = []
        i = data.find(pat)
        while i >= 0 and len(hits) < 60:
            if i % 4 == 0:
                hits.append(BASE + i)
            i = data.find(pat, i + 1)
        print(f"{kind} {v} ({pat.hex()}): {len(hits)} at " + " ".join(f"{h:08X}" for h in hits))


if __name__ == "__main__":
    main()
