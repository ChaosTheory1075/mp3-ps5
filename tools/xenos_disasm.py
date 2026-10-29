"""Xenos (Xbox 360 GPU) shader microcode disassembler, for the shaders in
tools/mp3_frame.py captures. Bit layouts as include/rex/graphics/format/ucode.h.

  python xenos_disasm.py capture.bin <shader hash hex> [...]
"""
import struct
import sys

CF = ["nop", "exec", "exece", "cexec", "cexece", "cexecp", "cexecpe", "loop", "endloop",
      "call", "ret", "jmp", "alloc", "cexecpc", "cexecpce", "vsdone"]
VEC = ["add", "mul", "max", "min", "seq", "sgt", "sge", "sne", "frc", "trunc", "floor", "mad",
       "cndeq", "cndge", "cndgt", "dp4", "dp3", "dp2add", "cube", "max4", "setpeq_push",
       "setpne_push", "setpgt_push", "setpge_push", "kill_eq", "kill_gt", "kill_ge", "kill_ne",
       "dst", "maxa"]
VEC_SRCS = {8: 1, 9: 1, 10: 1, 11: 3, 12: 3, 13: 3, 14: 3, 17: 3}
SCA = ["adds", "adds_prev", "muls", "muls_prev", "muls_prev2", "maxs", "mins", "seqs", "sgts",
       "sges", "snes", "frcs", "truncs", "floors", "exp", "logc", "log", "rcpc", "rcpf", "rcp",
       "rsqc", "rsqf", "rsq", "maxas", "maxasf", "subs", "subs_prev", "setp_eq", "setp_ne",
       "setp_gt", "setp_ge", "setp_inv", "setp_pop", "setp_clr", "setp_rstr", "kills_eq",
       "kills_gt", "kills_ge", "kills_ne", "kills_one", "sqrt", "?41", "mulsc", "mulsc", "addsc",
       "addsc", "subsc", "subsc", "sin", "cos", "retain_prev"]
SCA_TWO = {0, 2, 5, 6, 25}  # src3.x op src3.y
FETCH = {0: "vfetch", 1: "tfetch", 16: "getBCF", 17: "getCompLOD", 18: "getGradients",
         19: "getWeights", 24: "setTexLOD", 25: "setGradientsH", 26: "setGradientsV"}
DIM = ["1D", "2D", "3D", "Cube"]
C = "xyzw"


def b(v, lo, n):
    return (v >> lo) & ((1 << n) - 1)


def sb(v, lo, n):
    x = b(v, lo, n)
    return x - (1 << n) if x >> (n - 1) else x


def alu_src(w, i, ps):
    reg = b(w[2], 8 * (3 - i), 8)  # src1_reg at bits 16..23, src2 8..15, src3 0..7
    is_temp = b(w[2], 32 - i, 1)  # src1_sel bit 31, src2 30, src3 29
    swz = b(w[1], 8 * (3 - i), 8)
    neg = b(w[1], 24 + (3 - i), 1)
    sw = "".join(C[((swz >> (2 * k)) + k) & 3] for k in range(4))
    if is_temp:
        s = "r%d" % (reg & 0x3F) + ("[aL]" if reg & 0x40 else "")
        if reg & 0x80:
            s = "|%s|" % s
    else:
        s = "c%d" % reg
        if b(w[0], 7, 1):
            s = "|%s|" % s
    if sw != "xyzw":
        s += "." + sw
    return ("-" if neg else "") + s


def dest(reg, mask, export, ps, rel):
    if export:
        if ps:
            name = {0: "oC0", 1: "oC1", 2: "oC2", 3: "oC3", 61: "oDepth"}.get(reg, "export%d" % reg)
        else:
            name = {62: "oPos", 63: "oPts"}.get(reg, "o%d" % reg)
    else:
        name = "r%d" % reg + ("[aL]" if rel else "")
    return name + "." + "".join(C[k] if mask >> k & 1 else "_" for k in range(4))


def alu(w, ps):
    out = []
    export = b(w[0], 15, 1)
    pred = ""
    if b(w[1], 28, 1):
        pred = "(p%s) " % ("" if b(w[1], 27, 1) else "!")
    vop = b(w[2], 24, 5)
    vmask = b(w[0], 16, 4)
    smask = b(w[0], 20, 4)
    sop = b(w[0], 26, 6)
    if vmask or vop in range(20, 28) or (export and not smask):
        n = VEC_SRCS.get(vop, 2)
        srcs = ", ".join(alu_src(w, i, ps) for i in range(1, n + 1))
        sat = "_sat" if b(w[0], 24, 1) else ""
        out.append("%s%s%s %s, %s" % (pred, VEC[vop] if vop < len(VEC) else "v?%d" % vop, sat,
                                     dest(b(w[0], 0, 6), vmask, export, ps, b(w[0], 6, 1)), srcs))
    if smask or (sop != 50 and sop >= 27 and sop <= 39):
        sat = "_sat" if b(w[0], 25, 1) else ""
        name = SCA[sop] if sop < len(SCA) else "s?%d" % sop
        if 42 <= sop <= 47:
            # constant op temp: src3 constant .x, temp register from bits
            treg = (sop & 1) | (b(w[2], 29, 1) << 1) | (b(w[1], 0, 8) & 0x3C)
            src = "%s, r%d" % (alu_src(w, 3, ps), treg)
        else:
            src = alu_src(w, 3, ps)
        out.append("%s%s%s %s, %s" % (pred, name, sat,
                                     dest(b(w[0], 8, 6), smask, export, ps, b(w[0], 14, 1)), src))
    return out or ["%snop" % pred]


def fetch(w):
    op = b(w[0], 0, 5)
    name = FETCH.get(op, "fetch%d" % op)
    src = b(w[0], 5, 6)
    dst = b(w[0], 12, 6)
    const = b(w[0], 20, 5)
    pred = "(p%s) " % ("" if b(w[2], 31, 1) else "!") if b(w[1], 31, 1) else ""
    if op == 0:
        sw = b(w[0], 30, 2)
        dsw = b(w[1], 0, 12)
        d = "".join("xyzw01?_"[b(dsw, 3 * k, 3)] for k in range(4))
        return "%svfetch r%d.%s, r%d.%s, vf%d (fmt %d stride %d off %d%s)" % (
            pred, dst, d, src, C[sw], const * 3 + b(w[0], 25, 2), b(w[1], 16, 6), b(w[2], 0, 8),
            sb(w[2], 8, 23), " mini" if b(w[1], 30, 1) else "")
    ssw = b(w[0], 26, 6)
    s = "".join(C[b(ssw, 2 * k, 2)] for k in range(3))
    dsw = b(w[1], 0, 12)
    d = "".join("xyzw01?_"[b(dsw, 3 * k, 3)] for k in range(4))
    extra = []
    if b(w[2], 2, 7):
        extra.append("lodbias %d/16" % sb(w[2], 2, 7))
    if b(w[1], 29, 1):
        extra.append("reg_lod")
    if b(w[2], 0, 1):
        extra.append("reg_grad")
    ox, oy = sb(w[2], 16, 5), sb(w[2], 21, 5)
    if ox or oy:
        extra.append("off %g,%g" % (ox / 2, oy / 2))
    if b(w[0], 25, 1):
        extra.append("unnorm")
    return "%s%s r%d.%s, r%d.%s, tf%d %s %s" % (pred, name, dst, d, src, s, const,
                                              DIM[b(w[2], 14, 2)], " ".join(extra))


def disasm(dwords, ps):
    lines = []
    execs = []
    i = 0
    end_cf = None
    while True:
        if end_cf is not None and i >= end_cf * 2:
            break
        k = i // 2
        if 3 * k + 2 >= len(dwords):
            break
        w0, w1, w2 = dwords[3 * k:3 * k + 3]
        cf = (w0 | (w1 & 0xFFFF) << 32) if i % 2 == 0 else ((w1 >> 16) | (w2 << 16))
        op = b(cf, 44, 4)
        name = CF[op]
        if op in (1, 2, 3, 4, 5, 6, 13, 14):
            addr, cnt, seq = b(cf, 0, 12), b(cf, 12, 3), b(cf, 16, 12)
            if end_cf is None or addr < end_cf:
                end_cf = addr if end_cf is None else min(end_cf, addr)
            cond = ""
            if op in (3, 4):
                cond = " %sb%d" % ("" if b(cf, 42, 1) else "!", b(cf, 34, 8))
            elif op in (5, 6, 13, 14):
                cond = " %sp0" % ("" if b(cf, 42, 1) else "!")
            lines.append("  %s%s  @%d x%d" % (name, cond, addr, cnt))
            for j in range(cnt):
                a = addr + j
                w = dwords[3 * a:3 * a + 3]
                if len(w) < 3:
                    break
                if seq >> (2 * j) & 1:
                    lines.append("      %3d  %s" % (a, fetch(w)))
                else:
                    for t in alu(w, ps):
                        lines.append("      %3d  %s" % (a, t))
            if op in (2, 4, 6, 14):
                lines.append("  -- end")
                if end_cf is not None and i + 1 >= end_cf * 2:
                    break
        elif op in (7, 8):
            lines.append("  %s i%d @%d" % (name, b(cf, 16, 5), b(cf, 0, 13)))
        elif op in (9, 11):
            c = "always" if b(cf, 13, 1) else ("%sp0" % ("" if b(cf, 42, 1) else "!") if b(cf, 14, 1)
                                               else "%sb%d" % ("" if b(cf, 42, 1) else "!", b(cf, 34, 8)))
            lines.append("  %s %s @%d" % (name, c, b(cf, 0, 13)))
        elif op == 12:
            lines.append("  alloc %s %d" % (["?", "pos/interp", "param", "memory"][b(cf, 41, 2)], b(cf, 0, 3)))
        elif op == 0:
            pass
        else:
            lines.append("  " + name)
        i += 1
    return "\n".join(lines)


def main():
    sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
    import mp3_frame
    _, shaders, _ = mp3_frame.read(sys.argv[1])
    for h in sys.argv[2:]:
        kind, dw = shaders[int(h, 16)]
        print("shader %s (%s, %d dwords)" % (h, "pixel" if kind == 1 else "vertex", len(dw)))
        print(disasm(dw, kind == 1))


if __name__ == "__main__":
    main()
