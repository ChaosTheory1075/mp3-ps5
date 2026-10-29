#!/usr/bin/env python3
"""A small ATRAC9 encoder, for the app's home screen music (sce_sys/snd0.at9:
ATRAC9, 48 kHz stereo, in a RIFF WAVE file, as the PS5 plays it while the
app is highlighted).

    at9enc.py <tables: LibAtrac9's C/src/huffCodes.c> <input audio> <out.at9>
              [--kbps 192] [--gain-db 0] [--seconds S]

SUPERSEDED for snd0.at9: the PS5's own decoder plays this output garbled. It rejects
superframes whose unused bytes aren't 0x01 (this pads with 0x00), and wants the gradient's
start unit >= 1 and precisions <= 15; FFmpeg and LibAtrac9 check none of that. The app's
snd0.at9 comes from BlackBearReloaded's PS5 AT9 Converter (github.com/manoma080/ps5-at9-converter):
    python -I ps5_at9.py -i MaxPayne3Theme.mp3 -o snd0.at9 --start 48 --duration 60 --fade-in 0.5 --fade-out 4

For Max Payne 3 RePrescribed (local only, like the dashboard art: not in git):

    at9enc.py huffCodes.c MaxPayne3Theme.mp3 sce_sys/snd0.at9 --kbps 192 --gain-db -3
              --start 48 --seconds 60 --fade 1.5

What the PS5 plays: UFC 6's snd0.at9 (144 kbps, format version 1, a smpl
chunk looping it, 20 s) and Crackdown PS5's (192 kbps, version 2, 60 s). A
4-minute one and a 336 kbps one stayed silent. This file is laid out as
UFC's (version 1, the loop), at 192 kbps and 60 s.

The bitstream is the one LibAtrac9 (github.com/Thealexbarney/LibAtrac9,
MIT) decodes; its Huffman tables are read from that checkout's huffCodes.c.
The encoder is the simple kind: one stereo block per frame, the right
channel's highs as intensity stereo (the left's shape at the right's level),
no band extension, scale factors coded as deltas, and per frame one gradient
(a noise floor rising with frequency) searched for the most precision that
fits the frame's bytes (fewer bands only when nothing else fits). Input is
decoded with PyAV (pip install av; any format FFmpeg reads) and resampled to
48 kHz. Checked against FFmpeg's ATRAC9 decoder: about 35 dB SNR at 192 kbps
on the song's loudest part (FFmpeg's output is LibAtrac9's with the polarity
inverted, which is inaudible).
"""
import argparse
import math
import re
import struct
import sys

import numpy as np

N = 256  # samples per frame at 48 kHz (FrameSamplesPower 8)
H = N // 2
FRAMES_PER_SUPERFRAME = 4  # superframe index 2
SAMPLE_RATE_INDEX = 7  # 48000
CHANNEL_CONFIG = 2  # one stereo block
# (192 kbps, the most the PS5 plays here - 336 stayed silent: tuned on the theme, 38.5 dB SNR, 42 / 27 / 13.5 dB
# at 0-1 / 1-4 / 4-8 kHz; coding to 21 kHz with both channels in full spread the bits thinner, 30 dB)
BAND_COUNT = 15  # 25 quantization units: 176 of 256 coefficients, up to 16.5 kHz
STEREO_BAND = 12  # the right channel coded up to 22 units (11 kHz), intensity stereo above
SLOPE_FOLLOW = 0.45  # how much of the spectrum's slope the noise floor follows (0: flat, 1: equal SNR)
MIN_BAND = 3

BAND_TO_UNITS = [0, 4, 8, 10, 12, 13, 14, 15, 16, 18, 20, 21, 22, 23, 24, 25, 26, 28, 30]
UNIT_COEFFS = [2, 2, 2, 2, 2, 2, 2, 2, 4, 4, 4, 4, 8, 8, 8, 8, 8, 8, 8, 8] + [16] * 10
UNIT_START = [0, 2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80, 88, 96, 112, 128, 144, 160,
              176, 192, 208, 224, 240, 256]
UNIT_CODEBOOK = [0] * 8 + [1] * 4 + [2] * 8 + [3] * 10
SF_WEIGHTS0 = [0, 0, 0, 1, 1, 2, 2, 2, 2, 2, 2, 3, 2, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 6, 6, 7, 7, 8, 10, 12, 12, 12]
INV_STEP = [2.0 ** p - 0.5 for p in range(16)]  # QuantizerInverseStepSize
BASE_CURVE = [1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 4, 4, 5, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 18, 19, 20, 21, 22,
              23, 24, 25, 26, 26, 27, 27, 28, 28, 28, 29, 29, 29, 29, 30, 30, 30, 30]
CURVES = [[BASE_CURVE[i * 48 // length] for i in range(length)] for length in range(1, 49)]


# ---- the Huffman tables (from huffCodes.c) ----

def load_tables(path):
    src = open(path, encoding="utf-8").read()
    arrays = {}
    for m in re.finditer(r"static const uint(?:8|16)_t (\w+)\[\d+\]\s*=\s*\{([^}]*)\}", src):
        arrays[m.group(1)] = [int(v, 0) for v in m.group(2).replace("\n", " ").split(",") if v.strip()]

    def book(name, value_count, value_power, value_bits):
        return {"bits": arrays[name + "Bits"], "codes": arrays[name + "Codes"], "count": value_count,
                "power": value_power, "vbits": value_bits}

    sf_unsigned = {b: book(f"ScaleFactorsA{b}", 1, 0, b) for b in range(1, 7)}
    spectrum = [[[None] * 4 for _ in range(8)] for _ in range(2)]
    shape = {2: (4, 2), 3: (2, 1), 4: (2, 1), 5: (1, 0), 6: (1, 0), 7: (1, 0)}
    for s, letter in enumerate("AB"):
        for p in range(2, 8):
            for cb in range(4):
                name = f"Spectrum{letter}{p}{cb + 1}"
                if name + "Bits" in arrays:
                    count, power = shape[p] if not (p == 2 and cb == 0) else (2, 1)
                    spectrum[s][p][cb] = book(name, count, power, p)
    return sf_unsigned, spectrum


# ---- the transform (LibAtrac9's IMDCT, inverted) ----

def dct4_matrix():
    """LibAtrac9's Dct4 for N = 256 as a matrix (its output for each unit input)."""
    bits = 8
    sin = [[math.sin(math.pi * (4 * i + 1) / (4 * (1 << b))) for i in range(1 << b)] for b in range(9)]
    cos = [[math.cos(math.pi * (4 * i + 1) / (4 * (1 << b))) for i in range(1 << b)] for b in range(9)]

    def rev(v, n):
        return int(format(v, f"0{n}b")[::-1], 2) if n else 0
    shuffle = [rev(i ^ (i // 2), bits) for i in range(N)]
    x = np.eye(N)
    t = np.empty_like(x)
    i = np.arange(H)
    a, b = x[:, 2 * i], x[:, N - 1 - 2 * i]
    t[:, 2 * i] = a * np.array(cos[bits])[:H] + b * np.array(sin[bits])[:H]
    t[:, 2 * i + 1] = a * np.array(sin[bits])[:H] - b * np.array(cos[bits])[:H]
    stages = bits - 1
    for stage in range(stages):
        blocks, size_bits = 1 << stage, stages - stage
        half_bits = size_bits - 1
        size, half = 1 << size_bits, 1 << half_bits
        s_tab, c_tab = np.array(sin[half_bits])[:half], np.array(cos[half_bits])[:half]
        for blk in range(blocks):
            j = np.arange(half)
            front = (blk * size + j) * 2
            back = front + size
            aa = t[:, front] - t[:, back]
            bb = t[:, front + 1] - t[:, back + 1]
            t[:, front] += t[:, back]
            t[:, front + 1] += t[:, back + 1]
            t[:, back] = aa * c_tab + bb * s_tab
            t[:, back + 1] = aa * s_tab - bb * c_tab
    return t[:, shuffle]  # row k: Dct4 of unit vector k


def windows():
    mdct = np.array([(math.sin(((i + 0.5) / N - 0.5) * math.pi) + 1.0) * 0.5 for i in range(N)])
    imdct = mdct / (mdct[::-1] ** 2 + mdct ** 2)
    return mdct, imdct


def synthesis_matrix():
    """The 2N output samples one frame's spectrum adds (its own frame's N, then the next's N)."""
    d = dct4_matrix()  # y = X @ d
    _, w = windows()
    s = np.zeros((N, 2 * N))
    i = np.arange(H)
    s[:, i] = w[i] * d[:, i + H]
    s[:, i + H] = -w[i + H] * d[:, N - 1 - i]
    s[:, N + i] = -w[N - 1 - i] * d[:, H - 1 - i]
    s[:, N + H + i] = -w[H - 1 - i] * d[:, i]
    return s



# ---- bits ----

class Bits:
    def __init__(self):
        self.v, self.n = 0, 0

    def put(self, value, bits):
        if bits:
            self.v = (self.v << bits) | (value & ((1 << bits) - 1))
            self.n += bits

    def align(self):
        self.put(0, -self.n % 8)

    def bytes(self):
        return self.v.to_bytes(self.n // 8, "big")


# ---- the encoder ----

def gradient(units, start_value, end_value, end_unit):
    """CreateGradient for mode 0 with start unit 0."""
    g = [start_value] * end_unit + [end_value] * (units + 1 - end_unit)
    vc, uc = end_value - start_value, end_unit
    if uc > 0 and vc != 0:
        curve = CURVES[uc - 1]
        if vc < 0:
            scale, base = (-vc - 1) / 31.0, start_value - 1
            for i in range(uc):
                g[i] = base - int(curve[i] * scale)
        else:
            scale, base = (vc - 1) / 31.0, start_value + 1
            for i in range(uc):
                g[i] = base + int(curve[i] * scale)
    return g


def precisions(sf, grad, units):
    """CalculatePrecisions for gradient mode 0 (no mask there): coarse 1..15, the rest fine -
    at most 15 of it (the decoders cap the precision at 30)."""
    prec = [min(30, max(1, sf[i] - grad[i])) for i in range(units)]
    return [min(p, 15) for p in prec], [max(0, p - 15) for p in prec]


def codebook_sets(sf, units):
    sets = [0] * units
    if units <= 1:
        return sets
    s = list(sf[:units]) + [sf[units - 1]]
    avg = (sum(s[:12]) + 6) // 12 if units > 12 else 0
    for i in range(8, units):
        lo = min(s[i - 1], s[i + 1])
        if s[i] - lo >= 3 or s[i] - s[i - 1] + s[i] - s[i + 1] >= 3:
            sets[i] = 1
    for i in range(12, units):
        if sets[i] == 0:
            lo = min(s[i - 1], s[i + 1])
            if s[i] - lo >= 2 and s[i] >= avg - (1 if UNIT_COEFFS[i] == 16 else 0):
                sets[i] = 1
    return sets


def codable(book):
    """The book's values that have a code, with the coefficients each stands for."""
    if "codable" not in book:
        mask, out = (1 << book["vbits"]) - 1, []
        for value, nb in enumerate(book["bits"]):
            if nb:
                c = [(value >> (j * book["vbits"])) & mask for j in range(book["count"])]
                out.append((value, np.array([v - (mask + 1) if v > mask >> 1 else v for v in c], float)))
        book["codable"] = out
    return book["codable"]


class Encoder:
    def __init__(self, tables_path, frame_bytes):
        self.sf_books, self.spec_books = load_tables(tables_path)
        self.frame_bytes = frame_bytes
        self.analysis = np.linalg.pinv(synthesis_matrix())  # (2N, N): exact with the decoder's overlap-add

    @staticmethod
    def scale_factors(spec, units):
        sf = []
        for i in range(units):
            m = float(np.max(np.abs(spec[UNIT_START[i]:UNIT_START[i + 1]])))
            sf.append(0 if m <= 2.0 ** -15 else min(31, max(0, math.ceil(math.log2(m) + 15))))
        return sf

    @staticmethod
    def intensity_scale_factors(right, left, sf_left, start, units):
        """The right channel above the stereo band is the left's (decoded, normalized) shape
        times its own scale: the scale that gives the right channel's level."""
        sf = []
        for i in range(start, units):
            a, b = UNIT_START[i], UNIT_START[i + 1]
            rr = float(np.sqrt(np.mean(right[a:b] ** 2)))
            ll = float(np.sqrt(np.mean((left[a:b] / 2.0 ** (sf_left[i] - 15)) ** 2)))
            sf.append(0 if rr <= 2.0 ** -15 or ll <= 1e-9 else min(31, max(0, round(math.log2(rr / ll) + 15))))
        return sf

    def write_sf(self, bw, sf):
        """Mode 0 (VLC delta offset): weights 0, base 0, 6-bit values."""
        bw.put(0, 2)  # coding mode
        bw.put(0, 3)  # weight index
        bw.put(0, 5)  # base value
        bw.put(3, 2)  # bit length 6
        book = self.sf_books[6]
        v = [sf[i] + SF_WEIGHTS0[i] for i in range(len(sf))]
        bw.put(v[0], 6)
        for i in range(1, len(sf)):
            d = (v[i] - v[i - 1]) & 63
            bw.put(book["codes"][d], book["bits"][d])

    def write_spectrum(self, bw, spec, sf, prec, fine, coded):
        sets = codebook_sets(sf, coded)
        fine_q = []
        for i in range(coded):
            a, b = UNIT_START[i], UNIT_START[i + 1]
            p = prec[i]
            x = spec[a:b] / 2.0 ** (sf[i] - 15)
            lim = (1 << p) - 1
            q = np.clip(np.round(x * INV_STEP[p]), -lim, lim).astype(int)
            if fine[i]:
                rest = x - q / INV_STEP[p]
                fl = (1 << fine[i]) - 1
                fq = np.clip(np.round(rest / FINE_STEP[fine[i]]), -fl, fl).astype(int)
                fine_q.append(fq)
            else:
                fine_q.append(None)
            bits = p + 1
            if bits <= 7:
                book = self.spec_books[sets[i]][bits][UNIT_CODEBOOK[i]]
                mask = (1 << book["vbits"]) - 1
                for g in range(len(q) >> book["power"]):
                    value = 0
                    for j in range(book["count"]):
                        value |= (int(q[g * book["count"] + j]) & mask) << (j * book["vbits"])
                    if not book["bits"][value]:
                        # (some groups have no code - the smallest book has none for two zeros):
                        # the nearest one that has
                        want = x[g * book["count"]:(g + 1) * book["count"]] * INV_STEP[p]
                        value = min(codable(book), key=lambda v: float(np.sum((v[1] - want) ** 2)))[0]
                    bw.put(book["codes"][value], book["bits"][value])
            else:
                for c in q:
                    bw.put(int(c), bits)
        for i in range(coded):
            if fine_q[i] is not None:
                for c in fine_q[i]:
                    bw.put(int(c), fine[i] + 1)

    def frame(self, specs, first, g, tilt, bands, stereo_bands):
        """One frame: a stereo block, the left channel coded up to `bands`, the right up to
        `stereo_bands` and as intensity stereo (the left's shape, its own scale) above."""
        units, stereo_units = BAND_TO_UNITS[bands], BAND_TO_UNITS[stereo_bands]
        left, right = specs
        end_value = max(0, min(31, g + tilt))
        grad = gradient(units, g, end_value, units)
        sf_l = self.scale_factors(left, units)
        sf_r = self.scale_factors(right, stereo_units) + self.intensity_scale_factors(
            right, left, sf_l, stereo_units, units)
        signs = []
        for i in range(stereo_units, units):
            a, b = UNIT_START[i], UNIT_START[i + 1]
            signs.append(1 if float(np.dot(left[a:b], right[a:b])) < 0 else 0)
        bw = Bits()
        bw.put(0 if first else 1, 1)  # (not) the first in its superframe
        bw.put(0, 1)  # band parameters follow
        bw.put(bands - MIN_BAND, 4)
        bw.put(stereo_bands - MIN_BAND, 4)
        bw.put(0, 1)  # no band extension
        bw.put(0, 2)  # gradient mode 0
        bw.put(0, 6)  # start unit
        bw.put(units - 1, 6)  # end unit - 1
        bw.put(g, 5)
        bw.put(end_value, 5)
        bw.put(0, 4)  # boundary
        bw.put(0, 1)  # primary channel: the left
        bw.put(1 if any(signs) else 0, 1)  # joint stereo signs (the right's opposite the left's)
        if any(signs):
            for s in signs:
                bw.put(s, 1)
        bw.put(0, 1)  # no extension data
        for spec, sf, coded in ((left, sf_l, units), (right, sf_r, stereo_units)):
            prec, fine = precisions(sf, grad, units)
            self.write_sf(bw, sf)
            self.write_spectrum(bw, spec, sf, prec, fine, coded)
        bw.align()
        return bw

    def encode_frame(self, specs, first):
        """The most precision that fits the frame's bytes. The noise floor (the gradient)
        follows SLOPE_FOLLOW of the frame's own spectral slope - a line fitted to the left
        channel's scale factors - so the quieter highs keep their share (a flat floor gave the
        loud lows 20 bits a coefficient and 4-16 kHz 1-6, heard as distortion; Crackdown PS5's
        fix). The search is over how far under the spectrum the floor sits, with fewer bands
        only when even the lowest precision doesn't fit."""
        budget = self.frame_bytes * 8
        fewer = [(b, s) for b, s in ((13, 9), (11, 8), (8, 6)) if b < BAND_COUNT]
        for bands, stereo_bands in [(BAND_COUNT, STEREO_BAND)] + fewer:
            units = BAND_TO_UNITS[bands]
            sf = np.array(self.scale_factors(specs[0], units), float)
            live = sf > 0
            if live.sum() >= 2:
                slope, icpt = np.polyfit(np.arange(units, dtype=float)[live], sf[live], 1)
            else:
                slope, icpt = 0.0, float(sf.max()) if units else 0.0
            sf_start, sf_end = icpt, icpt + slope * SLOPE_FOLLOW * (units - 1)
            best = None
            lo, hi = -8, 31  # the floor's depth under the fitted spectrum, in scale factor steps
            while lo <= hi:
                depth = (lo + hi) // 2
                start = int(min(31, max(0, round(sf_start - depth))))
                end = int(min(31, max(0, round(sf_end - depth))))
                if end == start:
                    # (no flat gradient: LibAtrac9 and FFmpeg build that one differently)
                    end = start + 1 if start < 31 else start - 1
                bw = self.frame(specs, first, start, end - start, bands, stereo_bands)
                if bw.n <= budget:
                    best, lo = bw, depth + 1
                else:
                    hi = depth - 1
            if best is not None:
                return best.bytes()
        return self.frame([np.zeros(N), np.zeros(N)], first, 31, 0, MIN_BAND, MIN_BAND).bytes()


FINE_STEP = [3.0518043793392844e-05, 1.0172681264464281e-05, 4.3597205419132631e-06, 2.0345362528928561e-06,
             9.8445302559331759e-07, 4.8441339354591809e-07, 2.4029955742829012e-07, 1.1967860311134448e-07,
             5.9722199204291275e-08, 2.9831909866464167e-08, 1.4908668194134265e-08, 7.4525137468602791e-09,
             3.7258019525568114e-09, 1.8627872668859698e-09, 9.3136520869755679e-10, 4.6567549848772173e-10]


def read_audio(path):
    import av

    with av.open(path) as c:
        res = av.AudioResampler(format="flt", layout="stereo", rate=48000)
        chunks = []
        for frame in c.decode(audio=0):
            for f in res.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1, 2))
        for f in res.resample(None):
            chunks.append(f.to_ndarray().reshape(-1, 2))
    return np.concatenate(chunks).T * 32767.0  # (2, samples), in 16-bit units


def riff(config, sample_count, delay, data, kbps, superframe_bytes):
    """The RIFF file as a snd0.at9 the PS5 plays (laid out as UFC 6's: format version 1, the
    whole clip looped by a smpl chunk)."""
    guid = bytes.fromhex("D242E147BA368D4D88FC61654F8C836C")
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 2, 48000, kbps * 1000 // 8, superframe_bytes, 0, 34,
                      N * FRAMES_PER_SUPERFRAME, 3) + guid + struct.pack("<I", 1) + config + struct.pack("<I", 0)
    fact = struct.pack("<III", sample_count, delay, delay)
    # smpl: sample period 20833 ns (48 kHz), unity note 60, one forward loop over the whole clip
    smpl = struct.pack("<9I", 0, 0, 20833, 60, 0, 0, 0, 1, 24) + struct.pack(
        "<6I", 0, 0, delay, delay + sample_count - 1, 0, 0)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"fact" + struct.pack("<I", len(fact)) + fact
    body += b"smpl" + struct.pack("<I", len(smpl)) + smpl
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tables")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--kbps", type=int, default=192)
    ap.add_argument("--gain-db", type=float, default=0.0)
    ap.add_argument("--start", type=float, default=0.0, help="where the clip starts (seconds)")
    ap.add_argument("--seconds", type=float, help="the clip's length (the PS5's snd0s are short: UFC 6's is 20 s)")
    ap.add_argument("--fade", type=float, default=0.0, help="fade in and out (seconds), for a clean loop")
    args = ap.parse_args()

    superframe_bytes = round(args.kbps * 1000 / 8 * N * FRAMES_PER_SUPERFRAME / 48000)
    frame_bytes = superframe_bytes // FRAMES_PER_SUPERFRAME
    superframe_bytes = frame_bytes * FRAMES_PER_SUPERFRAME
    cfg = (0xFE << 24) | (SAMPLE_RATE_INDEX << 20) | (CHANNEL_CONFIG << 17) | ((frame_bytes - 1) << 5) | (2 << 3)
    config = cfg.to_bytes(4, "big")

    pcm = read_audio(args.input) * 10 ** (args.gain_db / 20)
    pcm = pcm[:, int(args.start * 48000):]
    if args.seconds:
        pcm = pcm[:, :int(args.seconds * 48000)]
    if args.fade:
        n = min(int(args.fade * 48000), pcm.shape[1] // 2)
        ramp = np.linspace(0.0, 1.0, n) ** 2
        pcm[:, :n] *= ramp
        pcm[:, -n:] *= ramp[::-1]
    samples = pcm.shape[1]
    enc = Encoder(args.tables, frame_bytes)
    # N samples of silence first (the decoder's delay); whole superframes, ending in silence
    frames = -(-(samples + 2 * N) // N)
    frames = -(-frames // FRAMES_PER_SUPERFRAME) * FRAMES_PER_SUPERFRAME
    padded = np.zeros((2, (frames + 1) * N))
    padded[:, N:N + samples] = pcm
    specs = np.einsum("cfs,sk->cfk", np.lib.stride_tricks.sliding_window_view(padded, 2 * N, axis=1)[:, ::N][:, :frames],
                      enc.analysis)
    out = bytearray()
    for sfi in range(frames // FRAMES_PER_SUPERFRAME):
        sup = bytearray()
        for k in range(FRAMES_PER_SUPERFRAME):
            f = sfi * FRAMES_PER_SUPERFRAME + k
            sup += enc.encode_frame([specs[0, f], specs[1, f]], k == 0)
        if len(sup) > superframe_bytes:
            sys.exit("superframe overflow")
        out += sup + bytes(superframe_bytes - len(sup))
        if sfi % 200 == 0:
            print(f"\r{sfi * 100 // (frames // FRAMES_PER_SUPERFRAME)}%", end="", flush=True)
    with open(args.output, "wb") as f:
        f.write(riff(config, samples, N, bytes(out), args.kbps, superframe_bytes))
    print(f"\r{args.output}: {samples / 48000:.1f} s, {args.kbps} kbps, {len(out)} bytes of ATRAC9")


if __name__ == "__main__":
    main()
