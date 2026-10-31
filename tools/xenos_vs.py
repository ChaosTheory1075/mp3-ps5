"""Runs the vertex shaders of a version-2 tools/mp3_frame.py capture on the CPU with the Xenos
rules (xenos_emu.py's ALU: 0 * anything = 0 and so on), on the vertex data the CPU wrote, and
reports the draws whose positions blow up - to tell bad input data (the game, the recompiled
CPU code) from a bad shader translation (the GPU side).

  python xenos_vs.py capture.bin            the draws with exploded or non-finite positions
  python xenos_vs.py capture.bin <draw seq>  one draw: disassembly, fetches, first vertices

Straight-line code, p0 predication and bool-conditional execs; jumps and calls are ignored.
Indexed draws are run over every vertex of the buffer (the index buffer isn't captured).
"""
import sys

import numpy as np

import mp3_frame
import xenos_disasm as dis
import xenos_emu as emu

b, sb = dis.b, dis.sb


def program(dw, bools):
    """(kind, words, p0 condition) for every instruction that runs, bool execs resolved."""
    out = []
    i = 0
    end_cf = None
    while True:
        if end_cf is not None and i >= end_cf * 2:
            break
        k = i // 2
        if 3 * k + 2 >= len(dw):
            break
        w0, w1, w2 = dw[3 * k:3 * k + 3]
        cf = (w0 | (w1 & 0xFFFF) << 32) if i % 2 == 0 else ((w1 >> 16) | (w2 << 16))
        op = b(cf, 44, 4)
        if op in (1, 2, 3, 4, 5, 6, 13, 14):
            addr, cnt, seq = b(cf, 0, 12), b(cf, 12, 3), b(cf, 16, 12)
            end_cf = addr if end_cf is None else min(end_cf, addr)
            cond = None
            run = True
            if op in (3, 4):
                bi = b(cf, 34, 8)
                run = bool(bools[bi >> 5] >> (bi & 31) & 1) == bool(b(cf, 42, 1))
            elif op in (5, 6, 13, 14):
                cond = b(cf, 42, 1)
            if run:
                for j in range(cnt):
                    a = addr + j
                    out.append(("F" if seq >> (2 * j) & 1 else "A", dw[3 * a:3 * a + 3], cond))
            if op in (2, 4, 6, 14):
                break
        i += 1
    return out


def swap_words(words, endian):
    if endian == 1:  # 8 in 16
        return ((words & 0x00FF00FF) << 8) | ((words >> 8) & 0x00FF00FF)
    if endian == 2:  # 8 in 32
        return words.byteswap()
    if endian == 3:  # 16 in 32
        return (words << 16) | (words >> 16)
    return words


def half(u):
    return u.astype(np.uint16).view(np.float16).astype(np.float64)


# dwords per vertex format
WORDS = {6: 1, 7: 1, 16: 1, 17: 1, 25: 1, 26: 2, 31: 1, 32: 2, 33: 1, 34: 2, 35: 4, 36: 1, 37: 2,
         38: 4, 57: 3}


def decode(words, fmt, signed, normalized, exp_adjust):
    """(n, k) dwords of one fetch -> (n, 4) values, missing components (0, 0, 0, 1)."""
    n = words.shape[0]
    out = np.zeros((n, 4))
    out[:, 3] = 1.0
    w = words.astype(np.int64)

    def ints(fields):
        cols = []
        for word, lo, bits in fields:
            v = (w[:, word] >> lo) & ((1 << bits) - 1)
            if signed:
                v = np.where(v >= 1 << (bits - 1), v - (1 << bits), v)
                cols.append(np.maximum(v / ((1 << (bits - 1)) - 1), -1.0) if normalized else v * 1.0)
            else:
                cols.append(v / ((1 << bits) - 1) if normalized else v * 1.0)
        return cols

    if fmt in (36, 37, 38, 57):
        cnt = {36: 1, 37: 2, 38: 4, 57: 3}[fmt]
        vals = [words[:, c].astype(np.uint32).view(np.float32).astype(np.float64) for c in range(cnt)]
    elif fmt == 6:
        vals = ints([(0, 0, 8), (0, 8, 8), (0, 16, 8), (0, 24, 8)])
    elif fmt == 7:
        vals = ints([(0, 0, 10), (0, 10, 10), (0, 20, 10), (0, 30, 2)])
    elif fmt == 16:
        vals = ints([(0, 0, 11), (0, 11, 11), (0, 22, 10)])
    elif fmt == 17:
        vals = ints([(0, 0, 10), (0, 10, 11), (0, 21, 11)])
    elif fmt == 25:
        vals = ints([(0, 0, 16), (0, 16, 16)])
    elif fmt == 26:
        vals = ints([(0, 0, 16), (0, 16, 16), (1, 0, 16), (1, 16, 16)])
    elif fmt == 31:
        vals = [half(w[:, 0] & 0xFFFF), half(w[:, 0] >> 16)]
    elif fmt == 32:
        vals = [half(w[:, 0] & 0xFFFF), half(w[:, 0] >> 16), half(w[:, 1] & 0xFFFF), half(w[:, 1] >> 16)]
    else:  # 33-35: 32-bit integers
        vals = []
        for c in range(fmt - 32):
            v = words[:, c].astype(np.uint32)
            v = v.view(np.int32).astype(np.float64) if signed else v.astype(np.float64)
            vals.append(v / (2.0 ** 31 - 1 if signed else 2.0 ** 32 - 1) if normalized else v)
    for c, v in enumerate(vals):
        out[:, c] = v
    if exp_adjust and fmt not in (31, 32, 36, 37, 38, 57):
        out[:, :len(vals)] *= 2.0 ** exp_adjust
    return out


def run_vs(dw, draw, buffers, n):
    vf = {v["fc"]: v for v in draw["vfetches"]}
    last = {}

    def vfetch(w, R, P, cond):
        if not b(w[1], 30, 1):  # full fetch: the index and the constant (mini fetches reuse them)
            src = R[b(w[0], 5, 6)][:, b(w[0], 30, 2)]
            last["index"] = np.floor(src + 0.5) if b(w[1], 15, 1) else np.floor(src)
            last["fc"] = b(w[0], 20, 5) * 3 + b(w[0], 25, 2)
        fmt = b(w[1], 16, 6)
        stride, off = b(w[2], 0, 8), sb(w[2], 8, 23)
        v = vf.get(last.get("fc"))
        val = np.zeros((n, 4))
        if v is not None and v["key"] in buffers and fmt in WORDS:
            data = swap_words(np.frombuffer(buffers[v["key"]], "<u4").copy(), v["dwords"][1] & 3)
            idx = last["index"].astype(np.int64) * stride + off
            cols = []
            for c in range(WORDS[fmt]):
                i = idx + c
                cols.append(np.where((i >= 0) & (i < len(data)), data[np.clip(i, 0, len(data) - 1)], 0))
            val = decode(np.stack(cols, 1).astype(np.uint32), fmt, b(w[1], 12, 1),
                         b(w[1], 13, 1) == 0, sb(w[1], 24, 6))
        mask = np.ones(n, bool)
        if b(w[1], 31, 1):
            mask = P == bool(b(w[2], 31, 1))
        if cond is not None:
            mask &= P == bool(cond)
        dst, dsw = b(w[0], 12, 6), b(w[1], 0, 12)
        for c in range(4):
            s = b(dsw, 3 * c, 3)
            if s == 7:
                continue
            nv = val[:, s] if s < 4 else np.full(n, 0.0 if s == 4 else 1.0)
            R[dst][:, c] = np.where(mask, nv, R[dst][:, c])

    interp = {0: np.stack([np.arange(n, dtype=np.float64)] + [np.zeros(n)] * 3, 1)}
    exports, _ = emu.run(None, draw["vs_consts"].astype(np.float64), {}, interp, n,
                         program=program(dw, draw["bools"]), vfetch=vfetch)
    return exports


def main():
    frame, shaders, records = mp3_frame.read(sys.argv[1])
    buffers = mp3_frame.read.buffers
    one = int(sys.argv[2]) if len(sys.argv) > 2 else None
    print("frame %d, %d vertex buffers" % (frame, len(buffers)))
    for kind, seq, regs, d in records:
        if kind != "D" or "vs_consts" not in d or (one is not None and seq != one):
            continue
        vs = shaders.get(d["vs"])
        if vs is None or not d["vfetches"]:
            continue
        sizes = [len(buffers.get(v["key"], b"")) // max(4 * v["stride"], 4) for v in d["vfetches"]]
        n = min([s for s in sizes if s] or [0])
        if not n:
            continue
        n = min(n, 200000)
        pos = run_vs(vs[1], d, buffers, n).get(62)
        if pos is None:
            continue
        wv = pos[:, 3]
        with np.errstate(all="ignore"):
            ndc = pos[:, :2] / wv[:, None]
        finite = np.isfinite(pos).all(1)
        big = (np.abs(ndc) > 4).any(1) & (wv > 0.1)
        if one is not None or big.sum() > 0.02 * n or (~finite).sum():
            print("D%d vs %016x prim %d count %d verts %d |pos| max %.3g w %.3g..%.3g nonfinite %d "
                  "off-screen %d" % (seq, d["vs"], d["prim"], d["count"], n,
                                     np.nanmax(np.abs(np.where(finite[:, None], pos, 0))),
                                     np.nanmin(wv), np.nanmax(wv), (~finite).sum(), big.sum()))
        if one is not None:
            print(dis.disasm(vs[1], False))
            print("bools", [hex(x) for x in d["bools"]])
            for v in d["vfetches"]:
                print("vf%d stride %d @%08X size %d bytes %d" % (
                    v["fc"], v["stride"], v["key"][0], v["key"][1], len(buffers.get(v["key"], b""))))
            for i in range(min(8, n)):
                print(i, np.round(pos[i], 3))


if __name__ == "__main__":
    main()
