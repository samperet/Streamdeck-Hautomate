#!/usr/bin/env python3
"""Build a browser emulator of the Stream Deck from ../configuration.yaml.

Downloads every Material Design icon, Spotify cover and font the page uses (once,
into .cache/), embeds them, and writes streamdeck-emulator-local.html: one file that
works offline from file://. Rebuilt automatically when the config changes (see
../autobuild/), or by hand:  python3 emulator/build.py
"""
import base64
import fcntl
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parent / "configuration.yaml"
TEMPLATE = HERE / "template.html"
LOCAL = HERE / "streamdeck-emulator-local.html"    # full document, open directly in a browser
ENV = HERE.parent / ".env"
LIVE_AUTH = HERE / ".live-auth.js"                 # token for live mode from file://; never copied to HA_WWW
_SETTINGS = next((f for f in (HERE.parent / "settings.yaml", HERE.parent / "settings.example.yaml") if f.exists()), None)
SETTINGS = (yaml.safe_load(_SETTINGS.read_text()) or {}) if _SETTINGS else {}
HA_CONFIG = Path(SETTINGS.get("ha_config", "~/homeassistant/config")).expanduser()  # Home Assistant's config folder
HA_WWW = HA_CONFIG / "www"   # Home Assistant serves this as /local/ (the Stream Deck dashboard)
VERSION_JS = ".build-version.js"  # beside the local page: which build is newest (the page polls it)
DASHBOARD = HA_CONFIG / "dashboards/stream_deck.yaml"  # embeds the page from /local/
CACHE = HERE / ".cache"
FONTS_URL = ("https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600"
             "&family=Barlow+Semi+Condensed:wght@600;700&family=Roboto:wght@500&family=JetBrains+Mono:wght@400&display=swap")
MDI_URL = "https://cdn.jsdelivr.net/npm/@mdi/svg@7.4.47/svg/{}.svg"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "streamdeck-emulator"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def cached(name, fetch):
    CACHE.mkdir(exist_ok=True)
    f = CACHE / name
    if f.exists():
        return f.read_bytes() or None
    try:
        data = fetch()
    except Exception as e:  # noqa: BLE001
        print(f"  ! {name}: {e}", file=sys.stderr)
        data = b""
    f.write_bytes(data)
    return data or None


# Templates the emulator knows how to evaluate, keyed by a recognisable fragment.
TEMPLATE_KINDS = [
    ("media_artist", "marquee"),  # the scrolling artist and song under the cover, "marquee:<n>:…" (gen_config.py)
    ("media_title", "title"),
    ("'pause' if is_state", "playpause_icon"),
    ("'volume-off' if", "mute_icon"),
    ("not state_attr", "mute_toggle"),
    ("volume_level", "volume_pct"),
    ("entity_picture", "tile"),  # the Now Playing panel's cover tiles, "tile:<n>:<url>" (gen_config.py)
]


GAME = re.compile(r"\{# deck-game (\w+) (\d+) (\w+) #\}")  # a game board key (gen_config.py, lights_out_page)
COND = re.compile(r"'([^']*)' if (is_state|state_attr)\('([^']+)', '([^']+)'\) else '([^']*)'")


def classify(value):
    if not isinstance(value, str) or "{{" not in value and "{%" not in value:
        return value
    # "src" is the template verbatim: live mode has Home Assistant render it exactly as the deck does
    m = GAME.search(value)
    if m:
        ent = re.search(r"input_text\.[a-z0-9_]+", value)
        return {"$tpl": "game", "game": m.group(1), "cell": int(m.group(2)), "part": m.group(3),
                "entity": ent.group(0) if ent else None, "src": value}
    m = COND.search(value)
    if m:
        a, fn, ent, arg, b = m.groups()
        return {"$tpl": "cond", "fn": fn, "entity": ent, "arg": arg, "a": a, "b": b, "src": value}
    for frag, kind in TEMPLATE_KINDS:
        if frag in value:
            m = re.search(r"media_player\.[a-z0-9_]+", value)
            return {"$tpl": kind, "entity": m.group(0) if m else None, "src": value}
    return {"$tpl": "unknown", "src": value}


def walk(obj):
    if isinstance(obj, dict):
        return {k: walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(v) for v in obj]
    return classify(obj)


CHICAGO = HERE.parent / "fonts/ChicagoFLF.ttf"  # the deck's font (visualizer/deck_run.py); the page uses it everywhere


def embedded_fonts():
    """The page's fonts as @font-face rules with the font files inlined: Chicago, and the Google
    fonts behind it (Latin subset)."""
    out = []
    if CHICAGO.exists():
        out.append('@font-face { font-family: "ChicagoFLF"; font-display: block; src: url(data:font/ttf;base64,'
                   + base64.b64encode(CHICAGO.read_bytes()).decode() + ') format("truetype") }')
    css = cached("fonts.css", lambda: get_as_browser(FONTS_URL))
    if not css:
        return "<style>\n" + "\n".join(out) + "\n</style>\n"
    for comment, block in re.findall(r"/\* ([\w-]+) \*/\s*(@font-face \{[^}]+\})", css.decode()):
        if comment != "latin":
            continue
        url = re.search(r"url\((https://[^)]+\.woff2)\)", block).group(1)
        woff = cached("font-" + url.rsplit("/", 1)[1], lambda u=url: get(u))
        if woff:
            out.append(block.replace(url, "data:font/woff2;base64," + base64.b64encode(woff).decode()))
    return "<style>\n" + "\n".join(out) + "\n</style>\n"


def get_as_browser(url):
    # Google Fonts only serves woff2 to browsers it recognises
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                                             "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def env():
    if not ENV.exists():
        return {}
    pairs = (l.split("=", 1) for l in ENV.read_text().splitlines() if "=" in l and not l.lstrip().startswith("#"))
    return {k.strip(): v.strip().strip("'\"") for k, v in pairs}


def write_live_auth(url, token):
    """Live mode's credentials for the file:// page, as a sidecar script readable only by you.

    Kept out of the page itself because the page is also served, without login, from Home Assistant's /local/."""
    if not token:
        LIVE_AUTH.unlink(missing_ok=True)
        return
    text = "window.DECK_LIVE = " + json.dumps({"url": url, "token": token}) + ";\n"
    if LIVE_AUTH.exists() and LIVE_AUTH.read_text() == text:
        return
    tmp = LIVE_AUTH.with_name(f".{LIVE_AUTH.name}.{os.getpid()}.tmp")  # the path unit may run a build alongside yours
    tmp.touch(mode=0o600)
    tmp.chmod(0o600)
    tmp.write_text(text)
    tmp.replace(LIVE_AUTH)


def write_atomic(path, data):
    """Write then rename, so a browser refresh never sees half a page; the temp name is per process."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def main():
    cfg = yaml.safe_load(CONFIG.read_text())
    pages = cfg.get("pages", [])
    anon = cfg.get("anonymous_pages", [])
    raw_text = CONFIG.read_text()

    # Icons named directly or produced by the known templates.
    icon_names = set(re.findall(r"icon_mdi:\s*([a-z0-9-]+)\s*(?:[,}\n])", raw_text))
    icon_names |= set(re.findall(r"'([a-z0-9-]+)' (?:if|else)", raw_text))
    icon_names |= {"arrow-right", "arrow-left", "power",  # special keys
                   "play", "pause", "skip-previous", "skip-next", "volume-high", "volume-off"}  # player controls
    icons, missing = {}, []
    for name in sorted(icon_names):
        svg = cached(f"mdi-{name}.svg", lambda n=name: get(MDI_URL.format(n)))
        m = re.search(rb' d="([^"]+)"', svg or b"")
        if m:
            icons[name] = m.group(1).decode()
        else:
            missing.append(name)

    # Spotify covers + titles; artists from the lists (oEmbed has none), for the scrolling artist in Dev mode
    artists = {}
    for f in [*(CONFIG.parent / "albums").glob("*.json"), *(CONFIG.parent / "people").glob("*.json")]:
        try:
            artists.update({it["id"]: it.get("artist", "") for it in json.loads(f.read_text()) if "id" in it})
        except (ValueError, TypeError, KeyError):
            pass
    albums = {}
    for kind, sid in sorted(set(re.findall(r"spotify:(album|playlist|artist|track|episode)[/:]([A-Za-z0-9]{22})", raw_text))):
        meta = cached(
            f"sp-{sid}.json",
            lambda k=kind, s=sid: get(f"https://open.spotify.com/oembed?url=https://open.spotify.com/{k}/{s}"),
        )
        info = json.loads(meta) if meta else {}
        img = cached(f"sp-{sid}.jpg", lambda u=info.get("thumbnail_url"): get(u) if u else b"")
        albums[sid] = {
            "title": info.get("title", f"{kind} {sid}"),
            "artist": artists.get(sid, ""),
            "img": "data:image/jpeg;base64," + base64.b64encode(img).decode() if img else None,
        }

    files = {}
    for rel in sorted(set(re.findall(r"/icons/([A-Za-z0-9_.-]+\.png)", raw_text))):
        f = CONFIG.parent / "icons" / rel
        if f.exists():
            files["/icons/" + rel] = "data:image/png;base64," + base64.b64encode(f.read_bytes()).decode()
        else:
            missing.append("/icons/" + rel)

    # Home › Sound Effects clips, by the name gen_config.py serves them under (www/sfx/)
    sounds = {}
    sfx = HA_WWW / "sfx"
    mime = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
            ".ogg": "audio/ogg", ".flac": "audio/flac"}
    for name in sorted(set(re.findall(r"/local/sfx/([A-Za-z0-9_.-]+)", raw_text))):
        f = sfx / name
        if f.exists():
            sounds[name] = f"data:{mime.get(f.suffix, 'audio/mpeg')};base64," + base64.b64encode(f.read_bytes()).decode()
        else:
            missing.append("/local/sfx/" + name)

    # Which entity IDs really exist in Home Assistant (registry is world-readable on this box).
    known = []
    reg = HA_CONFIG / ".storage/core.entity_registry"
    if reg.exists():
        known = [e["entity_id"] for e in json.loads(reg.read_text())["data"]["entities"]]
    players = re.findall(r"media_player\.[a-z0-9_]+", raw_text)

    e = env()
    ha_url = ("https" if e.get("WEBSOCKET_PROTOCOL") == "wss" else "http") + "://" + e.get("HASS_HOST", "localhost:8123")
    write_live_auth(ha_url, e.get("HASS_TOKEN"))

    # the deck's size from its pages: 32 keys is an XL (8x4, 96 px keys), 15 the MK.2/original (5x3, 72 px)
    n = len(pages[0]["buttons"]) if pages else 32
    deck = {"cols": 8, "rows": 4, "key": 96} if n == 32 else {"cols": 5, "rows": 3, "key": 72}
    player = SETTINGS.get("player")
    data = {
        "haUrl": ha_url,
        "deck": deck,
        "player": player or (players[0] if players else "media_player.wiim"),
        "knownEntities": known,
        "config": {
            "brightness": cfg.get("brightness", 100),
            "long_press_duration": cfg.get("long_press_duration", 0.5),
            "pages": walk(pages),
            "anonymous_pages": walk(anon),
        },
        "icons": icons,
        "missingIcons": missing,
        "albums": albums,
        "files": files,
        "sounds": sounds,
    }
    # this build's id: the open emulator polls VERSION_JS for it and reloads itself when it changes
    template = TEMPLATE.read_text()
    v = hashlib.sha1((template + json.dumps(data, sort_keys=True)).encode()).hexdigest()[:10]
    data["build"] = v
    html = template.replace("/*__DATA__*/null", json.dumps(data))
    html = re.sub(r'<link rel="(?:preconnect|stylesheet)" href="https://fonts\.[^>]*>\n?', "", html)
    page = (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<style>body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n'
        + embedded_fonts() + '</head>\n<body>\n' + html + '\n</body>\n</html>\n'
    ).encode()
    notice = f'window.DECK_BUILD_SEEN = "{v}";\n'.encode()
    write_atomic(LOCAL, page)
    write_atomic(LOCAL.parent / VERSION_JS, notice)  # after the page, so a reload finds the new one
    if HA_WWW.is_dir():  # same page for the Home Assistant dashboard, at /local/streamdeck-emulator.html
        write_atomic(HA_WWW / "streamdeck-emulator.html", page)
        write_atomic(HA_WWW / "streamdeck-emulator-version.js", notice)
        # Home Assistant lets browsers cache /local/ for 31 days, so the dashboard links to ?v=<this build>
        if DASHBOARD.exists():
            dash = DASHBOARD.read_text()
            new = re.sub(r"/local/streamdeck-emulator\.html(?:\?v=\w+)?", f"/local/streamdeck-emulator.html?v={v}", dash)
            if new != dash:
                write_atomic(DASHBOARD, new.encode())
    print(f"wrote {LOCAL.name} ({len(page) // 1024} KB); "
          f"{len(icons)} icons, {len(files)} key images, {len(sounds)} sounds, {len(albums)} covers, missing icons: {missing or 'none'}")

if __name__ == "__main__":
    # one build at a time: the path units and a hand-run gen_config.py often start together
    with open(HERE / ".build.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main()
