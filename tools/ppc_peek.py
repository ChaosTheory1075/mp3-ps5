"""Print words of the decrypted Xbox 360 image (base 0x82000000) as hex,
int and float.  python3 ppc_peek.py image.bin <address hex>..."""
import struct
import sys

BASE = 0x82000000
data = open(sys.argv[1], "rb").read()
for a in sys.argv[2:]:
    x = int(a, 16)
    o = x - BASE
    if not 0 <= o < len(data) - 3:
        print(f"{x:08X}: outside the image (bss)")
        continue
    w = data[o:o + 4]
    print(f"{x:08X}: {w.hex()} int {struct.unpack('>i', w)[0]} float {struct.unpack('>f', w)[0]:.6g}")
