#!/usr/bin/env python3
"""Mark the split-off pieces (SEH funclets, tails) share_registers.

    mark_share_registers.py <read_before_write.py output> <mp3_config.toml> <image.bin>

With non_volatile_as_local (and cr/ctr/xer) every recompiled function keeps
those registers in C++ locals, so a piece the analyzer split off its owner
reads zeros where the owner's registers should be (Max Payne 3 crashed in its
first SEH __finally, sub_821F4FD0, reading 0x580(r21)). gta4-ps5's
read_before_write.py lists the functions that read one before writing it.

Only a function that reads a GPR (or cr/ctr/xer) is marked, and not one with
a normal prologue (mflr r12; bl __savegprlr_N): a shared function's save and
restore helpers are elided, so it would hand the caller back its own values
(sub_8307A488, listed only for v127, set the D3D code's r30 to 1 and the
caller then stored to 0x5). Vector-only listings are VMX save slots reading
a not-yet-written local, harmless.
"""
import re
import struct
import sys

IMAGE_BASE = 0x82000000


def normal_prologue(image, address):
    off = address - IMAGE_BASE
    if off < 0 or off + 8 > len(image):
        return False
    w0, w1 = struct.unpack_from(">II", image, off)
    return w0 == 0x7D8802A6 and (w1 >> 26) == 18 and (w1 & 3) == 1


def main():
    image = open(sys.argv[3], "rb").read()
    listed, skipped = [], []
    for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
        m = re.match(r"^sub_([0-9A-F]{8})\s+([^|]*)\|", line)
        if not m:
            continue
        address = int(m.group(1), 16)
        regs = m.group(2).split()
        if all(r.startswith(("v", "f")) for r in regs) or normal_prologue(image, address):
            skipped.append(address)
            continue
        listed.append(address)
    path = sys.argv[2]
    cfg = open(path, encoding="utf-8").read()
    added = []
    for address in sorted(set(listed)):
        pattern = re.compile(r'^"0x%08X"\s*=\s*\{([^}\n]*)\}' % address, re.M | re.I)
        m = pattern.search(cfg)
        if m:
            inner = m.group(1).strip()
            if "share_registers" not in inner:
                inner = (inner + ", " if inner else "") + "share_registers = true"
                cfg = cfg[:m.start()] + '"0x%08X" = { %s }' % (address, inner) + cfg[m.end():]
        else:
            added.append('"0x%08X" = { share_registers = true }' % address)
    if added:
        cfg = cfg.rstrip("\n") + "\n\n# Split-off pieces (ci/mark_share_registers.py)\n" + "\n".join(added) + "\n"
    open(path, "w", encoding="utf-8").write(cfg)
    print(f"share_registers: {len(set(listed))} functions ({len(added)} new entries); "
          f"{len(set(skipped))} left alone (vector-only or normal prologue)")
    for address in sorted(set(listed)):
        print("  sub_%08X" % address)


if __name__ == "__main__":
    main()
