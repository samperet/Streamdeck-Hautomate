#!/usr/bin/env python3
"""Draw 60s analog hi-fi key faces for the Stream Deck.

Writes 144×144 PNGs to ../ (mounted in the container at /icons).
Everything is drawn at 4× and downsampled for smooth edges.
Run:  python3 icons/src/gen_icons.py
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SRC = Path(__file__).resolve().parent
OUT = SRC.parent
FONT = SRC / "Michroma-Regular.ttf"
W, SIZE = 576, 144  # work canvas, output size

# 1960s hi-fi palette
WAL_L, WAL_D = (112, 70, 40), (52, 30, 17)
CREAM_T, CREAM_B = (244, 236, 214), (214, 202, 172)
INK = (42, 30, 22)
BRASS_T, BRASS_B = (226, 196, 128), (160, 122, 58)
ALU = (196, 198, 200)
AMBER_HOT, AMBER_EDGE = (255, 214, 140), (214, 128, 40)
RED = (176, 36, 26)
ENAMEL = {
    "evening": (200, 92, 34), "bright": (214, 162, 40), "relax": (112, 122, 46),
    "movie": (36, 104, 106), "night": (38, 48, 88),
}


def lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def font(px):
    return ImageFont.truetype(str(FONT), px)


def fit(text, max_w, max_px):
    px = max_px
    while px > 20 and font(px).getlength(text) > max_w:
        px -= 2
    return font(px)


def save(img, name):
    img.resize((SIZE, SIZE), Image.LANCZOS).save(OUT / f"{name}.png", optimize=True)
    print("  ", name)


# ── Textures (built once) ─────────────────────────────────────────────
def make_wood():
    rnd = random.Random(7)
    waves = [(rnd.uniform(0.004, 0.011), rnd.uniform(0, 6.3), rnd.uniform(12, 36)) for _ in range(3)]
    img = Image.new("RGB", (W, W))
    px = img.load()
    for x in range(W):
        off = sum(a * math.sin(x * f + p) for f, p, a in waves)
        for y in range(W):
            v = (y + off) * 0.085
            g = (0.5 + 0.5 * math.sin(v + 0.7 * math.sin(v * 0.31))) ** 3
            px[x, y] = lerp(WAL_L, WAL_D, 0.25 + g * 0.6 + rnd.random() * 0.08)
    return img.filter(ImageFilter.GaussianBlur(1.2))


def make_alu():
    rnd = random.Random(3)
    rows = [rnd.gauss(0, 1) for _ in range(W)]
    img = Image.new("RGB", (W, W))
    px = img.load()
    for y in range(W):
        for x in range(W):
            light = 14 * (1 - (x + y) / (2 * W))  # soft light from top-left
            v = rows[y] * 7 + rnd.gauss(0, 2.5) + light
            px[x, y] = tuple(max(0, min(255, round(c + v))) for c in ALU)
    return img


WOOD, ALUMINIUM = make_wood(), make_alu()


def wood():
    return WOOD.copy()


def alu():
    img = ALUMINIUM.copy()
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, W - 5, W - 5], outline=(120, 122, 126), width=6)  # plate edge
    return img


def black():
    return Image.new("RGB", (W, W), (14, 12, 11))


# ── Drawing helpers ───────────────────────────────────────────────────
def grad_rrect(img, box, top, bot, r):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    g = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(g)
    for y in range(h):
        gd.line([(0, y), (w, y)], fill=lerp(top, bot, y / max(1, h - 1)))
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    img.paste(g, (x0, y0), m)


def shadow(img, box, r, off=14, blur=16, ellipse=False):
    sh = Image.new("L", (W, W), 0)
    b = [box[0] + off // 2, box[1] + off, box[2] + off // 2, box[3] + off]
    (ImageDraw.Draw(sh).ellipse if ellipse else lambda bb, fill: ImageDraw.Draw(sh).rounded_rectangle(bb, r, fill=fill))(b, fill=150)
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    img.paste((0, 0, 0), (0, 0), sh)


def engrave(img, xy, text, f, ink=INK, light=(255, 250, 235), anchor="mm"):
    d = ImageDraw.Draw(img)
    d.text((xy[0], xy[1] + 3), text, font=f, fill=light, anchor=anchor)
    d.text(xy, text, font=f, fill=ink, anchor=anchor)


def screw(d, cx, cy, r=16):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(150, 120, 70), outline=(90, 70, 40), width=3)
    d.line([cx - r + 5, cy - 4, cx + r - 5, cy + 4], fill=(80, 60, 34), width=5)


def brass_plate(img, box, label, max_px=72, chevron=None):
    shadow(img, box, 18, off=8, blur=8)
    grad_rrect(img, box, BRASS_T, BRASS_B, 18)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, 18, outline=(112, 84, 36), width=4)
    d.line([x0 + 20, y0 + 8, x1 - 20, y0 + 8], fill=(246, 226, 170), width=3)
    pad = 54 + (40 if chevron else 0)
    f = fit(label, (x1 - x0) - pad * 2 + 30, max_px)
    cx = (x0 + x1) / 2 + (18 if chevron == "left" else -18 if chevron == "right" else 0)
    engrave(img, (cx, (y0 + y1) / 2), label, f, light=(250, 230, 180))
    screw(d, x0 + 26, (y0 + y1) / 2, 11)
    screw(d, x1 - 26, (y0 + y1) / 2, 11)
    if chevron:
        tw = f.getlength(label)
        cy = (y0 + y1) / 2
        if chevron == "right":
            ax = cx + tw / 2 + 22
            d.polygon([(ax, cy - 18), (ax + 20, cy), (ax, cy + 18)], fill=INK)
        else:
            ax = cx - tw / 2 - 22
            d.polygon([(ax, cy - 18), (ax - 20, cy), (ax, cy + 18)], fill=INK)


# ── Key faces ─────────────────────────────────────────────────────────
def piano_key(name, glyph):
    img = black()
    box = (26, 18, W - 26, W - 30)
    shadow(img, box, 34, off=16, blur=10)
    grad_rrect(img, box, CREAM_T, CREAM_B, 34)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([box[0], box[3] - 70, box[2], box[3]], 34, fill=(176, 162, 128))  # front lip
    d.rectangle([box[0], box[3] - 70, box[2], box[3] - 50], fill=(196, 184, 150))
    d.line([box[0] + 40, box[1] + 10, box[2] - 40, box[1] + 10], fill=(255, 252, 240), width=5)
    cx, cy, s = W / 2, 230, 74

    def tri(x, y, s, right=True):
        k = 1 if right else -1
        return [(x - k * s * 0.8, y - s), (x + k * s * 0.9, y), (x - k * s * 0.8, y + s)]

    shapes = []
    if glyph == "play":
        shapes.append(("poly", tri(cx + 6, cy, s)))
    elif glyph == "pause":
        shapes += [("rect", [cx - 62, cy - s, cx - 20, cy + s]), ("rect", [cx + 20, cy - s, cx + 62, cy + s])]
    elif glyph in ("next", "prev"):
        r = glyph == "next"
        k = 1 if r else -1
        shapes += [("poly", tri(cx - k * 44, cy, 58, r)), ("poly", tri(cx + k * 22, cy, 58, r)),
                   ("rect", [min(cx + k * 70, cx + k * 92), cy - 58, max(cx + k * 70, cx + k * 92), cy + 58])]  # end-stop bar
    for kind, geo in shapes:  # highlight then ink = engraved
        sh = [(x, y + 4) for x, y in geo] if kind == "poly" else [geo[0], geo[1] + 4, geo[2], geo[3] + 4]
        (d.polygon if kind == "poly" else d.rectangle)(sh, fill=(255, 250, 236))
        (d.polygon if kind == "poly" else d.rectangle)(geo, fill=INK)
    save(img, name)


def knob_face(img, cx, cy, r, angle_deg=-40):
    shadow(img, (cx - r, cy - r, cx + r, cy + r), 0, off=18, blur=14, ellipse=True)
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(58, 58, 60))
    for i in range(72):  # knurled skirt
        a = i * math.tau / 72
        d.line([cx + math.cos(a) * (r - 26), cy + math.sin(a) * (r - 26), cx + math.cos(a) * r, cy + math.sin(a) * r],
               fill=(128, 128, 132) if i % 2 else (34, 34, 36), width=5)
    ri = r - 34
    rnd = random.Random(r)
    for k in range(ri, 0, -2):  # spun-aluminium cap
        v = 186 + round(28 * (k / ri)) + rnd.randint(-10, 10)
        d.ellipse([cx - k, cy - k, cx + k, cy + k], fill=(v, v, v + 3))
    a = math.radians(angle_deg - 90)
    d.line([cx + math.cos(a) * ri * 0.25, cy + math.sin(a) * ri * 0.25, cx + math.cos(a) * ri * 0.92, cy + math.sin(a) * ri * 0.92],
           fill=INK, width=12)


def volume_knob(name, sign):
    img = alu()
    knob_face(img, 288, 250, 182, angle_deg=40 if sign == "+" else -40)
    engrave(img, (288, 506), f"VOL {sign}", font(64), light=(236, 238, 240))
    save(img, name)


def toggle(name, muted):
    img = alu()
    d = ImageDraw.Draw(img)
    engrave(img, (288, 70), "SOUND", font(54), light=(236, 238, 240))
    engrave(img, (288, 516), "MUTE", font(54), light=(236, 238, 240))
    cx, cy = 288, 300
    d.ellipse([cx - 92, cy - 92, cx + 92, cy + 92], fill=(150, 152, 156), outline=(90, 90, 94), width=6)  # hex nut
    hexagon = [(cx + 74 * math.cos(math.radians(60 * i + 30)), cy + 74 * math.sin(math.radians(60 * i + 30))) for i in range(6)]
    d.polygon(hexagon, fill=(214, 216, 220), outline=(110, 110, 114))
    d.ellipse([cx - 42, cy - 42, cx + 42, cy + 42], fill=(70, 70, 74))
    tip_y = cy + (150 if muted else -150)
    lever = Image.new("RGBA", (W, W))
    ld = ImageDraw.Draw(lever)
    ld.polygon([(cx - 26, cy), (cx + 26, cy), (cx + 16, tip_y), (cx - 16, tip_y)], fill=(222, 224, 228))
    ld.line([(cx - 10, cy), (cx - 6, tip_y)], fill=(255, 255, 255), width=6)
    ld.ellipse([cx - 28, tip_y - 28, cx + 28, tip_y + 28], fill=(236, 238, 242), outline=(120, 120, 126), width=3)
    sh = lever.split()[3].filter(ImageFilter.GaussianBlur(8))
    img.paste((0, 0, 0), (14, 16), sh.point(lambda p: p * 0.55))
    img.paste(lever, (0, 0), lever)
    lamp = (226, 50, 30) if muted else (90, 34, 28)  # red pilot lamp lit when muted
    d = ImageDraw.Draw(img)
    d.ellipse([440, 270, 500, 330], fill=(60, 60, 64))
    d.ellipse([448, 278, 492, 322], fill=lamp)
    if muted:
        d.ellipse([456, 284, 472, 300], fill=(255, 190, 170))
    save(img, name)


def vu_meter(name, playing):
    img = black()
    d = ImageDraw.Draw(img)
    face = (34, 34, 542, 372)
    d.rounded_rectangle([28, 28, 548, 378], 22, fill=(170, 172, 176))  # chrome bezel
    hot, edge = (AMBER_HOT, AMBER_EDGE) if playing else ((206, 196, 170), (150, 136, 110))
    g = Image.new("RGB", (face[2] - face[0], face[3] - face[1]))
    gp = g.load()
    fw, fh = g.size
    for y in range(fh):
        for x in range(fw):
            t = math.hypot((x - fw / 2) / fw, (y - fh * 0.75) / fh) * 1.7
            gp[x, y] = lerp(hot, edge, t)
    m = Image.new("L", g.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, fw - 1, fh - 1], 16, fill=255)
    img.paste(g, face[:2], m)
    # Scale and needle are drawn on a copy, then pasted back through the window's shape so nothing spills out.
    layer = img.copy()
    d = ImageDraw.Draw(layer)
    px, py, R = 288, 430, 300
    for i in range(21):  # scale
        t = i / 20
        a = math.radians(-44 + 88 * t - 90)
        r0 = R - (40 if i % 5 == 0 else 24)
        d.line([px + math.cos(a) * r0, py + math.sin(a) * r0, px + math.cos(a) * R, py + math.sin(a) * R],
               fill=RED if t > 0.72 else INK, width=6 if i % 5 == 0 else 4)
    d.arc([px - R, py - R, px + R, py + R], -134, -46 - 24, fill=INK, width=5)
    d.arc([px - R, py - R, px + R, py + R], -46 - 24, -46, fill=RED, width=12)
    d.text((288, 250), "VU", font=font(64), fill=INK, anchor="mm")
    a = math.radians((14 if playing else -42) - 90)
    d.line([px, py, px + math.cos(a) * (R - 6), py + math.sin(a) * (R - 6)], fill=(30, 22, 18), width=6)
    d.pieslice([px - 64, py - 64, px + 64, py + 64], 180, 360, fill=(40, 36, 34))  # needle pivot cover
    clip = Image.new("L", (W, W), 0)
    ImageDraw.Draw(clip).rounded_rectangle(face, 16, fill=255)
    img.paste(layer, (0, 0), clip)
    save(img, name)


def nav_plate(name, label, chevron="right"):
    img = wood()
    brass_plate(img, (34, 196, W - 34, 380), label, chevron=chevron)
    save(img, name)


def radio_dial(name, n):
    img = black()
    d = ImageDraw.Draw(img)
    glass = (24, 40, 552, 300)
    d.rounded_rectangle([18, 34, 558, 306], 14, fill=(150, 120, 70))  # brass frame
    grad_rrect(img, glass, (255, 208, 128), (226, 150, 58), 10)
    d = ImageDraw.Draw(img)
    f = font(28)
    for i, mhz in enumerate(range(88, 110, 4)):
        x = 64 + i * 90
        d.line([x, 70, x, 120], fill=INK, width=5)
        d.text((x, 150), str(mhz), font=f, fill=INK, anchor="mm")
        if i < 5:
            for k in range(1, 4):
                d.line([x + k * 22.5, 70, x + k * 22.5, 98], fill=INK, width=3)
    d.text((288, 240), "FM  ·  MHz", font=font(26), fill=(110, 70, 30), anchor="mm")
    x = 64 + (n - 1) * (450 / 7)
    d.line([x, 54, x, 290], fill=(200, 26, 18), width=8)  # tuning pointer
    engrave(img, (288, 384), "PRESET", font(40), ink=(236, 226, 200), light=(0, 0, 0))
    engrave(img, (288, 486), str(n), font(110), ink=(244, 236, 214), light=(0, 0, 0))
    save(img, name)


def edison(name, label):
    img = wood()
    d = ImageDraw.Draw(img)
    cx, cy, r = 288, 200, 132
    glow = Image.new("L", (W, W), 0)
    ImageDraw.Draw(glow).ellipse([cx - r - 50, cy - r - 50, cx + r + 50, cy + r + 50], fill=170)
    glow = glow.filter(ImageFilter.GaussianBlur(50))
    img.paste((255, 170, 60), (0, 0), glow)
    bulb = Image.new("RGBA", (W, W))
    bd = ImageDraw.Draw(bulb)
    for k in range(r, 0, -3):  # warm glass
        bd.ellipse([cx - k, cy - k * 1.08, cx + k, cy + k * 1.08], fill=lerp((255, 236, 170), (232, 150, 50), k / r) + (235,))
    bd.polygon([(cx - 70, cy + 110), (cx + 70, cy + 110), (cx + 56, cy + 170), (cx - 56, cy + 170)], fill=(236, 160, 64, 235))
    img.paste(bulb, (0, 0), bulb)
    d = ImageDraw.Draw(img)
    pts = [(cx - 60, cy + 20)] + [(cx - 60 + i * 15, cy - 30 + (i % 2) * 40) for i in range(9)] + [(cx + 60, cy + 20)]
    d.line(pts, fill=(255, 252, 230), width=6, joint="curve")  # filament
    d.line([(cx - 60, cy + 20), (cx - 20, cy + 150)], fill=(120, 80, 40), width=4)
    d.line([(cx + 60, cy + 20), (cx + 20, cy + 150)], fill=(120, 80, 40), width=4)
    d.ellipse([cx - 120, cy - 110, cx - 70, cy - 40], fill=(255, 250, 230))  # glass highlight
    for i in range(4):  # brass screw base
        y = cy + 170 + i * 22
        d.rounded_rectangle([cx - 58, y, cx + 58, y + 20], 8, fill=lerp(BRASS_T, BRASS_B, i / 3), outline=(110, 80, 34), width=2)
    brass_plate(img, (40, 456, W - 40, 552), label.upper(), max_px=54, chevron=None)
    save(img, name)


def starburst(d, cx, cy, r, fill, w=7):
    for i in range(8):
        a = i * math.pi / 4
        rr = r if i % 2 == 0 else r * 0.6
        d.line([cx, cy, cx + math.cos(a) * rr, cy + math.sin(a) * rr], fill=fill, width=w)
    d.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=fill)
    for a, rr in ((0.4, r * 0.9), (2.0, r * 0.75), (3.6, r * 0.85), (5.1, r * 0.7)):  # atomic dots
        x, y = cx + math.cos(a) * rr, cy + math.sin(a) * rr
        d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=fill)


def enamel(name, label, color):
    img = Image.new("RGB", (W, W), color)
    d = ImageDraw.Draw(img)
    hi = lerp(color, (255, 255, 255), 0.18)
    for y in range(0, W, 4):  # slight top-to-bottom sheen
        d.line([0, y, W, y], fill=lerp(hi, color, y / W))
    cream = (244, 234, 208)
    d.rounded_rectangle([30, 30, W - 30, W - 30], 40, outline=cream, width=10)
    starburst(d, 288, 210, 120, cream)
    f = fit(label.upper(), W - 120, 76)
    d.text((288, 446), label.upper(), font=f, fill=cream, anchor="mm")
    save(img, name)


def big_red(name, label):
    img = wood()
    cx, cy, r = 288, 220, 170
    shadow(img, (cx - r, cy - r, cx + r, cy + r), 0, off=20, blur=16, ellipse=True)
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(200, 202, 206), outline=(110, 110, 114), width=6)  # chrome bezel
    rr = r - 34
    for k in range(rr, 0, -2):
        t = math.hypot(k / rr, 0)
        d.ellipse([cx - k - (rr - k) * 0.18, cy - k - (rr - k) * 0.3, cx + k - (rr - k) * 0.18, cy + k - (rr - k) * 0.3],
                  fill=lerp((236, 84, 60), (130, 18, 12), t))
    d.ellipse([cx - 80, cy - 100, cx - 20, cy - 60], fill=(255, 170, 150))
    brass_plate(img, (40, 438, W - 40, 548), label, max_px=58, chevron=None)
    save(img, name)


def power(name):
    img = alu()
    d = ImageDraw.Draw(img)
    cx, cy = 288, 228
    d.ellipse([cx - 130, cy - 130, cx + 130, cy + 130], fill=(214, 216, 220), outline=(110, 110, 114), width=8)
    for i in range(12):  # faceted red jewel
        a0, a1 = i * math.tau / 12, (i + 1) * math.tau / 12
        col = lerp((240, 70, 50), (120, 12, 8), (math.sin(a0 + 0.8) + 1) / 2)
        d.polygon([(cx, cy), (cx + math.cos(a0) * 96, cy + math.sin(a0) * 96), (cx + math.cos(a1) * 96, cy + math.sin(a1) * 96)], fill=col)
    d.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], fill=(214, 40, 26))
    d.ellipse([cx - 52, cy - 62, cx - 18, cy - 30], fill=(255, 200, 190))
    engrave(img, (288, 486), "POWER", font(64), light=(236, 238, 240))
    save(img, name)


def source(name, label):
    img = alu()
    knob_face(img, 288, 210, 140, angle_deg={"WI-FI": -60, "BLUETOOTH": -20, "LINE IN": 20, "OPTICAL": 60}[label])
    d = ImageDraw.Draw(img)
    for a in (-60, -20, 20, 60):  # detent dots
        t = math.radians(a - 90)
        x, y = 288 + math.cos(t) * 170, 210 + math.sin(t) * 170
        d.ellipse([x - 8, y - 8, x + 8, y + 8], fill=INK)
    engrave(img, (288, 470), label, fit(label, W - 90, 60), light=(236, 238, 240))
    save(img, name)


def lights_out_game(name):
    """The Lights Out game key: a 3×3 board of glowing coloured tiles, two of them dark."""
    img = Image.new("RGB", (W, W), (0, 0, 0))
    colors = [(255, 82, 82), (255, 160, 40), (255, 214, 0),
              (236, 72, 153), None, (46, 204, 113),
              (139, 92, 246), (56, 152, 255), None]
    cell, gap = 148, 22
    x0 = (W - 3 * cell - 2 * gap) // 2
    boxes = [(x0 + c * (cell + gap), x0 + r * (cell + gap)) for r in range(3) for c in range(3)]
    glow = Image.new("RGB", (W, W), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for (x, y), col in zip(boxes, colors):
        if col:
            gd.rounded_rectangle([x - 6, y - 6, x + cell + 6, y + cell + 6], 40, fill=col)
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(26)), 0.75)
    for (x, y), col in zip(boxes, colors):
        if col:
            grad_rrect(img, (x, y, x + cell, y + cell), lerp(col, (255, 255, 255), 0.35), col, 34)
        else:
            ImageDraw.Draw(img).rounded_rectangle([x, y, x + cell, y + cell], 34, fill=(38, 38, 42))
    save(img, name)


def main():
    piano_key("key-prev", "prev")
    piano_key("key-play", "play")
    piano_key("key-pause", "pause")
    piano_key("key-next", "next")
    volume_knob("knob-vol-up", "+")
    volume_knob("knob-vol-down", "−")
    toggle("toggle-sound", muted=False)
    toggle("toggle-muted", muted=True)
    vu_meter("vu-playing", True)
    vu_meter("vu-idle", False)
    for slug, label, chev in (("genres", "GENRES", "right"), ("lights", "LIGHTS", "right"), ("sources", "SOURCES", "right"),
                              ("home", "HOME", None), ("back", "BACK", "left")):
        nav_plate(f"plate-{slug}", label, chev)
    for n in range(1, 9):
        radio_dial(f"dial-preset-{n}", n)
    for slug in ("living", "kitchen", "dining", "hall", "outside"):
        edison(f"lamp-{slug}", slug)
    for slug, color in ENAMEL.items():
        enamel(f"enamel-{slug}", slug, color)
    big_red("button-all-off", "ALL OFF")
    power("power")
    for label in ("WI-FI", "BLUETOOTH", "LINE IN", "OPTICAL"):
        source(f"source-{label.lower().replace(' ', '-')}", label)
    lights_out_game("game-lights-out")


if __name__ == "__main__":
    main()
