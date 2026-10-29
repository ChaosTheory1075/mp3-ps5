#!/usr/bin/env python3
"""What the PS5 played: logs/mp3_audio.raw (48 kHz s16 stereo, 256-sample frames).

    analyze_audio.py mp3_audio.raw [out.wav]

Per second: loudness (RMS), peak, clicks (sample-to-sample jumps a real
signal doesn't make), frames repeated exactly or nearly (a stuck voice or a
replayed buffer buzzes at 48000/256 = 187.5 Hz), and the strongest
frequency. Also writes a .wav to listen to.
"""
import struct
import sys
import wave

import numpy as np

RATE, FRAME = 48000, 256


def main():
    pcm = np.fromfile(sys.argv[1], dtype="<i2").reshape(-1, 2).astype(np.float32) / 32768.0
    if len(sys.argv) > 2:
        with wave.open(sys.argv[2], "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(RATE)
            w.writeframes((pcm * 32767).astype("<i2").tobytes())
    mono = pcm.mean(axis=1)
    frames = mono[: len(mono) // FRAME * FRAME].reshape(-1, FRAME)
    print(f"{len(pcm) / RATE:.1f} s")
    print(" sec   rms   peak  clicks  repeat  near-repeat  top Hz")
    per = RATE // FRAME
    for s in range(0, len(frames) // per):
        f = frames[s * per:(s + 1) * per]
        x = f.reshape(-1)
        rms = float(np.sqrt(np.mean(x * x)))
        peak = float(np.max(np.abs(x)))
        clicks = int(np.sum(np.abs(np.diff(x)) > 0.5))
        nz = np.abs(f).max(axis=1) > 1e-4
        same = int(np.sum(np.all(f[1:] == f[:-1], axis=1) & nz[1:]))
        diff = np.sqrt(np.mean((f[1:] - f[:-1]) ** 2, axis=1))
        level = np.sqrt(np.mean(f[1:] ** 2, axis=1)) + 1e-9
        near = int(np.sum((diff / level < 0.05) & nz[1:]))
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        top = float(np.argmax(spec[20:]) + 20) * RATE / len(x)
        print(f"{s:4d} {rms:6.3f} {peak:6.3f} {clicks:7d} {same:7d} {near:11d} {top:8.0f}")


if __name__ == "__main__":
    main()
