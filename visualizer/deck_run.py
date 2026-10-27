#!/usr/bin/env python3
"""The normal deck (home-assistant-streamdeck-yaml), with push-to-talk on blank cover keys.

Out of the box the library acts on a long press only when the key is let go, and then
leaves the folder page. Blank cover keys (long_press: script.deck_listen) instead talk
straight to the deck manager on this computer (manager/deck_manager.py):

  key down  -> start recording on the server's microphone; the key reads "Listening…"
  key up    -> stop, transcribe with Whisper, add the album, artist or playlist you said
  (said nothing) -> fall back to listen mode: add what you start on Spotify

The page stays put; the new cover shows up when the regenerated configuration reloads.

Covers in a person's folder (long_press: script.deck_remove) can be deleted:

  hold      -> a red circle in the middle of the cover fills in, clockwise from 12
  2.5 s     -> the key turns red: "Let go, then tap to delete"
  tap it    -> within 5 s, deletes it from the folder; any other key cancels
  shorter   -> a normal press: plays it
The Now Playing panel (gen_config.py, panel_page) sits on the right of Home, People and
the album pages: the WiiM's cover cut into nine (icon "tile:<n>:<url>"), and under it three
keys that scroll "artist – song" on one line (icon "marquee:<n>:<artist>\n<song>\n<state>"), each
with a small icon under the line: Vol −, play/pause, Vol +. Album pages fill the 3x3 with covers
and keep only the scrolling line. Pages with it stay open after a key's action, so albums and
volume don't send you back. Tapping an album opens Now Playing (the cover big); its Back returns
to the album page. Volume keys (script.deck_volume): steps sized by the level, repeating faster
while held, 1% when you change direction; while it changes, the level shows across the cover.

After IDLE_HOME seconds without a press the deck goes back to Home.

Everything else goes to the library unchanged. Run by supervisor.py in place of the
home-assistant-streamdeck-yaml command.
"""
from __future__ import annotations

import asyncio
import functools
import io
import json
import os
import random
import time
import urllib.request

import home_assistant_streamdeck_yaml as sd
from PIL import Image, ImageDraw, ImageFont
from StreamDeck.ImageHelpers import PILHelper

LISTEN_SVC = "script.deck_listen"
REMOVE_SVC = "script.deck_remove"
HOLD_DELETE = 2.5   # seconds held before a cover asks to be deleted
CONFIRM_WINDOW = 5  # seconds to tap it again
MANAGER = os.environ.get("DECK_MANAGER_URL", "http://127.0.0.1:8765")  # the container shares the host network
MIN_HOLD = 0.4  # shorter is a tap, not a request
IDLE_HOME = 60  # seconds without a press before going back to Home (= IDLE_HOME in gen_config.py and the emulator)
HOME = "Home"
VOLUME_SVC = "script.deck_volume"      # Vol – / Vol + (homeassistant/deck_volume.yaml); direction ±1 and the player
NOW_PLAYING = "Now Playing"  # = NOW_PLAYING in gen_config.py
PLAY_ALBUM = "script.play_spotify_album"
RANDOM = "script.deck_random_album"  # More › Random (gen_config.py): handled here, no such script in HA
# ── volume: all of this is mirrored in emulator/template.html (VOL) and homeassistant/deck_volume.yaml ──
VOL_TIERS = ((15, 1), (35, 2), (60, 3), (101, 5))  # (below this %, step %): fine when quiet, quicker when loud
VOL_REVERSE = 4.0  # s: a slow tap (or one the other way) within this of the last fine-tunes (1% steps)
VOL_TAP_GAP = 0.35  # s between presses that still count as one quick run of taps
VOL_RUN = ((6, 10), (3, 5))  # (from this tap of a run, step %): tap on quickly to move faster
VOL_HOLD = 0.4     # s held before a press turns into a sweep
VOL_SWEEP = 2.6    # s a held key slides from wherever it is to 0 (or 100): 3 s from the press, with VOL_HOLD
VOL_TICK = 0.1     # s between volume_set calls while sweeping
VOL_SHOW = 2.0  # s the level stays across the cover after the last step
HA_URL = ("https" if os.environ.get("WEBSOCKET_PROTOCOL") == "wss" else "http") + "://" + os.environ.get("HASS_HOST", "localhost:8123")
AMBER, DIM, RED = "#f0a020", "#5a5a5a", "#ff5a46"
LIT_RGB = (245, 179, 1, 255)  # LIT in gen_config.py: the volume bar while fine-tuning


def log(msg: str) -> None:
    print(f"[deck-voice] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def _post(path: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(MANAGER + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


async def post(path: str, body: dict, timeout: float = 5) -> dict:
    try:
        return await asyncio.to_thread(_post, path, body, timeout)
    except Exception as e:  # noqa: BLE001
        log(f"{path} failed: {e!r}")
        return {"speech": "The deck manager on the server isn't answering. Check that deck-manager is running.",
                "error": True}


def draw(deck, config, key: int, complete_state, text: str | None, color: str = AMBER) -> None:
    """Show `text` on the key, or None for its normal look."""
    if text is None:
        sd.update_key_image(deck, key=key, config=config, complete_state=complete_state)
        return
    button = config.button(key)
    if button is None:
        return
    shown = button.copy(update={"text": text, "text_color": color, "text_size": 16, "icon": None, "icon_mdi": None})
    image = shown.try_render_icon(complete_state=complete_state, size=deck.key_image_format()["size"])
    with deck:
        deck.set_key_image(key, PILHelper.to_native_format(deck, image))


# ── Now Playing: the cover across nine keys ──
@functools.lru_cache(maxsize=4)
def _cover(url: str) -> Image.Image:
    with urllib.request.urlopen(url if url.startswith("http") else HA_URL + url, timeout=10) as r:
        img = Image.open(io.BytesIO(r.read())).convert("RGB")
    side = min(img.size)  # square it from the middle, in case the art isn't
    return img.crop(((img.width - side) // 2, (img.height - side) // 2, (img.width + side) // 2, (img.height + side) // 2))


@functools.lru_cache(maxsize=32)
def _tile(url: str, n: int, size: tuple[int, int]) -> Image.Image:
    """Key n's part of the cover. The cover spans the 3x3 bezels included (MQ_GAP between keys), and
    the strips under the bezels are left out, as if seen through a window: cut into plain thirds,
    the picture would look stretched across the gaps."""
    key = size[0]
    gap = MQ_GAP * key / 96
    span = round(3 * key + 2 * gap)
    cover = _cover(url).resize((span, span), Image.LANCZOS)
    x, y = round(n % 3 * (key + gap)), round(n // 3 * (key + gap))
    return cover.crop((x, y, x + key, y + key))


# ── "artist – song" on one large line, scrolling slowly across the three keys under the cover ──
# Measured on a 96 px key; the strip includes the bezels between the keys, so letters line up across
# them. emulator/template.html (drawMarquee) uses the same numbers.
MQ_GAP = 24             # the bezel between keys
MQ_PAD = 6              # margin at the strip's ends
MQ_SPEED = 28           # px a second
MQ_PAUSE = 1.5          # seconds the line rests at its start before each pass
MQ_SPACE = 96           # between the end of the line and its next pass
MQ_FPS = 14
MQ_SIZE, MQ_BASE = 40, 62  # the line's font size and baseline
MQ_ARTIST, MQ_SONG, MQ_DIM = "#a8a8a8", "#ffffff", "#5a5a5a"
MQ_SEP = "  –  "
MQ_ICON, MQ_ICON_Y = "#8a8a8a", 84  # the small icon under the line: colour, and its middle's height
_t0 = time.monotonic()


# ── Chicago on every key: fonts/ in the repo, mounted at /fonts (compose.yaml). The library draws all
# key text through _generate_text_image with a font file under its assets; an absolute path wins
# over that folder. The emulator inlines the same file (emulator/build.py). ──
FONT = "/fonts/ChicagoFLF.ttf"
if os.path.exists(FONT):
    sd.DEFAULT_FONT = FONT
    _library_text = sd._generate_text_image
    sd._generate_text_image = functools.lru_cache(maxsize=1000)(
        lambda **kw: _library_text.__wrapped__(**(kw | {"font_filename": FONT})))


@functools.lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(sd.ASSETS_PATH / sd.DEFAULT_FONT), size)


def _marquee_strip(text: str, key: int, t: float) -> Image.Image:
    """The whole three-key strip at time t: "artist – song", the artist grey, the song white. A line
    that fits on the middle key sits there; a longer one scrolls (spread over the three keys at
    rest, the bezels would hide letters)."""
    k = key / 96
    w = round(3 * key + 2 * MQ_GAP * k)
    img = Image.new("RGB", (w, key))
    pen = ImageDraw.Draw(img)
    font = _font(round(MQ_SIZE * k))
    artist, song = ([part.strip() for part in text.split("\n")] + ["", ""])[:2]
    if not song:
        parts = [("Nothing playing", MQ_DIM)]
    else:
        parts = ([(artist + MQ_SEP, MQ_ARTIST)] if artist else []) + [(song, MQ_SONG)]
    widths = [pen.textlength(part, font=font) for part, _ in parts]
    tw = sum(widths)
    y = round(MQ_BASE * k)
    if tw <= key - 2 * MQ_PAD * k:
        starts = [(w - tw) / 2]
    else:  # one pass is the line's width plus a gap (at least the strip)
        loop = max(tw + MQ_SPACE * k, w)
        moved = max(0.0, t % (MQ_PAUSE + loop / (MQ_SPEED * k)) - MQ_PAUSE) * MQ_SPEED * k
        starts = [MQ_PAD * k - moved, MQ_PAD * k - moved + loop]
    for x in starts:
        for (part, color), pw in zip(parts, widths):
            pen.text((x, y), part, font=font, fill=color, anchor="ls")
            x += pw
    return img


def _marquee_icon(image: Image.Image, n: int, state: str) -> Image.Image:
    """The key's small icon under the line: a speaker with − (bottom left), play or pause (bottom
    middle), a speaker with + (bottom right). Drawn 4x and scaled down for smooth edges."""
    ss, key = 4, image.width
    k = key * ss / 96
    mask = Image.new("L", (key * ss, key * ss))
    pen = ImageDraw.Draw(mask)
    cy = MQ_ICON_Y * k
    if n == 1:
        x = (96 - 10) / 2 * k
        if state == "playing":  # what a tap does: pause
            pen.rectangle([x, cy - 6 * k, x + 3.5 * k, cy + 6 * k], fill=255)
            pen.rectangle([x + 6.5 * k, cy - 6 * k, x + 10 * k, cy + 6 * k], fill=255)
        else:
            pen.polygon([(x, cy - 7 * k), (x, cy + 7 * k), (x + 11 * k, cy)], fill=255)
    else:
        x = 8 * k if n == 0 else (96 - 8 - 22) * k
        pen.rectangle([x, cy - 3 * k, x + 4 * k, cy + 3 * k], fill=255)
        pen.polygon([(x + 4 * k, cy - 3 * k), (x + 10 * k, cy - 7.5 * k), (x + 10 * k, cy + 7.5 * k), (x + 4 * k, cy + 3 * k)], fill=255)
        pen.rectangle([x + 13 * k, cy - 1.2 * k, x + 22 * k, cy + 1.2 * k], fill=255)
        if n == 2:
            pen.rectangle([x + 16.3 * k, cy - 4.5 * k, x + 18.7 * k, cy + 4.5 * k], fill=255)
    image = image.copy()
    image.paste(MQ_ICON, (0, 0), mask.resize(image.size, Image.LANCZOS))
    return image


def _marquee_key(strip: Image.Image, text: str, n: int, key: int) -> Image.Image:
    """Key n's third of the strip, with its icon."""
    x = round(n * (key + MQ_GAP * key / 96))
    state = (text.split("\n") + ["", "", ""])[2].strip()
    return _marquee_icon(strip.crop((x, 0, x + key, key)), n, state)


_library_render = sd.Button.render_icon


def _render_icon(self, complete_state, *, key_pressed=False, size=(sd.ICON_PIXELS, sd.ICON_PIXELS), **kw):
    button = self.rendered_template_button(complete_state)
    if isinstance(button.icon, str) and button.icon.startswith("marquee:"):
        _, n, text = button.icon.split(":", 2)
        strip = _vol_strip(_vol["pct"], size[0]) if vol_showing() else _marquee_strip(text, size[0], time.monotonic() - _t0)
        return _marquee_key(strip, text, int(n), size[0])
    if not (isinstance(button.icon, str) and button.icon.startswith("tile:")):
        return _library_render(self, complete_state, key_pressed=key_pressed, size=size, **kw)
    _, n, url = button.icon.split(":", 2)
    try:
        image = _tile(url, int(n), tuple(size)).copy() if url else Image.new("RGB", size)
    except Exception as e:  # noqa: BLE001
        log(f"cover {url}: {e!r}")
        image = Image.new("RGB", size)
    if vol_showing():
        return _vol_tile(image, int(n))
    if not button.text:
        return image
    return sd._add_text_to_image(image=image, font_filename=kw.get("font_filename", sd.DEFAULT_FONT), text_size=button.text_size,
                                 text=button.text, text_color=button.text_color or "white", text_offset=button.text_offset)


sd.Button.render_icon = _render_icon


# ── volume: the next step, and the level shown across the cover while it changes ──
_vol = {"dir": 0, "at": float("-inf"), "fine": False, "pct": 0, "until": 0.0, "run": 0, "tap": float("-inf")}


def vol_step(pct: int, up: bool, fine: bool, run: int) -> int:
    """How far one tap moves: by the run of quick taps (VOL_RUN), else 1% when fine-tuning, else by
    the level (VOL_TIERS)."""
    for nth, step in VOL_RUN:
        if run >= nth:
            return step
    ref = pct if up else pct - 1
    return 1 if fine else next(s for top, s in VOL_TIERS if ref < top)


def vol_next(pct: int, up: bool, step: int) -> int:
    """The level a step from pct, on a round number (a multiple of the step)."""
    new = (pct // step + 1) * step if up else (-(-pct // step) - 1) * step
    return min(100, max(0, new))


def vol_showing() -> bool:
    return time.monotonic() < _vol["until"]


@functools.lru_cache(maxsize=4)
def _vol_layer(pct: int, fine: bool, key: int) -> Image.Image:
    """The level across the 3x3 (bezels counted in, as the marquee): the number on the middle key,
    clear of the bezels, and a bar across the bottom row."""
    k = key / 96
    row = key + MQ_GAP * k  # from one row's top to the next
    w = round(2 * row + key)
    layer = Image.new("RGBA", (w, w), (0, 0, 0, 150))
    pen = ImageDraw.Draw(layer)
    num, size = str(pct), 84  # as big as fits on the middle key
    while True:
        big, small = _font(round(size * k)), _font(round(size * 0.45 * k))
        tw = pen.textlength(num, font=big) + pen.textlength("%", font=small)
        if tw <= key - 8 * k or size <= 30:
            break
        size -= 2
    x, y = (w - tw) / 2, row + (48 + size * 0.36) * k
    pen.text((x, y), num, font=big, fill="#ffffff", anchor="ls")
    pen.text((x + pen.textlength(num, font=big), y), "%", font=small, fill="#ffffff", anchor="ls")
    bh = 16 * k
    x0, x1, by = 30 * k, w - 30 * k, 2 * row + (key - bh) / 2
    pen.rounded_rectangle([x0, by, x1, by + bh], radius=bh / 2, fill=(255, 255, 255, 60))
    if pct:
        pen.rounded_rectangle([x0, by, x0 + max(bh, (x1 - x0) * pct / 100), by + bh], radius=bh / 2,
                              fill=LIT_RGB if fine else (255, 255, 255, 255))
    return layer


def _vol_tile(image: Image.Image, n: int) -> Image.Image:
    """Tile n of the cover with its part of the level on it."""
    key = image.width
    gap = MQ_GAP * key / 96
    x, y = round(n % 3 * (key + gap)), round(n // 3 * (key + gap))
    part = _vol_layer(_vol["pct"], _vol["fine"], key).crop((x, y, x + key, y + key))
    return Image.alpha_composite(image.convert("RGBA"), part).convert("RGB")


@functools.lru_cache(maxsize=4)
def _vol_strip(pct: int, key: int) -> Image.Image:
    """The scrolling line while the volume changes: the level on the middle key (album pages have no cover)."""
    k = key / 96
    w = round(3 * key + 2 * MQ_GAP * k)
    img = Image.new("RGB", (w, key))
    ImageDraw.Draw(img).text((w / 2, MQ_BASE * k), f"{pct}%", font=_font(round(MQ_SIZE * k)), fill=MQ_SONG, anchor="ms")
    return img

# what the background tasks below draw on: set when the library first draws the deck, and by each connection
live: dict = {"deck": None, "config": None, "state": None}
_library_update_all = sd.update_all_key_images


def _update_all_key_images(deck, config, complete_state):
    live.update(deck=deck, config=config, state=complete_state)
    _library_update_all(deck, config, complete_state)


sd.update_all_key_images = _update_all_key_images


async def animate_marquee() -> None:
    """Redraw the three keys under the cover while their text scrolls (only keys that changed are sent)."""
    sent: dict[int, bytes] = {}
    shown_page = None
    was_showing = False
    while True:
        await asyncio.sleep(1 / MQ_FPS)
        deck, config, state = live["deck"], live["config"], live["state"]
        if deck is None or config is None or not config._is_on:
            continue
        page = config.current_page()
        if page is not shown_page:
            sent.clear()
            shown_page = page
        showing = vol_showing()
        if showing or was_showing:  # the level across the cover: drawn as it changes, and taken off after
            size = deck.key_image_format()["size"]
            for i, b in enumerate(page.buttons):
                if not (isinstance(b.icon, str) and b.icon.startswith("tile:")):
                    continue
                try:
                    image = b.try_render_icon(complete_state=state, size=size)
                except Exception:  # noqa: BLE001
                    continue
                if sent.get(i) != (raw := image.tobytes()):
                    sent[i] = raw
                    with deck:
                        deck.set_key_image(i, PILHelper.to_native_format(deck, image))
        was_showing = showing
        keys = [(i, b) for i, b in enumerate(page.buttons) if isinstance(b.icon, str) and b.icon.startswith("marquee:")]
        if not keys:
            continue
        try:
            text = keys[0][1].rendered_template_button(state).icon.split(":", 2)[2]
        except Exception:  # noqa: BLE001
            continue
        size = deck.key_image_format()["size"]
        strip = _vol_strip(_vol["pct"], size[0]) if showing else _marquee_strip(text, size[0], time.monotonic() - _t0)
        for i, b in keys:
            image = _marquee_key(strip, text, int(b.icon.split(":", 2)[1]), size[0])
            raw = image.tobytes()
            if sent.get(i) == raw:
                continue
            sent[i] = raw
            with deck:
                deck.set_key_image(i, PILHelper.to_native_format(deck, image))


# ── back to Home after a minute without a press ──
_library_reset = sd.reset_inactivity_timer
_idle: asyncio.Task | None = None


def _reset_inactivity_timer(config, deck):
    global _idle
    _library_reset(config, deck)
    if _idle is not None:
        _idle.cancel()
    _idle = asyncio.ensure_future(_go_home(config, deck))


async def _go_home(config, deck) -> None:
    await asyncio.sleep(IDLE_HOME)
    if config._is_on and config.current_page().name != HOME and live["state"] is not None:
        log(f"idle {IDLE_HOME} s: back to {HOME}")
        config.to_page(HOME)
        _library_update_all(deck, config, live["state"])


sd.reset_inactivity_timer = _reset_inactivity_timer


def has_panel(page) -> bool:
    """Home, People and every album page (those keep only the scrolling line, their 3x3 is covers)."""
    return any(isinstance(b.icon, str) and b.icon.startswith(("tile:", "marquee:")) for b in page.buttons)


_library_handle = sd._handle_key_press


_back_to = HOME  # the album page Now Playing's Back returns to


async def _handle_key_press(websocket, complete_state, config, button, deck, *, is_long_press):
    """Pages with the Now Playing panel stay open after an action (the library closes pop-up pages).
    Tapping an album opens Now Playing; its Back (close-page) returns to that album page, which the
    library can't do itself (it goes back to the last named page, Home)."""
    global _back_to
    page = config.current_page()
    special = (button.long_press or {}).get("special_type") if is_long_press else button.special_type
    if not is_long_press and button.service == RANDOM and config._is_on:  # any album on the deck
        uris = sorted({(b.service_data or {}).get("uri", "") for p in config.pages + config.anonymous_pages
                       for b in p.buttons if b.service == PLAY_ALBUM} - {""})
        uris = [u for u in uris if u.startswith("spotify:album:")] or uris
        if uris:
            uri = random.choice(uris)
            log(f"random album: {uri}")
            await sd.call_service(websocket, PLAY_ALBUM, {"uri": uri})
            _back_to = HOME
            config.to_page(NOW_PLAYING)
            sd.update_all_key_images(deck, config, complete_state)
        return
    if special == "close-page" and page.name == NOW_PLAYING and config._is_on:
        config.to_page(_back_to)
        sd.update_all_key_images(deck, config, complete_state)
        return
    await _library_handle(websocket, complete_state, config, button, deck, is_long_press=is_long_press)
    if not is_long_press and button.service == PLAY_ALBUM and config._is_on:
        _back_to = page.name
        config.to_page(NOW_PLAYING)
        sd.update_all_key_images(deck, config, complete_state)
    elif special is None and config._is_on and has_panel(page) and config.current_page() is not page:
        config.to_page(page.name)
        sd.update_all_key_images(deck, config, complete_state)


sd._handle_key_press = _handle_key_press

_library_callback = sd._on_press_callback


_animation: asyncio.Task | None = None


def _on_press_callback(websocket, complete_state, config):
    global _animation
    live.update(config=config, state=complete_state)
    if _animation is None or _animation.done():
        _animation = asyncio.ensure_future(animate_marquee())
    inner = _library_callback(websocket, complete_state, config)
    held: dict[int, tuple[float, dict, asyncio.Future]] = {}
    holding_cover: dict[int, tuple[float, asyncio.Future]] = {}  # cover key -> (pressed at, the 2.5 s prompt)
    armed: dict | None = None    # the cover waiting for its confirming tap
    swallow: set[int] = set()    # keys whose release belongs to a confirm or cancel tap
    ramping: dict[int, asyncio.Task] = {}  # volume key -> its steps while held

    async def ramp(up: bool, player: str) -> None:
        """A tap steps once: a first tap by the level; a slower tap soon after (either way) 1%
        (fine-tuning); quick taps in a row by the level, then 5% from the 3rd, 10% from the 6th.
        Held past VOL_HOLD, it slides to 0 (or 100) over VOL_SWEEP s. Keeps its own level: the
        speaker's state comes back too slowly to add each step to it."""

        async def set_volume(pct: int) -> None:
            _vol.update(pct=pct, at=time.monotonic(), until=time.monotonic() + VOL_SHOW)
            await sd.call_service(websocket, "media_player.volume_set", {"entity_id": player, "volume_level": pct / 100})

        now = time.monotonic()
        recent = now - _vol["at"] < VOL_REVERSE
        same = _vol["dir"] == (1 if up else -1)
        run = _vol["run"] + 1 if same and now - _vol["tap"] < VOL_TAP_GAP else 1
        fine = recent and run == 1  # tapping slowly, or back the other way: fine adjustments
        if recent:
            pct = _vol["pct"]
        else:
            attrs = (complete_state.get(player) or {}).get("attributes") or {}
            pct = round(float(attrs.get("volume_level") or 0) * 100)
        _vol.update(dir=1 if up else -1, fine=fine, run=run, tap=now)
        await set_volume(vol_next(pct, up, vol_step(pct, up, fine, run)))
        await asyncio.sleep(VOL_HOLD)
        # still held: a steady slide to the end, VOL_SWEEP s from wherever it is
        _vol["run"] = 0
        start, end, t0 = _vol["pct"], 100 if up else 0, time.monotonic()
        while _vol["pct"] != end:
            await asyncio.sleep(VOL_TICK)
            pct = round(start + (end - start) * min(1.0, (time.monotonic() - t0) / VOL_SWEEP))
            if pct != _vol["pct"]:
                await set_volume(pct)
            else:
                _vol["until"] = time.monotonic() + VOL_SHOW

    async def notify(message: str) -> None:
        await sd.call_service(websocket, "persistent_notification.create",
                              {"notification_id": "deck_listen", "title": "Stream Deck", "message": message})

    async def flash(deck, key: int, text: str, seconds: float = 3) -> None:
        draw(deck, config, key, complete_state, text)
        await asyncio.sleep(seconds)
        draw(deck, config, key, complete_state, None)

    async def released(deck, key: int, data: dict, seconds: float, started: asyncio.Future) -> None:
        if not (await started).get("ok"):
            return  # the press already said why
        if seconds < MIN_HOLD:
            await post("/api/voice_cancel", {})
            draw(deck, config, key, complete_state, None)
            return
        draw(deck, config, key, complete_state, "Adding…")
        reply = await post("/api/voice_stop", {"folder": data.get("folder", "")}, timeout=90)
        log(f"key {key}: {reply.get('speech')}")
        if reply.get("added"):
            draw(deck, config, key, complete_state, None)  # the cover arrives with the config reload
            await notify(reply["speech"])
        elif reply.get("heard") or reply.get("error"):
            await notify(reply["speech"])
            await flash(deck, key, "Not\nadded")
        else:  # nothing said: wait for something to start on Spotify instead
            await sd.call_service(websocket, LISTEN_SVC, {**data, "voice": False})
            await flash(deck, key, "Start it\non Spotify", 5)

    # ── deleting a cover: hold 2.5 s, let go, tap again ──
    def disarm() -> dict | None:
        nonlocal armed
        was, armed = armed, None
        if was:
            was["timer"].cancel()
            draw(was["deck"], config, was["key"], complete_state, None)
        return was

    async def prompt(deck, key: int) -> None:
        """While a cover is held: a red circle in its middle fills in like a clock, then it asks for the tap."""
        start = time.monotonic()
        button = config.button(key)
        w, h = size = deck.key_image_format()["size"]
        base = button.try_render_icon(complete_state=complete_state, size=size).convert("RGBA")
        ss = 4  # drawn 4x larger and scaled down, so the circle's edge is smooth
        r = min(w, h) * 0.23 * ss
        cx, cy = w * ss / 2, h * ss / 2
        disc = [cx - r, cy - r, cx + r, cy + r]
        while (done := (time.monotonic() - start) / HOLD_DELETE) < 1:
            layer = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
            pen = ImageDraw.Draw(layer)
            pen.ellipse([cx - r - 3 * ss, cy - r - 3 * ss, cx + r + 3 * ss, cy + r + 3 * ss], fill=(0, 0, 0, 150))  # lifts it off the cover
            pen.ellipse(disc, fill=(40, 12, 10, 255), outline=RED, width=2 * ss)  # the empty circle
            if done > 0:
                pen.pieslice(disc, -90, -90 + 360 * done, fill=RED)
            frame = Image.alpha_composite(base, layer.resize(size, Image.LANCZOS)).convert("RGB")
            with deck:
                deck.set_key_image(key, PILHelper.to_native_format(deck, frame))
            await asyncio.sleep(0.08)
        draw(deck, config, key, complete_state, "Let go,\nthen tap\nto delete", RED)

    async def expire(me: dict) -> None:
        await asyncio.sleep(CONFIRM_WINDOW)
        if armed is me:
            disarm()

    def arm(deck, key: int, data: dict) -> None:
        nonlocal armed
        disarm()
        armed = {"deck": deck, "key": key, "page": config.current_page().name, "data": data}
        armed["timer"] = asyncio.ensure_future(expire(armed))
        draw(deck, config, key, complete_state, "Tap again\nto delete", RED)

    async def delete(deck, key: int, data: dict) -> None:
        draw(deck, config, key, complete_state, "Deleting…", RED)
        reply = await post("/api/remove_id", {"folder": data.get("folder", ""), "id": data.get("id", "")}, timeout=45)
        log(f"key {key}: {reply.get('speech')}")
        await notify(reply.get("speech", "Couldn't delete it."))
        draw(deck, config, key, complete_state, None)  # the reloaded configuration drops the cover

    async def key_change_callback(deck, key: int, key_pressed: bool) -> None:  # noqa: FBT001
        button = config.button(key)
        lp = button.long_press if button is not None else None
        if not key_pressed and key in swallow:
            swallow.discard(key)
            return
        if key_pressed and armed is not None:  # the confirming tap, or any other key to cancel
            was = disarm()
            swallow.add(key)
            if was["key"] == key and was["page"] == config.current_page().name:
                asyncio.ensure_future(delete(deck, key, was["data"]))
            return
        if config._is_on and button is not None and button.service == VOLUME_SVC:
            sd.reset_inactivity_timer(config, deck)
            if (task := ramping.pop(key, None)) is not None:
                task.cancel()
            if key_pressed:
                data = button.service_data or {}
                ramping[key] = asyncio.ensure_future(ramp(int(data.get("direction", 1)) > 0, data.get("entity_id", "")))
            else:
                _vol["at"] = time.monotonic()  # the reversal window counts from letting go
            return
        if config._is_on and lp and lp.get("service") == REMOVE_SVC:
            sd.reset_inactivity_timer(config, deck)
            if key_pressed:
                sd.update_key_image(deck, key=key, config=config, complete_state=complete_state, key_pressed=True)
                holding_cover[key] = (time.monotonic(), asyncio.ensure_future(prompt(deck, key)))
                return
            if key not in holding_cover:
                return
            t0, waiting = holding_cover.pop(key)
            waiting.cancel()
            if time.monotonic() - t0 >= HOLD_DELETE:
                arm(deck, key, dict(lp.get("service_data") or {}))
            else:  # an ordinary press: play it
                sd.update_key_image(deck, key=key, config=config, complete_state=complete_state)
                await sd._try_handle_key_press(websocket, complete_state, config, button, deck, is_long_press=False)
            return
        if not (config._is_on and lp and lp.get("service") == LISTEN_SVC):
            await inner(deck, key, key_pressed)
            return
        sd.reset_inactivity_timer(config, deck)
        data = dict(lp.get("service_data") or {})
        if key_pressed:
            draw(deck, config, key, complete_state, "Listening…")
            started = asyncio.ensure_future(post("/api/voice_start", {"folder": data.get("folder", ""),
                                                                        "slot": data.get("slot")}))
            held[key] = (time.monotonic(), data, started)
            reply = await started
            if not reply.get("ok"):
                await notify(reply.get("speech", "Couldn't start recording."))
                await flash(deck, key, "No mic")
            return
        if key in held:  # the release may arrive while the start is still on its way; released() waits for it
            t0, data, started = held.pop(key)
            asyncio.ensure_future(released(deck, key, data, time.monotonic() - t0, started))

    return key_change_callback


sd._on_press_callback = _on_press_callback

if __name__ == "__main__":
    sd.main()
