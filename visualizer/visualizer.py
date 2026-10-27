#!/usr/bin/env python3
"""Microphone music visualizer for the Stream Deck.

Twenty-four white bars (three per key column) bounce to what the microphone hears.
Each key shows its slice of one picture, so a bar runs up through all four rows.

Used by supervisor.py, which pauses the normal deck while this runs. It can also
render offline, for a preview without a deck or microphone:

    python3 visualizer.py --wav song.wav --gif preview.gif
"""
from __future__ import annotations

import math
import os
import threading
import time

import numpy as np
from PIL import Image, ImageDraw

COLS, ROWS = 8, 4          # a Stream Deck XL; set_layout() changes these for the deck plugged in
KEY = 96                   # its key image is 96x96 px (72 on the 15-key deck)
GAP = 24                   # the bezel between keys, in the same units, so bars line up across keys
BARS_PER_KEY = 3
BANDS = COLS * BARS_PER_KEY
F_LO, F_HI = 45.0, 14000.0
FFT = 2048
FPS = 30
RANGE_DB = 42         # loudness range shown, below the loudest band

WHITE, CAP, BG = (255, 255, 255), (255, 255, 255), (0, 0, 0)
W = COLS * KEY + (COLS - 1) * GAP
H = ROWS * KEY + (ROWS - 1) * GAP


def set_layout(deck) -> None:
    """Size the canvas and the bands for this deck (8x4 keys of 96 px, or 5x3 of 72)."""
    global COLS, ROWS, KEY, GAP, BANDS, W, H
    ROWS, COLS = deck.key_layout()
    KEY = deck.key_image_format()["size"][0]
    GAP = round(24 * KEY / 96)
    BANDS = COLS * BARS_PER_KEY
    W = COLS * KEY + (COLS - 1) * GAP
    H = ROWS * KEY + (ROWS - 1) * GAP


class Analyzer:
    """Turns audio blocks into 24 levels between 0 and 1, adapting to the room's loudness."""

    def __init__(self, rate: int):
        self.rate = rate
        self.buf = np.zeros(FFT, dtype=np.float32)
        self.window = np.hanning(FFT).astype(np.float32)
        freqs = np.fft.rfftfreq(FFT, 1 / rate)
        edges = np.geomspace(F_LO, min(F_HI, rate / 2 - 1), BANDS + 1)
        # each band takes at least one FFT bin, so the low bands don't come out empty
        self.bins = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            idx = np.where((freqs >= lo) & (freqs < hi))[0]
            if idx.size == 0:
                idx = np.array([int(np.argmin(np.abs(freqs - (lo + hi) / 2)))])
            self.bins.append(idx)
        # treble carries less energy than bass; tilt so the right side moves too
        self.tilt = np.linspace(0, 14, BANDS)
        self.avg = np.full(BANDS, -80.0)   # each band's recent loudness, to spot hits
        self.ceil = -30.0
        self.noise: float | None = None    # the room's background level, in dBFS
        self.age = 0.0
        self.lock = threading.Lock()

    def feed(self, samples: np.ndarray) -> None:
        s = np.asarray(samples).ravel()
        s = s.astype(np.float32) / 32768.0 if s.dtype.kind == "i" else s.astype(np.float32)
        with self.lock:
            if s.size >= FFT:
                self.buf[:] = s[-FFT:]
            else:
                self.buf = np.roll(self.buf, -s.size)
                self.buf[-s.size:] = s

    def levels(self, dt: float) -> tuple[np.ndarray, float]:
        """Return the 24 levels, and how clearly anything is playing (0 = only room noise)."""
        with self.lock:
            x = self.buf.copy()
        rms = float(np.sqrt(np.mean(x * x)))
        spec = np.abs(np.fft.rfft(x * self.window)) ** 2
        db = np.array([10 * math.log10(spec[i].mean() + 1e-12) for i in self.bins]) + self.tilt
        # ceiling: jumps to the loudest band, relaxes 2 dB a second so quiet songs still fill the deck
        self.ceil = max(float(db.max()), self.ceil - 2.0 * dt, -75.0)
        level = np.clip((db - (self.ceil - RANGE_DB)) / RANGE_DB, 0, 1) ** 1.8
        # a hit is a band jumping above its own recent average; it gives the bounce
        hit = np.clip((db - self.avg - 3) / 12, 0, 1)
        self.avg += (db - self.avg) * min(1.0, dt / 0.35)
        lv = np.clip(0.6 * level + 0.55 * hit * level ** 0.5, 0, 1)
        # fade out when the room is only at its background level (fan, hum, the mic's own hiss)
        rms_db = 20 * math.log10(rms + 1e-9)
        self.age += dt
        if self.noise is None:
            self.noise = rms_db - 10
        # learn quickly for the first 10 s, then slowly, so a long loud song isn't taken for noise
        self.noise = min(rms_db, self.noise + (3.0 if self.age < 10 else 0.5) * dt)
        presence = float(np.clip((rms_db - self.noise - 3) / 9, 0, 1))
        return lv * presence, presence


class Bars:
    """Bars snap up to the music and fall back with gravity; a cap rests briefly on each peak."""

    GRAVITY, CAP_GRAVITY, CAP_HOLD = 4.5, 2.2, 0.25

    def __init__(self):
        self.pos = np.zeros(BANDS)
        self.vel = np.zeros(BANDS)
        self.cap = np.zeros(BANDS)
        self.cap_vel = np.zeros(BANDS)
        self.cap_t = np.zeros(BANDS)

    def step(self, target: np.ndarray, dt: float) -> None:
        up = target > self.pos
        self.vel = np.where(up, 0, self.vel - self.GRAVITY * dt)
        self.pos = np.where(up, target, np.maximum(target, self.pos + self.vel * dt))
        self.vel = np.where(self.pos <= target, 0, self.vel)
        raise_cap = self.pos >= self.cap
        self.cap = np.where(raise_cap, self.pos, self.cap)
        self.cap_t = np.where(raise_cap, self.CAP_HOLD, self.cap_t - dt)
        falling = (~raise_cap) & (self.cap_t <= 0)
        self.cap_vel = np.where(falling, self.cap_vel - self.CAP_GRAVITY * dt, 0)
        self.cap = np.where(falling, np.maximum(self.pos, self.cap + self.cap_vel * dt), self.cap)


def bar_x(i: int) -> tuple[int, int]:
    """Left and right edge of bar i on the whole-deck canvas (three bars inside each key)."""
    col, j = divmod(i, BARS_PER_KEY)
    margin, width, space = 10, 19, 9
    x0 = col * (KEY + GAP) + margin + j * (width + space)
    return x0, x0 + width


def draw(bars: Bars) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for i in range(BANDS):
        x0, x1 = bar_x(i)
        top = H - bars.pos[i] * H
        if bars.pos[i] > 0.004:
            d.rectangle([x0, top, x1 - 1, H - 1], fill=WHITE)
        cy = H - bars.cap[i] * H
        if bars.cap[i] > 0.02:
            d.rectangle([x0, cy - 8, x1 - 1, cy - 4], fill=CAP)
    return img


def key_tiles(img: Image.Image) -> list[Image.Image]:
    """Cut the canvas into the 32 key images, left to right, top to bottom."""
    return [img.crop((c * (KEY + GAP), r * (KEY + GAP), c * (KEY + GAP) + KEY, r * (KEY + GAP) + KEY))
            for r in range(ROWS) for c in range(COLS)]


def open_mic(analyzer_box: list, device: str | None, log=print):
    """Start a microphone stream feeding a new Analyzer (put in analyzer_box[0]).

    Tries the given device, then the system default, then every input device.
    """
    import sounddevice as sd

    candidates: list = []
    if device not in (None, ""):
        candidates.append(int(device) if str(device).isdigit() else device)
    candidates.append(None)
    candidates += [i for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    errors = []
    for dev in candidates:
        try:
            info = sd.query_devices(dev, "input")
            rate = int(info["default_samplerate"])
            channels = 1
            try:
                sd.check_input_settings(device=dev, channels=1, samplerate=rate, dtype="float32")
            except Exception:  # noqa: BLE001  (raw hardware is often stereo-only)
                channels = min(2, int(info["max_input_channels"]))
            analyzer = Analyzer(rate)
            stream = sd.InputStream(device=dev, channels=channels, samplerate=rate, blocksize=512,
                                    dtype="float32",
                                    callback=lambda data, frames, t, status: analyzer.feed(data.mean(axis=1)))
            stream.start()
            analyzer_box.append(analyzer)
            log(f"visualizer: microphone {info['name']!r}, {rate} Hz, {channels} ch")
            return stream
        except Exception as e:  # noqa: BLE001
            errors.append(f"{dev}: {e}")
    raise RuntimeError("no usable microphone: " + "; ".join(errors))


def run(deck, stop: threading.Event, brightness: int = 80, silence_minutes: float = 10.0,
        device: str | None = None, log=print) -> str:
    """Draw on an open deck until a key is pressed, stop is set, or the room stays quiet.

    Returns the reason it ended: "key", "stopped" or "silence".
    """
    from StreamDeck.ImageHelpers import PILHelper

    to_native = getattr(PILHelper, "to_native_key_format", None) or PILHelper.to_native_format
    set_layout(deck)
    reason = {"why": "stopped"}

    def on_key(_deck, _key, pressed):
        if pressed:
            reason["why"] = "key"
            stop.set()

    box: list[Analyzer] = []
    stream = open_mic(box, device or os.environ.get("AUDIO_DEVICE"), log)
    analyzer, bars = box[0], Bars()
    deck.set_key_callback(on_key)
    deck.set_brightness(brightness)
    sent: list[bytes | None] = [None] * (COLS * ROWS)
    quiet_since, last = time.monotonic(), time.monotonic()
    try:
        while not stop.is_set():
            now = time.monotonic()
            dt, last = min(now - last, 0.1), now
            lv, presence = analyzer.levels(dt)
            if presence == 0:
                if now - quiet_since > silence_minutes * 60:
                    reason["why"] = "silence"
                    break
            else:
                quiet_since = now
            bars.step(lv, dt)
            for k, tile in enumerate(key_tiles(draw(bars))):
                raw = tile.tobytes()
                if raw != sent[k]:  # only send keys that changed
                    sent[k] = raw
                    with deck:
                        deck.set_key_image(k, to_native(deck, tile))
            time.sleep(max(0.0, 1 / FPS - (time.monotonic() - now)))
    finally:
        stream.stop()
        stream.close()
        deck.set_key_callback(None)
        with deck:
            deck.reset()
    return reason["why"]


def preview(wav: str, gif: str, seconds: float = 6.0) -> None:
    """Render a GIF of the whole deck from a WAV file, with the deck's bezel drawn in."""
    import wave

    w = wave.open(wav)
    rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
    pcm = np.frombuffer(w.readframes(int(rate * seconds)), dtype={2: np.int16, 4: np.int32}[width])
    pcm = pcm.reshape(-1, ch).mean(axis=1) / (2 ** (8 * width - 1))
    analyzer, bars, frames = Analyzer(rate), Bars(), []
    hop, dt = rate // FPS, 1 / FPS
    for start in range(0, len(pcm) - hop, hop):
        analyzer.feed(pcm[start:start + hop])
        lv, _ = analyzer.levels(dt)
        bars.step(lv, dt)
        img = draw(bars)
        frame = Image.new("RGB", (W + 40, H + 40), (26, 28, 31))
        for k, tile in enumerate(key_tiles(img)):
            r, c = divmod(k, COLS)
            frame.paste(tile, (20 + c * (KEY + GAP), 20 + r * (KEY + GAP)))
        frames.append(frame)
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=int(1000 / FPS), loop=0)
    print(f"wrote {gif}: {len(frames)} frames")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--wav", required=True)
    p.add_argument("--gif", default="visualizer-preview.gif")
    p.add_argument("--seconds", type=float, default=6.0)
    a = p.parse_args()
    preview(a.wav, a.gif, a.seconds)
