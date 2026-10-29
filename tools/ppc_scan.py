"""For each call site of a function in the decrypted Xbox 360 image, find the
calling function (back to its mflr prologue, on to its blr) and report what
it uses: timebase reads (mftb), calls, float compares, divides. A frame
limiter reads the clock and sleeps what's left of the frame.

  python3 ppc_scan.py image.bin <callee hex> [more callees...]
"""
import struct
import sys
from collections import Counter

BASE = 0x82000000


def main():
    data = open(sys.argv[1], "rb").read()
    targets = {int(t, 16) for t in sys.argv[2:]}

    def word(a):
        o = a - BASE
        return struct.unpack(">I", data[o:o + 4])[0] if 0 <= o < len(data) - 3 else 0

    def branch_target(a, w):
        li = w & 0x03FFFFFC
        if li & 0x02000000:
            li -= 0x04000000
        return a + li

    sites = []
    for o in range(0, len(data) - 3, 4):
        w = struct.unpack(">I", data[o:o + 4])[0]
        if (w >> 26) == 18 and (w & 2) == 0 and branch_target(BASE + o, w) in targets:
            sites.append(BASE + o)
    seen = set()
    for s in sites:
        start = s
        while start > s - 0x4000 and word(start) != 0x7D8802A6:  # mflr r12
            start -= 4
        if start in seen:
            continue
        seen.add(start)
        end = s
        while end < s + 0x4000 and word(end) != 0x4E800020:  # blr
            end += 4
        mftb = calls = fcmp = div = 0
        callees = Counter()
        for a in range(start, end + 4, 4):
            w = word(a)
            if (w >> 26) == 31 and ((w >> 1) & 0x3FF) in (371, 339) and ((w >> 11) & 0x3FF) in (0x188, 0x1A8, 268 << 0, 0x10C):
                mftb += 1  # mftb / mfspr tb
            if (w >> 26) == 31 and ((w >> 1) & 0x3FF) == 371:
                mftb += 1
            if (w >> 26) == 18 and (w & 3) == 1:
                calls += 1
                callees[branch_target(a, w)] += 1
            if (w >> 26) == 63 and ((w >> 1) & 0x3FF) in (0, 32):
                fcmp += 1
            if (w >> 26) in (59, 63) and ((w >> 1) & 0x1F) == 18:
                div += 1
        top = " ".join(f"{c:08X}" for c, _ in callees.most_common(8))
        print(f"{start:08X}-{end:08X} ({(end - start) // 4 + 1} ins) site {s:08X}: mftb {mftb}, calls {calls}, "
              f"fcmp {fcmp}, fdiv {div}; calls {top}")


if __name__ == "__main__":
    main()
