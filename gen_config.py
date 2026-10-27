#!/usr/bin/env python3
"""Generate configuration.yaml: a white-on-black Stream Deck for Home Assistant, genres -> album covers.

Two deck sizes (`deck` in settings.yaml): the Stream Deck XL (8x4 keys of 96 px) and the 15-key
MK.2/original (5x3 keys of 72 px). On the XL, Home and People keep a Now Playing panel on their
right: the speaker's cover across 3x3 keys and under it three keys that scroll "artist – song"
(drawn by visualizer/deck_run.py), which are also Vol –, play/pause and Vol +. Album pages fill the
deck with covers, with the scrolling line under them. The 15-key deck has the same pages, smaller:
the cover shows on Now Playing, the scrolling line under Home.

Everything about one house (speaker, TV, people, genres) is in settings.yaml (settings.example.yaml
documents it). Albums live in albums/<genre>.json (verified Spotify IDs); each person's folder in
people/<name>.json (managed by talking to Home Assistant Assist, see manager/). The autobuild
watcher re-runs this, or run:  python3 gen_config.py
It also rebuilds emulator/streamdeck-emulator-local.html.
"""
import csv
import json
import re
import textwrap
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SETTINGS_FILE = HERE / "settings.yaml" if (HERE / "settings.yaml").exists() else HERE / "settings.example.yaml"
S = yaml.safe_load(SETTINGS_FILE.read_text()) or {}

# deck: (columns, rows, key image px). The library numbers keys left to right, top to bottom
DECKS = {"xl": (8, 4, 96), "mk2": (5, 3, 72)}
DECK = S.get("deck", "xl")
COLS, ROWS, KEY_PX = DECKS[DECK]
KEYS = COLS * ROWS
PANEL = COLS >= 8  # room for the Now Playing panel (3x3 cover + scrolling line) beside other keys
LEFT = COLS - 3 if PANEL else COLS  # columns left of the panel
# text sizes below are in a 72 px key's pixels, scaled to the deck's keys (the emulator divides by KEY_PX)
SCALE = KEY_PX / 72

PLAYER = S["player"]  # the speaker albums play on
GENRES = [tuple(g) for g in S.get("genres", [])]  # (file, label on the key); albums/<file>.json, in Home's order
PEOPLE = [(p[0], p[1]) for p in S.get("people", [])]  # (file in people/, name on the key)
# other names Assist may hear for a person -> their file (manager/deck_manager.py, tools/install_ha.py)
NICKNAMES = {n.lower(): p[0] for p in S.get("people", []) if len(p) > 2 for n in p[2]}
PERSON_GENRE = S.get("person_genre") or {}  # genres that live on a person's page (bottom-right key) instead of Home
TOP, TOP_NAME = S.get("top") or (None, None)  # a ranked list with its own Home key, flipped with ‹ ›
SOURCES = [tuple(x) for x in S.get("sources", [])]  # (source name in Home Assistant, key label): More › Amp
TV = S.get("tv")  # the TV (LG webOS); Wake-on-LAN and Jellyfin in homeassistant/deck_tv.yaml
TV_LABEL = S.get("tv_label", "TV")
FILMS = [tuple(f) for f in S.get("films", [])]  # (name in Jellyfin, label): More › TV, each turns the TV on and plays it
LAN_PROBE = S.get("lan_probe_ip", "192.168.1.1")  # only used to find this computer's LAN address
SFX_WWW = Path(S.get("ha_config", "~/homeassistant/config")).expanduser() / "www/sfx"  # served as /local/sfx/

RANDOM = "script.deck_random_album"  # More › Random: visualizer/deck_run.py picks one of the deck's albums and plays it
NOW_PLAYING = "Now Playing"  # tapping an album opens it: the cover big, Back returns to the album page (visualizer/deck_run.py)
IDLE_HOME = 60     # seconds without a press before the deck goes back to Home (visualizer/deck_run.py, emulator)
SLEEP = "timer.deck_sleep"  # homeassistant/deck_sleep.yaml: when it runs out, everything turns off
SLEEP_TIMES = [("00:05:00", "5 min"), ("00:10:00", "10 min"), ("00:15:00", "15 min"),
               ("00:30:00", "30 min"), ("01:00:00", "1 hour")]
VISUALIZER = "input_boolean.deck_visualizer"  # created by visualizer/supervisor.py on first run
LISTEN = "input_text.deck_listen"  # homeassistant/deck_intercom.yaml; holds the Home key label while listening
LISTEN_SECONDS = 60  # how long a held blank key waits for you to start something on Spotify
LIGHTS_OUT = "input_text.deck_lightsout"  # the Lights Out board (More › Lights Out); homeassistant/deck_games.yaml
# lit-key colours; each new game takes the next one (its number is in the board). Mirrored in emulator/template.html
LO_COLOURS = ["#f5b301", "#1ec8e6", "#e8459a", "#3ddc6e", "#9b6cff", "#ff7a1a"]
LIT = "#f5b301"
KARAOKE = HERE / "karaoke/songs.csv"  # More › Karaoke: Rank,Song,Artist,Category,Karaoke Video (a YouTube link)
SOUNDS = HERE / "sounds"  # drop .mp3/.wav files here: each is a key on More › Sound Effects
SFX_TYPES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

WHITE, DIM = "#ffffff", "#5a5a5a"
EMPTY = {"special_type": "empty"}
TAKE = {"service": "script.deck_listen_take"}  # holding the Home key that shows "Listening…": take what's on now


def key(text, size=12, color=WHITE, **kw):
    size = round(size * SCALE)
    if kw.get("special_type") == "go-to-page" and "icon" not in kw and "icon_mdi" not in kw:
        # the library draws a book icon behind go-to-page text when icon_mdi is unset; black on black hides it
        kw["icon_mdi_color"] = "#000000"
    return {"text": text, "text_size": size, "text_color": color, **kw}


def back(to=None):
    """Back: a white arrow, the same on every page. Closes a pop-up page, or with `to` goes to that page."""
    nav = {"special_type": "go-to-page", "special_type_data": to} if to else {"special_type": "close-page"}
    return key("", 9, DIM, icon_mdi="arrow-left", icon_mdi_color=WHITE, **nav)


def page_arrow(names, i, left):
    """‹ or › between a list's pages, with the number of the page it goes to under the chevron."""
    i %= len(names)
    return key(f"{i + 1} / {len(names)}", 9, DIM, special_type="go-to-page", special_type_data=names[i],
               icon_mdi="chevron-left" if left else "chevron-right", icon_mdi_color=WHITE, text_offset=36)


def goto(page, text, size=13):
    return key(text, size, special_type="go-to-page", special_type_data=page)


def player(service, text, **kw):
    return key(text, 11, entity_id=PLAYER, service=service, **kw)


def listening(label, text=None):
    """Text for the Home key `label` that reads "Listening…" while one of its folders is listening."""
    return f"{{{{ 'Listening…' if is_state('{LISTEN}', '{label}') else '{label if text is None else text}' }}}}"


def blank(folder, home_key, slot):
    """An empty cover slot: hold it and say what to add, or start it on Spotify (manager/deck_manager.py).
    What you add lands on this key: `slot` is its position in the folder (see place())."""
    return key("", 9, DIM, long_press={"service": "script.deck_listen",
                                        "service_data": {"folder": folder, "home_key": home_key, "slot": slot}})


def place(items):
    """Which key each item of a folder sits on: {slot: item}, slot 0 = the first cover key.

    An item keeps its "slot" (the key it was added on) while that key is free; items without
    one, or whose key is taken, fill the first free keys in list order. The deck manager uses
    this too, so a folder looks the same on the deck as when it was edited.
    """
    placed, loose = {}, []
    for it in items:
        s = it.get("slot")
        if isinstance(s, int) and s >= 0 and s not in placed:
            placed[s] = it
        else:
            loose.append(it)
    free = 0
    for it in loose:
        while free in placed:
            free += 1
        placed[free] = it
    return placed


def remove(folder, item):
    """Long press on a cover you added: held 2.5 s, then a confirming tap, deletes it (visualizer/deck_run.py)."""
    return {"service": "script.deck_remove", "service_data": {"folder": folder, "id": item["id"], "title": item["title"]}}


def sleeping(otherwise):
    """Text colour: amber while the sleep timer is counting down."""
    return f"{{{{ '{LIT}' if is_state('{SLEEP}', 'active') else '{otherwise}' }}}}"


def page(name, rows):
    """A page from up to four rows of keys, each padded with empty keys to the deck's width."""
    assert len(rows) <= ROWS and all(len(r) <= COLS for r in rows), name
    slots = [k for r in rows for k in r + [EMPTY] * (COLS - len(r))]
    return {"name": name, "buttons": slots + [EMPTY] * (KEYS - len(slots))}


def tile(n):
    """A ninth of the WiiM's cover ("tile:<n>:<url>", cut by visualizer/deck_run.py); tap it to pause."""
    return key(f"{{{{ '' if is_state('{PLAYER}', 'playing') else 'Paused' }}}}" if n == 4 else "", 14,
               icon=f"tile:{n}:{{{{ state_attr('{PLAYER}', 'entity_picture') or '' }}}}",
               entity_id=PLAYER, service="media_player.media_play_pause")


def marquee(n):
    """Key n of the three under the cover: "artist – song" on one large line, scrolling slowly
    ("marquee:<n>:<artist>\\n<song>\\n<state>", drawn by visualizer/deck_run.py and emulator/template.html).
    Under the line each key has a small icon for what it does: Vol −, play/pause, Vol +."""
    icon = (f"marquee:{n}:{{{{ state_attr('{PLAYER}', 'media_artist') or '' }}}}\n"
            f"{{{{ state_attr('{PLAYER}', 'media_title') or '' }}}}\n{{{{ states('{PLAYER}') }}}}")
    if n == 1:
        return {"icon": icon, "entity_id": PLAYER, "service": "media_player.media_play_pause"}
    return {"icon": icon, "service": VOLUME, "service_data": {"direction": 1 if n == 2 else -1, "entity_id": PLAYER}}


def panel_page(name, rows):
    """A page with the Now Playing panel on the right; `rows` fill the LEFT columns. Without room
    for the panel (the 15-key deck) it's a plain page of `rows`.

    Pages with the panel stay open after a key's action (visualizer/deck_run.py), so you can
    play albums and change the volume without leaving them."""
    if not PANEL:
        return page(name, rows)
    assert len(rows) <= ROWS and all(len(r) <= LEFT for r in rows), name
    rows = [list(r) + [EMPTY] * (LEFT - len(r)) for r in rows] + [[EMPTY] * LEFT] * (ROWS - len(rows))
    side = [[tile(3 * r + c) for c in range(3)] for r in range(3)] + [[marquee(c) for c in range(3)]]
    return page(name, [r + s for r, s in zip(rows, side)])


def volume(word):
    """A volume key's text: the word, and the WiiM's volume under it."""
    return f"{word}\n{{{{ ((state_attr('{PLAYER}', 'volume_level') or 0) * 100) | round | int }}}}%"


def vol_key(up):
    """Vol + or Vol – as a key of its own (the 15-key deck's album pages have no room for the
    scrolling line): steps like the keys under it, repeating while held (visualizer/deck_run.py)."""
    # an en dash, not a minus sign: Chicago (fonts/ChicagoFLF.ttf) has no minus
    return key(volume("Vol +" if up else "Vol –"), 11, service=VOLUME,
               service_data={"direction": 1 if up else -1, "entity_id": PLAYER})


# homeassistant/deck_volume.yaml: one step, 1% when quiet and more as it gets louder; visualizer/deck_run.py
# (and the emulator) repeat it while held, and turn to 1% steps when you change direction
VOLUME = "script.deck_volume"



PER_PAGE = (ROWS - 1) * COLS  # albums a page: all rows but the last (24 on the XL, 10 on the 15-key deck)
JUMP = 5       # holding ‹ or › skips this many pages, in folders long enough to need it


def album_pages(name, albums, folder, home_key, empty_hint=None, room=False, extra=None, listen=True):
    """A genre's albums, 24 a page across the whole deck; Back, Vol − +, ‹ › and the scrolling
    "artist – song" line below them.

    Pages are named "Jazz", "Jazz 2", "Jazz 3"…; the arrows wrap around at the ends.
    Empty slots listen when held (listen=False: they stay empty). room=True adds a fresh page when the last one is full,
    so there is always a blank key to hold. `extra` is a key for the bottom-right slot;
    once the folder needs arrows it takes each page's last cover slot instead.
    """
    placed = place(albums)
    used = max(placed) + 1 if placed else 0
    per = PER_PAGE - 1 if extra and used >= PER_PAGE else PER_PAGE
    count = max(1, -(-used // per))
    if room and len(placed) >= count * per:  # every key taken: a fresh page, so there's a blank to hold
        count += 1
    names = [name if n == 0 else f"{name} {n + 1}" for n in range(count)]

    def cover(slot):
        a = placed.get(slot)
        if a is None:
            return blank(folder, home_key, slot) if listen else EMPTY
        return ({"icon": f"spotify:{a.get('kind', 'album')}/{a['id']}", "service": "script.play_spotify_album",
                 "service_data": {"uri": f"spotify:{a.get('kind', 'album')}:{a['id']}"}}
                | ({"long_press": remove(folder, a)} if folder.startswith("people/") else {}))

    pages = []
    for n in range(count):
        slots = range(n * per, (n + 1) * per)
        covers = [cover(s) for s in slots]
        if empty_hint and not any(s in placed for s in slots):
            covers[0] = covers[0] | {"text": empty_hint}
        if extra and per < PER_PAGE:
            covers.append(extra)
        if count > 1:
            prev, nxt = page_arrow(names, n - 1, True), page_arrow(names, n + 1, False)
            if count > 2 * JUMP:  # hold to skip ahead or back
                prev |= {"long_press": {"special_type": "go-to-page", "special_type_data": names[(n - JUMP) % count]}}
                nxt |= {"long_press": {"special_type": "go-to-page", "special_type_data": names[(n + JUMP) % count]}}
        else:
            prev, nxt = EMPTY, extra or EMPTY
        if PANEL:  # volume is the two keys under the scrolling line
            bottom = [back(), EMPTY, EMPTY, prev, nxt] + [marquee(c) for c in range(3)]
        else:
            bottom = [back(), vol_key(False), vol_key(True), prev, nxt]
        pages.append(page(names[n], [covers[r * COLS:(r + 1) * COLS] for r in range(ROWS - 1)] + [bottom]))
    return pages


def lan_ip():
    """This computer's LAN address: speakers fetch the sound effect clips from Home Assistant here."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((LAN_PROBE, 80))  # UDP: no packet is sent, it only picks the route
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def sync_sounds():
    """Copy sounds/ into Home Assistant's www/sfx/ under URL-safe names; returns [(label, file name)]."""
    import re
    import shutil
    clips = []  # (label, served name, source file)
    for f in sorted(SOUNDS.glob("*")) if SOUNDS.is_dir() else []:
        if f.suffix.lower() in SFX_TYPES:
            label = re.sub(r"[-_]+", " ", f.stem).strip()
            name = re.sub(r"[^a-z0-9]+", "-", f.stem.lower()).strip("-") + f.suffix.lower()
            clips.append((label[:1].upper() + label[1:], name, f))
    if SFX_WWW.parent.is_dir():
        SFX_WWW.mkdir(exist_ok=True)
        for _, name, src in clips:
            dst = SFX_WWW / name
            if not dst.exists() or dst.read_bytes() != src.read_bytes():
                shutil.copyfile(src, dst)
        for old in SFX_WWW.iterdir():
            if old.name not in {name for _, name, _ in clips}:
                old.unlink()
    return [(label, name) for label, name, _ in clips]


def sound_pages(sounds):
    """More › Sound Effects: a key per clip in sounds/, played over the music (homeassistant/deck_sfx.yaml).

    29 per page with Back bottom-left and ‹ › bottom-right, like the album folders. These are named
    pages, not anonymous ones, so the page stays open while you play sounds; Back goes to Home by
    name (not close-page), which also resets the deck's current page, as Lights Out does.
    """
    base = f"http://{lan_ip()}:8123/local/sfx/"

    def label(text):
        return text.replace(" ", "\n") if len(text) > 8 else text

    keys = [key(label(t), 11, service="script.deck_sfx", service_data={"url": base + name}) for t, name in sounds]
    if not keys:
        keys = [key("No sounds\n\nAdd files to\nsounds/", 9, DIM)]
    return list_pages("Sound Effects", keys)


def list_pages(name, keys, extra=None):
    """Keys to a page with Back (to Home) bottom-left and ‹ › bottom-right: "Name", "Name 2"…
    (29 a page on the XL, 12 on the 15-key deck).

    `extra` is a key repeated on every page, right of Back; the pages then hold one fewer."""
    per = KEYS - 3 - (1 if extra else 0)
    count = -(-len(keys) // per)
    names = [name if n == 0 else f"{name} {n + 1}" for n in range(count)]
    pages = []
    for n in range(count):
        ks = keys[n * per:(n + 1) * per]
        ks += [EMPTY] * (per - len(ks))
        arrows = [page_arrow(names, n - 1, True), page_arrow(names, n + 1, False)] if count > 1 else [EMPTY, EMPTY]
        full = (ROWS - 1) * COLS
        bottom = [back("Home")] + ([extra] if extra else []) + ks[full:] + arrows
        pages.append(page(names[n], [ks[r * COLS:(r + 1) * COLS] for r in range(ROWS - 1)] + [bottom]))
    return pages


def karaoke_pages():
    """More › Karaoke: a key per song in karaoke/songs.csv, in rank order; each plays its YouTube
    karaoke video on the TV (script.tv_youtube_play, homeassistant/deck_tv.yaml), stopping the one
    playing. Stop, right of Back, closes YouTube. Named pages like Sound Effects, so the list stays
    open between songs."""
    songs = list(csv.DictReader(KARAOKE.open(encoding="utf-8"))) if KARAOKE.exists() else []
    keys = []
    for s in songs:
        video = re.search(r"[?&]v=([\w-]{11})", s["Karaoke Video"])
        assert video, f"karaoke/songs.csv: no YouTube video in {s['Karaoke Video']!r} ({s['Song']})"
        # title, then artist; the deck draws only the line breaks we give it
        text = "\n".join(textwrap.wrap(s["Song"], 11) + [""] + textwrap.wrap(s["Artist"], 11))
        keys.append(key(text, 8, service="script.tv_youtube_play",
                        service_data={"video": video.group(1), "title": f"{s['Song']} – {s['Artist']}"}))
    stop = key("Stop", 12, service="script.tv_youtube_stop")
    return list_pages("Karaoke", keys or [key("No songs\n\nAdd them to\nkaraoke/songs.csv", 9, DIM)], stop)


def lights_out_page():
    """Lights Out: every key is the board (homeassistant/deck_games.yaml plays it); hold any key to leave.

    The "{# deck-game … #}" comments tell the emulator which cell a template draws; it plays the game itself.
    Change the messages here and in lightsOutText() in emulator/template.html together.
    """
    board = LIGHTS_OUT
    # the messages sit on the second row (five keys, centred) and the middle of the bottom row
    mid, foot = COLS + (COLS - 4) // 2, (ROWS - 1) * COLS + (COLS - 1) // 2
    moves = mid + 3
    won = {mid: "You", mid + 1: "won", mid + 2: "in", moves: "{{ s.split(':')[1] }}", mid + 4: "moves",
           foot: "Tap to\nplay again"}
    start = {mid + 1: "Tap to\nplay", foot: "Hold any\nkey to\nleave"}

    def cell(i):
        msg = (f"{{%- set s = (states('{board}') or '') ~ '' -%}}{{# deck-game lightsout {i} text #}}"
               f"{{%- if '#' not in s.split(':')[0] -%}}"
               f"{{%- if s.startswith('{'.' * KEYS}:') -%}}{won.get(i, '')}{{%- else -%}}{start.get(i, '')}{{%- endif -%}}"
               f"{{%- endif -%}}")
        return key(msg, 14 if i == moves else 11, linked_entity=board,
                   icon_background_color=f"{{# deck-game lightsout {i} bg #}}"
                                         f"{{%- set s = ((states('{board}') or '') ~ '').split(':') -%}}"
                                         f"{{{{ {LO_COLOURS}[((s[2] if s | length > 2 else 0) | int(0)) % {len(LO_COLOURS)}] "
                                         f"if s[0][{i}:{i + 1}] == '#' else '#000000' }}}}",
                   service="script.deck_lightsout_press", service_data={"board": board, "cell": i, "cols": COLS, "cells": KEYS},
                   long_press={"special_type": "go-to-page", "special_type_data": "Home"})

    cells = [cell(i) for i in range(KEYS)]
    return page("Lights Out", [cells[r * COLS:(r + 1) * COLS] for r in range(ROWS)])


def main():
    folders = {p: json.loads((HERE / "people" / f"{p}.json").read_text())
               if (HERE / "people" / f"{p}.json").exists() else [] for p, _ in PEOPLE}
    loaded = {g: json.loads((HERE / "albums" / f"{g}.json").read_text())
              for g, _ in GENRES if (HERE / "albums" / f"{g}.json").exists()}
    play_text = f"{{{{ 'Pause' if is_state('{PLAYER}', 'playing') else 'Play' }}}}"

    def genre_key(g, label, on_home=True):
        if g not in loaded:
            return key(label, 13, DIM)
        size = 13 if max(len(line) for line in label.split("\n")) <= 9 else 11  # "Electronic" fits on one line
        k = goto(label.replace("\n", " "), listening(label.replace("\n", " "), label) if on_home else label, size)
        return k | ({"long_press": TAKE} if on_home else {})

    shared = [(g, label) for g, label in GENRES if g not in PERSON_GENRE.values()]  # Home, then More
    label_of = dict(GENRES)
    top_file = HERE / "albums" / f"{TOP}.json"
    top = json.loads(top_file.read_text()) if TOP and top_file.exists() else []
    # the ranked list's key ends Home's top row; its label wraps at the space
    top_key = [goto(TOP_NAME, TOP_NAME.replace(" ", "\n", 1), 12)] if top else []
    on_home = (ROWS - 1) * LEFT - len(top_key)  # genres on Home (all rows but the last); the rest go under More

    def home_key_for(g):
        if g in PERSON_GENRE.values():
            return "People"
        return [label for gg, label in shared[:on_home] if gg == g][0].replace("\n", " ") \
            if g in [gg for gg, _ in shared[:on_home]] else "More"

    def person_extra(slug):  # a person's bottom-right key: their genre, if they have one
        if slug in PERSON_GENRE:
            return genre_key(PERSON_GENRE[slug], label_of[PERSON_GENRE[slug]], on_home=False)
        return None

    # opens the Sleep page; amber "Sleep on" while the timer runs. On More
    sleep_key = key(f"{{{{ 'Sleep on' if is_state('{SLEEP}', 'active') else 'Sleep' }}}}", 12, sleeping(WHITE),
                    special_type="go-to-page", special_type_data="Sleep", linked_entity=SLEEP)
    mute = player("media_player.volume_mute",
                  f"{{{{ 'Muted' if state_attr('{PLAYER}', 'is_volume_muted') else 'Mute' }}}}",
                  service_data={"entity_id": PLAYER,
                                "is_volume_muted": f"{{{{ not state_attr('{PLAYER}', 'is_volume_muted') }}}}"})
    prev, nxt = player("media_player.media_previous_track", "Prev"), player("media_player.media_next_track", "Next")
    play = player("media_player.media_play_pause", play_text)
    # People is a white three-person icon; the text only appears while one of its folders listens
    people_key = (goto("People", listening("People", "")) | {"icon_mdi": "account-group", "icon_mdi_color": WHITE,
                                                              "long_press": TAKE}) if PEOPLE else EMPTY
    more_key = goto("More", listening("More"), 11) | {"long_press": TAKE}
    homes = [genre_key(g, label) for g, label in shared[:on_home]]
    homes = homes[:LEFT - len(top_key)] + top_key + homes[LEFT - len(top_key):]
    if PANEL:
        # left of the Now Playing panel: genres; People (bottom left), More, Prev, Next and Mute.
        # Play/pause and volume are the three keys under the cover
        home = panel_page("Home", [homes[r * LEFT:(r + 1) * LEFT] for r in range(ROWS - 1)]
                          + [[people_key, more_key, prev, nxt, mute]])
        # an album tapped: its cover big on the right; Back goes to the album page it came from
        now_playing = panel_page(NOW_PLAYING, [[back()], [], [], [prev, play, nxt, mute]])
    else:
        # genres, then People, More and the scrolling line (Vol –, play/pause, Vol +)
        home = page("Home", [homes[r * COLS:(r + 1) * COLS] for r in range(ROWS - 1)]
                    + [[people_key, more_key] + [marquee(c) for c in range(3)]])
        # the cover on the right three columns; Back, the player and volume beside it
        tiles = [[tile(3 * r + c) for c in range(3)] for r in range(3)]
        now_playing = page(NOW_PLAYING, [[back(), prev] + tiles[0], [play, nxt] + tiles[1],
                                         [vol_key(False), vol_key(True)] + tiles[2]])

    def menu(name, keys, last=()):
        """A page of keys flowing across the rows, Back bottom-left and `last` bottom-right."""
        room = (ROWS - 1) * COLS + COLS - 1 - len(last)
        keys = list(keys)[:room]
        rows = [keys[r * COLS:(r + 1) * COLS] for r in range(ROWS - 1)]
        tail = keys[(ROWS - 1) * COLS:]
        return page(name, rows + [[back()] + tail + [EMPTY] * (COLS - 1 - len(tail) - len(last)) + list(last)])

    # the rarely used keys: the amp, Sleep, the visualizer, the TV; Karaoke, Sound Effects, Lights Out
    # and Random (any album on the deck); then any genres that don't fit on Home
    tv_keys = [goto("TV", TV_LABEL, 12), goto("Karaoke", "Karaoke", 11)] if TV else []
    more = menu("More", [goto("Amp", "Amp", 12), sleep_key,
                         key("Visualizer", 10, service="input_boolean.turn_on", service_data={"entity_id": VISUALIZER}),
                         *tv_keys[:1], *tv_keys[1:], goto("Sound Effects", "Sound\nEffects", 11),
                         key("", special_type="go-to-page", special_type_data="Lights Out", icon="/icons/game-lights-out.png"),
                         key("Random", 12, service=RANDOM)]
                + [EMPTY] * (-(len(tv_keys) + 6) % COLS)  # genres start on a row of their own
                + [genre_key(g, label, on_home=False) for g, label in shared[on_home:]])
    # the speaker: its inputs, and its power
    amp = menu("Amp", [player("media_player.select_source", t, service_data={"entity_id": PLAYER, "source": s})
                       for s, t in SOURCES] + [player("media_player.toggle", "Power")])
    # pick how long until everything turns off; starting again replaces the old countdown
    sleep = menu("Sleep", [key(t, 13, service="timer.start", service_data={"entity_id": SLEEP, "duration": d})
                           for d, t in SLEEP_TIMES],
                 last=[key("Cancel\nsleep", 11, sleeping(DIM), service="timer.cancel", service_data={"entity_id": SLEEP},
                           linked_entity=SLEEP)])
    names = [goto(n, n, 13 if len(n) <= 8 else 11) for _, n in PEOPLE]
    people = (panel_page("People", [names[r * LEFT:(r + 1) * LEFT] for r in range(ROWS - 1)] + [[back()]])
              if PANEL else menu("People", names))
    # the TV: power, and films from Jellyfin (homeassistant/deck_tv.yaml)
    tv = menu("TV", ([key("Power", 12, f"{{{{ '{LIT}' if is_state('{TV}', 'on') else '{WHITE}' }}}}",
                          service="media_player.toggle", service_data={"entity_id": TV}, linked_entity=TV)] if TV else [])
              + [key(label, 11, service="script.tv_jellyfin_play", service_data={"title": name}) for name, label in FILMS])

    cfg = {
        "brightness": 80,
        "auto_reload": True,
        "long_press_duration": 0.5,
        "pages": [home, lights_out_page()]
        + sound_pages(sync_sounds()) + (karaoke_pages() if TV else []),
        "anonymous_pages": [more, amp, sleep, people, tv, now_playing]
        + [p for slug, n in PEOPLE for p in album_pages(n, folders[slug], f"people/{slug}", "People",
                                                        "Empty\n\nHold and say\nwhat to add", room=True,
                                                        extra=person_extra(slug))]
        + (album_pages(TOP_NAME, top, f"albums/{TOP}", TOP_NAME, listen=False) if top else [])
        + [p for g, label in GENRES if g in loaded
           for p in album_pages(label.replace("\n", " "), loaded[g], f"albums/{g}", home_key_for(g))],
    }
    header = (
        "# GENERATED by gen_config.py. Edit albums/<genre>.json, people/<name>.json or gen_config.py, then re-run it.\n"
        "# Minimal deck for a Stream Deck XL (8x4): white text on black. Home and People have the\n"
        "# Now Playing panel on the right: the cover across 3x3 keys, 'artist – song' scrolling on the 3 keys below.\n"
        "# Each genre or person = pages of 24 covers across the deck, with Back, Vol − +, ‹ › (hold to skip 5 pages)\n"
        "# and the scrolling line below.\n"
        "# After 60 s without a press the deck goes back to Home (visualizer/deck_run.py).\n"
        "# Person folders (people/*.json) are edited through Home Assistant Assist: 'add Kind of Blue to Alex'.\n"
        "# Holding a blank cover key starts listen mode: whatever you start next on Spotify is added there.\n"
        "# In a person's folder, hold a cover 2.5 s, then tap it again, to delete it.\n"
        "# A person's page can hold a genre of their own (person_genre in settings.yaml), bottom right of the covers.\n"
        "# Home › Greatest Hits: Rolling Stone's 500 Greatest Albums (albums/top500.json), by rank.\n"
        "# More › Lights Out (coloured-tiles icon): a game page whose state is in homeassistant/deck_games.yaml.\n"
        "# More › Sleep starts timer.deck_sleep (homeassistant/deck_sleep.yaml); when it ends, everything turns off.\n"
        "# More › TV: the LG TV's power, and films that turn it on and play from Jellyfin (homeassistant/deck_tv.yaml).\n"
        "# More › Karaoke: one key per song in karaoke/songs.csv plays its YouTube karaoke video on the TV, Stop closes YouTube (homeassistant/deck_tv.yaml).\n"
        "# More › Sound Effects: one key per file in sounds/, played on a second speaker so the music keeps going (homeassistant/deck_sfx.yaml).\n"
    )
    import yaml
    out = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=200)
    # written in place, not via rename: the deck container bind-mounts this exact file
    target = HERE / "configuration.yaml"
    if not target.exists() or target.read_text() != header + out:
        target.write_text(header + out)
    print(f"configuration.yaml: {len(loaded)} genres with albums, {len(top)} top albums, "
          f"{sum(len(v) for v in loaded.values())} album keys")
    import subprocess
    import sys
    subprocess.run([sys.executable, str(HERE / "emulator" / "build.py")], check=True)


def sounds_now():
    return sorted((f.name, f.stat().st_mtime_ns) for f in SOUNDS.glob("*")) if SOUNDS.is_dir() else []


if __name__ == "__main__":
    # the path unit misses changes made while this runs (e.g. several sounds dropped at once): go again
    for _ in range(5):
        before = sounds_now()
        main()
        if sounds_now() == before:
            break
