"""Runs a captured Xenos pixel shader on the CPU (numpy, all pixels at once),
to compare against what the GPU produced in a tools/mp3_frame.py capture.

  python xenos_emu.py capture.bin <draw seq> <texture-from-copy map>

Supports the straight-line ALU/fetch code and p0 predication; flow-control
jumps are ignored (predicated code runs masked). Textures are point-sampled.
"""
import numpy as np

import xenos_disasm as dis

b = dis.b


class Shader:
    def __init__(self, dwords):
        self.dw = dwords

    def program(self):
        """(kind, words) for every executed instruction, in order."""
        out = []
        i = 0
        end_cf = None
        dw = self.dw
        while True:
            if end_cf is not None and i >= end_cf * 2:
                break
            k = i // 2
            w0, w1, w2 = dw[3 * k:3 * k + 3]
            cf = (w0 | (w1 & 0xFFFF) << 32) if i % 2 == 0 else ((w1 >> 16) | (w2 << 16))
            op = b(cf, 44, 4)
            if op in (1, 2, 3, 4, 5, 6, 13, 14):
                addr, cnt, seq = b(cf, 0, 12), b(cf, 12, 3), b(cf, 16, 12)
                end_cf = addr if end_cf is None else min(end_cf, addr)
                cond = None
                if op in (5, 6, 13, 14):
                    cond = b(cf, 42, 1)
                for j in range(cnt):
                    a = addr + j
                    out.append(("F" if seq >> (2 * j) & 1 else "A", dw[3 * a:3 * a + 3], cond))
                if op in (2, 4, 6, 14):
                    break
            i += 1
        return out


def swz4(swz):
    return [((swz >> (2 * k)) + k) & 3 for k in range(4)]


def run(shader, consts, textures, interp, n, trace=None):
    """consts: (256,4); textures: {fetch const: (H,W,4) float array};
    interp: {reg: (n,4)}. Returns {export reg: (n,4)}, temps."""
    R = np.zeros((64, n, 4), np.float64)
    for r, v in interp.items():
        R[r] = v
    P = np.zeros(n, bool)
    ps = np.zeros(n)
    exports = {}
    with np.errstate(all="ignore"):
        for kind, w, cond in shader.program():
            if kind == "F":
                op = b(w[0], 0, 5)
                if op != 1:
                    continue
                src, dst, const = b(w[0], 5, 6), b(w[0], 12, 6), b(w[0], 20, 5)
                ssw = b(w[0], 26, 6)
                u = R[src][:, b(ssw, 0, 2)]
                v = R[src][:, b(ssw, 2, 2)]
                tex = textures.get(const)
                if tex is None:
                    val = np.zeros((n, 4))
                else:
                    h, wd = tex.shape[:2]
                    ox, oy = dis.sb(w[2], 16, 5) / 2, dis.sb(w[2], 21, 5) / 2
                    x = np.clip(np.floor(u * wd + ox), 0, wd - 1).astype(int)
                    y = np.clip(np.floor(v * h + oy), 0, h - 1).astype(int)
                    val = tex[y, x]
                mask = np.ones(n, bool)
                if b(w[1], 31, 1):
                    mask = P == bool(b(w[2], 31, 1))
                if cond is not None:
                    mask &= P == bool(cond)
                dsw = b(w[1], 0, 12)
                for c in range(4):
                    s = b(dsw, 3 * c, 3)
                    if s == 7:
                        continue
                    nv = val[:, s] if s < 4 else np.full(n, 0.0 if s == 4 else 1.0)
                    R[dst][:, c] = np.where(mask, nv, R[dst][:, c])
                if trace is not None:
                    print("   %-48s | r%d = %s" % (dis.fetch(w), dst, np.round(R[dst][trace], 5)))
                continue

            def src(i):
                reg = b(w[2], 8 * (3 - i), 8)
                temp = b(w[2], 32 - i, 1)
                sw = swz4(b(w[1], 8 * (3 - i), 8))
                if temp:
                    v = R[reg & 0x3F][:, sw]
                    if reg & 0x80:
                        v = np.abs(v)
                else:
                    v = np.broadcast_to(consts[reg][sw], (n, 4)).astype(np.float64)
                    if b(w[0], 7, 1):
                        v = np.abs(v)
                if b(w[1], 24 + (3 - i), 1):
                    v = -v
                return v

            mask = np.ones(n, bool)
            if b(w[1], 28, 1):
                mask = P == bool(b(w[1], 27, 1))
            if cond is not None:
                mask &= P == bool(cond)
            vop, sop = b(w[2], 24, 5), b(w[0], 26, 6)
            vmask, smask = b(w[0], 16, 4), b(w[0], 20, 4)
            export = b(w[0], 15, 1)
            s1, s2, s3 = src(1), src(2), src(3)
            # Vector op.
            a, c2 = s1, s2
            vr = None
            if vop == 0: vr = a + c2
            elif vop == 1: vr = np.where((a == 0) | (c2 == 0), 0.0, a * c2)
            elif vop == 2: vr = np.maximum(a, c2)
            elif vop == 3: vr = np.minimum(a, c2)
            elif vop == 4: vr = (a == c2) * 1.0
            elif vop == 5: vr = (a > c2) * 1.0
            elif vop == 6: vr = (a >= c2) * 1.0
            elif vop == 7: vr = (a != c2) * 1.0
            elif vop == 8: vr = a - np.floor(a)
            elif vop == 9: vr = np.trunc(a)
            elif vop == 10: vr = np.floor(a)
            elif vop == 11: vr = np.where((a == 0) | (c2 == 0), 0.0, a * c2) + s3
            elif vop == 12: vr = np.where(a == 0, c2, s3)
            elif vop == 13: vr = np.where(a >= 0, c2, s3)
            elif vop == 14: vr = np.where(a > 0, c2, s3)
            elif vop == 15: vr = np.repeat((a * c2).sum(1, keepdims=True), 4, 1)
            elif vop == 16: vr = np.repeat((a[:, :3] * c2[:, :3]).sum(1, keepdims=True), 4, 1)
            elif vop == 17: vr = np.repeat((a[:, :2] * c2[:, :2]).sum(1, keepdims=True) + s3[:, :1], 4, 1)
            elif vop == 19: vr = np.repeat(a.max(1, keepdims=True), 4, 1)
            else: vr = np.zeros((n, 4))
            if b(w[0], 24, 1):
                vr = np.clip(vr, 0, 1)
            # Scalar op: a = component 3 of the swizzle, b = component 0.
            sa, sb_ = s3[:, 3], s3[:, 0]
            if 42 <= sop <= 47:
                treg = (sop & 1) | (b(w[2], 29, 1) << 1) | (b(w[1], 0, 8) & 0x3C)
                sw = swz4(b(w[1], 0, 8))
                creg = b(w[2], 0, 8)
                ca = consts[creg][sw[3]]
                if b(w[0], 7, 1):
                    ca = abs(ca)
                tb = R[treg][:, sw[0]]
                sa = np.full(n, ca)
                sb_ = tb
                if b(w[1], 24, 1):
                    sa = -sa
            sr = None
            if sop in (0,): sr = sa + sb_
            elif sop == 1: sr = sa + ps
            elif sop == 2: sr = np.where((sa == 0) | (sb_ == 0), 0.0, sa * sb_)
            elif sop in (3, 4): sr = np.where((sa == 0) | (ps == 0), 0.0, sa * ps)
            elif sop == 5: sr = np.maximum(sa, sb_)
            elif sop == 6: sr = np.minimum(sa, sb_)
            elif sop == 7: sr = (sa == 0) * 1.0
            elif sop == 8: sr = (sa > 0) * 1.0
            elif sop == 9: sr = (sa >= 0) * 1.0
            elif sop == 10: sr = (sa != 0) * 1.0
            elif sop == 11: sr = sa - np.floor(sa)
            elif sop == 12: sr = np.trunc(sa)
            elif sop == 13: sr = np.floor(sa)
            elif sop == 14: sr = np.exp2(sa)
            elif sop == 15: sr = np.maximum(np.log2(sa), -3.4e38)
            elif sop == 16: sr = np.log2(sa)
            elif sop in (17, 18, 19): sr = 1.0 / sa
            elif sop in (20, 21, 22): sr = 1.0 / np.sqrt(sa)
            elif sop == 25: sr = sa - sb_
            elif sop == 26: sr = sa - ps
            elif sop in (27, 28, 29, 30):
                p = {27: sa == 0, 28: sa != 0, 29: sa > 0, 30: sa >= 0}[sop]
                P = np.where(mask, p, P)
                sr = np.where(p, 0.0, 1.0)
            elif sop == 33:
                P = np.where(mask, False, P)
                sr = np.full(n, -3.4e38)
            elif sop == 40: sr = np.sqrt(sa)
            elif sop in (42, 43): sr = np.where((sa == 0) | (sb_ == 0), 0.0, sa * sb_)
            elif sop in (44, 45): sr = sa + sb_
            elif sop in (46, 47): sr = sa - sb_
            elif sop == 48: sr = np.sin(sa)
            elif sop == 49: sr = np.cos(sa)
            elif sop == 50: sr = ps
            else: sr = np.zeros(n)
            if b(w[0], 25, 1):
                sr = np.clip(sr, 0, 1)

            def write(dst, val, m, rel):
                for c in range(4):
                    if m >> c & 1:
                        dst[:, c] = np.where(mask, val[:, c] if val.ndim == 2 else val, dst[:, c])

            vd, sd = b(w[0], 0, 6), b(w[0], 8, 6)
            if export:
                vm = vmask & ~smask
                sm = smask & ~vmask
                for reg, m, val in ((vd, vm, vr), (sd, sm, sr)):
                    if m:
                        exports.setdefault(reg, np.zeros((n, 4)))
                        write(exports[reg], val, m, 0)
            else:
                write(R[vd], vr, vmask, 0)
                write(R[sd], sr, smask, 0)
            ps = np.where(mask, sr, ps)
            if trace is not None:
                print("   %-48s | v %s s %.5g p %d" % (" / ".join(dis.alu(w, True)), np.round(vr[trace], 5), sr[trace], P[trace]))
    return exports, R
