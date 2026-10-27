#!/usr/bin/env python3
"""Synthesize a toast: several people clinking glasses, as sounds/glasses-clinking.mp3.

Pure Python (no numpy): each clink is a few inharmonic, decaying glass modes plus a
short click; pairs of glasses ring together at slightly different pitches, spread
across the stereo field, with a little room reverb. Encoded with GStreamer's LAME.
    python3 tools/make_glasses_sound.py
"""
import math
import random
import struct
import subprocess
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "sounds" / "glasses-clinking.mp3"
RATE = 44100
SECONDS = 3.6
MODES = [(1.0, 1.0, 0.9), (2.32, 0.45, 0.45), (4.25, 0.22, 0.22), (6.63, 0.10, 0.12)]  # ratio, gain, decay (s)

random.seed(7)
n = int(RATE * SECONDS)
left, right = [0.0] * n, [0.0] * n


def glass(start, f0, amp, pan):
    """One glass ringing from `start` seconds."""
    s0 = int(start * RATE)
    length = min(n - s0, int(1.6 * RATE))
    gl, gr = amp * math.cos(pan * math.pi / 2), amp * math.sin(pan * math.pi / 2)
    modes = [(2 * math.pi * f0 * r * random.uniform(0.995, 1.005), g, d * random.uniform(0.8, 1.2),
              random.uniform(0, 2 * math.pi)) for r, g, d in MODES]
    beat = random.uniform(2, 6)  # slight wobble, as real glasses have
    for i in range(length):
        t = i / RATE
        v = sum(g * math.exp(-t / d) * math.sin(w * t + ph) for w, g, d, ph in modes)
        v *= 1 + 0.08 * math.sin(2 * math.pi * beat * t)
        if i < 120:  # the hard "tink" of rims touching
            v += random.uniform(-1, 1) * 0.7 * (1 - i / 120)
        left[s0 + i] += v * gl
        right[s0 + i] += v * gr


# a table of people toasting: clinks bunched near the start, thinning out
t = 0.05
while t < 2.4:
    pan = random.uniform(0.15, 0.85)
    amp = random.uniform(0.35, 1.0)
    f = random.uniform(1700, 3300)
    glass(t, f, amp, pan)
    glass(t + random.uniform(0, 0.004), f * random.uniform(1.04, 1.25), amp * random.uniform(0.6, 0.9), pan)
    if random.random() < 0.25:  # a quick second tap
        glass(t + random.uniform(0.07, 0.12), f * random.uniform(0.97, 1.03), amp * 0.5, pan)
    t += random.expovariate(1 / (0.18 + t * 0.12))

# small room: a few decaying early reflections, crossed between channels
for delay, g in [(0.023, 0.28), (0.041, 0.22), (0.067, 0.16), (0.097, 0.11), (0.131, 0.07)]:
    d = int(delay * RATE)
    for i in range(n - 1, d - 1, -1):
        left[i], right[i] = left[i] + g * right[i - d], right[i] + g * left[i - d]

# fade the tail, normalise to -4 dBFS
for i in range(int(0.3 * RATE)):
    k = i / (0.3 * RATE)
    left[n - 1 - i] *= k
    right[n - 1 - i] *= k
peak = max(max(map(abs, left)), max(map(abs, right)))
scale = 10 ** (-4 / 20) * 32767 / peak

wav = OUT.with_suffix(".wav")
OUT.parent.mkdir(exist_ok=True)
with wave.open(str(wav), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(b"".join(struct.pack("<hh", int(a * scale), int(b * scale)) for a, b in zip(left, right)))
subprocess.run(["gst-launch-1.0", "-q", "filesrc", f"location={wav}", "!", "wavparse", "!", "audioconvert", "!",
                "lamemp3enc", "target=bitrate", "bitrate=192", "!", "id3v2mux", "!", "filesink", f"location={OUT}"],
               check=True)
wav.unlink()
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
