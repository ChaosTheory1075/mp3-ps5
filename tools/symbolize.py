#!/usr/bin/env python3
"""Name the functions in a PS5 crash line.

    symbolize.py <symbols.txt.gz from ps5-build-logs> <log file>

The crash line's addresses are anchor-relative (rip - &rex::ps5diag::NowMs +
0x10000000); the symbols are llvm-nm -n of eboot.elf.
"""
import bisect
import gzip
import re
import sys


def main():
    syms = []
    with gzip.open(sys.argv[1], "rt", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split(" ", 2)
            if len(parts) == 3:
                syms.append((int(parts[0], 16), parts[2]))
    syms.sort()
    anchor = next(a for a, n in syms if n.endswith("ps5diag::NowMs()"))
    addrs = [a for a, _ in syms]

    def name(rel):
        a = rel - 0x10000000 + anchor
        i = bisect.bisect_right(addrs, a) - 1
        return f"{syms[i][1]} +{a - syms[i][0]:#x}" if i >= 0 else "?"

    for line in open(sys.argv[2], errors="replace"):
        m = re.search(r"\[ps5 crash\].*anchor-relative ([0-9A-F]+)\) \| stack: (.*)", line)
        if not m:
            continue
        print("fault in", name(int(m.group(1), 16)))
        for w in m.group(2).split():
            print("   ", w, name(int(w, 16)))


if __name__ == "__main__":
    main()
