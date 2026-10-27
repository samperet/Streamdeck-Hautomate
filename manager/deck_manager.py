#!/usr/bin/env python3
"""Deck manager: lets Home Assistant Assist edit the people folders on the Stream Deck.

Assist phrases like "add Kind of Blue by Miles Davis to Alex" arrive (through the
rest_command in ../homeassistant/deck_intercom.yaml) as POST requests:

    POST /api/add     {"item": "kind of blue by miles davis", "person": "Alex"}
    POST /api/remove  {"item": "kind of blue", "person": "Alex"}     (item "last" = newest)
    POST /api/list    {"person": "Alex"}

Each answers {"speech": "..."} for Assist to say. A change edits people/<name>.json and
re-runs gen_config.py, which rewrites configuration.yaml (the deck reloads it by itself)
and rebuilds the local emulator page.

An item can be a Spotify link or URI, or a name. Names need a Spotify API key in
manager/.env (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET); links always work.

Listen mode (holding a blank cover key on the deck, through script.deck_listen):

    POST /api/listen       {"folder": "people/alex"}   (or "albums/jazz"; "voice": false skips the microphone)
    POST /api/listen_take  {}

Push-to-talk, from the deck itself (visualizer/deck_run.py) while a blank cover key is held:

    POST /api/voice_start  {"folder": "people/alex"}   key pressed: start recording
    POST /api/voice_stop   {"folder": "people/alex"}   key released: transcribe and add
                           -> {"speech": "...", "heard": bool, "added": bool}
    POST /api/voice_cancel {}                         a tap too short to say anything

Deleting a cover from the deck (held 2.5 s, then tapped to confirm):

    POST /api/remove_id    {"folder": "people/alex", "id": "<Spotify ID>"}

/api/listen first listens on this computer's microphone for VOICE_WAIT seconds: say an
album ("Kind of Blue by Miles Davis"), an artist ("the artist Nina Simone") or a playlist
("the playlist Sunday Morning") and it is looked up on Spotify and added. Speech-to-text is
Whisper (the "whisper" service in ~/homeassistant/compose.yaml, at WHISPER_HOST). If you
say nothing, it watches your Spotify playback for LISTEN_SECONDS and adds whatever you
start: the album, playlist or artist it plays from, otherwise the song. /api/listen_take (or
holding another blank key in the same folder) takes what is playing right now. It needs
a one-time Spotify login: open http://127.0.0.1:8765/spotify/login on this computer.

Over SSH, set up the key and login in the terminal instead:  python3 manager/deck_manager.py setup

Standard library only. Listens on 127.0.0.1:8765, reachable only from this computer.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import html
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DECK = HERE.parent
sys.path.insert(0, str(DECK))
from gen_config import GENRES, LISTEN_SECONDS, NICKNAMES, PEOPLE, PER_PAGE, place  # noqa: E402  (one list for deck and manager)

PORT = int(os.environ.get("DECK_MANAGER_PORT", "8765"))
REDIRECT = f"http://127.0.0.1:{PORT}/spotify/callback"  # add this exact URI to the Spotify app's settings
USER_TOKEN = HERE / "spotify_user.json"  # your Spotify login (refresh token), for listen mode
LINK = re.compile(r"(album|playlist|artist)[/:\s]?([A-Za-z0-9]{22})(?![A-Za-z0-9])")  # Assist strips the colons from URIs
LOCK = threading.Lock()


def load_env() -> None:
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def log(msg: str) -> None:
    print(f"[deck-manager] {time.strftime('%H:%M:%S')} {msg}", flush=True)


# ── people ──
def person(name: str) -> tuple[str, str] | None:
    """Match what Assist heard to (slug, display name): "Alex's", a nickname from settings.yaml and so on."""
    n = re.sub(r"('s|s')$", "", name.strip().lower()).strip()
    n = NICKNAMES.get(n, n)
    for slug, display in PEOPLE:
        if n in (slug, display.lower()) or n.rstrip("s") == slug:
            return slug, display
    close = difflib.get_close_matches(n, [s for s, _ in PEOPLE], n=1, cutoff=0.75)
    return next(((s, d) for s, d in PEOPLE if close and s == close[0]), None)


def folder_path(slug: str) -> Path:
    return DECK / "people" / f"{slug}.json"


def deck_folder(spec: str) -> tuple[Path, str] | None:
    """A deck folder named like "people/alex" or "albums/jazz" -> (its file, the name to say)."""
    kind, _, slug = spec.strip().lower().partition("/")
    if kind == "people":
        return next(((folder_path(s), f"{d}'s folder") for s, d in PEOPLE if s == slug), None)
    if kind == "albums":
        return next(((DECK / "albums" / f"{g}.json", f"the {label.replace(chr(10), ' ')} page")
                     for g, label in GENRES if g == slug), None)
    return None


def read_folder(path: Path) -> list[dict]:
    return json.loads(path.read_text()) if path.exists() else []


def write_folder(path: Path, items: list[dict]) -> None:
    path.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n")
    r = subprocess.run([sys.executable, str(DECK / "gen_config.py")], capture_output=True, text=True, cwd=DECK)
    if r.returncode:
        raise RuntimeError(f"gen_config.py failed: {r.stderr.strip()[-300:]}")


# ── Spotify ──
def http(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def from_link(kind: str, sid: str) -> dict:
    """Look up a Spotify album or playlist by ID (no API key needed)."""
    meta = json.loads(http(f"https://open.spotify.com/oembed?url=https://open.spotify.com/{kind}/{sid}"))
    artist = ""
    try:
        page = http(f"https://open.spotify.com/{kind}/{sid}").decode("utf8", "replace")
        m = re.search(r'<meta property="og:description" content="([^"]*)"', page)
        parts = (m.group(1) if m else "").split(" · ")
        artist = parts[0] if kind == "album" else (parts[1] if kind == "playlist" and len(parts) > 1 else "")
    except Exception:  # noqa: BLE001  (the artist is a nicety)
        pass
    return {"kind": kind, "id": sid, "title": meta["title"], "artist": artist}


_token: dict = {}


def spotify_token() -> str | None:
    cid, secret = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not secret or "PASTE" in cid:
        return None
    if _token.get("exp", 0) > time.time() + 60:
        return _token["value"]
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    r = json.loads(http("https://accounts.spotify.com/api/token", b"grant_type=client_credentials",
                        {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"}))
    _token.update(value=r["access_token"], exp=time.time() + r.get("expires_in", 3600))
    return _token["value"]


def search(text: str) -> dict | None:
    """Find an album, artist or playlist on Spotify by name.

    "the playlist X", "the artist X" (or "songs by X") and "the album X" say which; "X by Y"
    is an album. Otherwise all three are searched and the name closest to what was said wins.
    """
    token = spotify_token()
    if token is None:
        raise LookupError("nokey")
    words = re.sub(r"\s+", " ", text).strip(" ,.!?")
    kind = None
    if re.search(r"\bplaylist\b", words, re.I):
        kind = "playlist"
    elif re.search(r"\bartist\b|\b(songs|music|anything|stuff) (by|from)\b", words, re.I):
        kind = "artist"
    elif re.search(r"\b(album|record)\b", words, re.I):
        kind = "album"
    words = re.sub(r"\b(the )?(album|playlist|record|artist)\b|\b(songs|music|anything|stuff) (by|from)\b",
                   " ", words, flags=re.I)
    words = re.sub(r"\s+", " ", words).strip(" ,.")
    m = re.match(r"(.+?)\s+by\s+(.+)", words, re.I) if kind in (None, "album") else None
    title, artist = (m.group(1), m.group(2)) if m else (words, "")
    if not title:
        return None
    if kind is None and artist:
        kind = "album"
    types = [kind] if kind else ["album", "artist", "playlist"]
    find = lambda q, t: json.loads(http("https://api.spotify.com/v1/search?" + urllib.parse.urlencode(  # noqa: E731
        {"q": q, "type": ",".join(t), "limit": 5}), headers={"Authorization": f"Bearer {token}"}))
    q = f'album:"{title}" artist:"{artist}"' if kind == "album" and artist else f"{title} {artist}".strip()
    r = find(q, types)
    hits = {t: [h for h in r.get(f"{t}s", {}).get("items", []) if h] for t in types}
    if kind == "album" and artist and not hits["album"]:  # field filters are strict; retry loosely
        hits["album"] = [h for h in find(f"{title} {artist}", ["album"]).get("albums", {}).get("items", []) if h]
    found = [(t, rank, h) for t in types for rank, h in enumerate(hits[t])]
    if not found:
        return None
    if kind is None:  # closest name to what was said; ties go album, artist, playlist
        said = words.lower()
        bonus = {"album": 0.03, "artist": 0.02, "playlist": 0.0}
        def score(f):
            t, rank, h = f
            names = [h["name"].lower()] + [f"{h['name']} by {a['name']}".lower() for a in h.get("artists", [])]
            return max(difflib.SequenceMatcher(None, said, n).ratio() for n in names) + bonus[t] - rank * 0.02
        t, _, h = max(found, key=score)
    else:
        t, _, h = found[0]
    if t == "album":
        who = ", ".join(a["name"] for a in h.get("artists", []))
    elif t == "playlist":
        who = (h.get("owner") or {}).get("display_name", "")
    else:
        who = ""
    return {"kind": t, "id": h["id"], "title": h["name"], "artist": who}


# ── voice: the microphone on this computer, transcribed by Whisper ──
WHISPER_HOST = os.environ.get("WHISPER_HOST", "127.0.0.1:10300")  # Wyoming protocol (rhasspy/wyoming-whisper)
VOICE_WAIT = 6      # seconds to start talking after the hold, else listen mode watches Spotify instead
VOICE_MAX = 12      # longest a request can be
VOICE_PAUSE = 1.2   # this much quiet ends it
RATE, CHUNK = 16000, 480  # 30 ms of 16-bit mono


def _level(pcm: bytes) -> float:
    a = memoryview(pcm).cast("h")
    m = sum(a) / len(a)
    return (sum((x - m) ** 2 for x in a) / len(a)) ** 0.5


def record_speech(stop: threading.Event) -> bytes | None:
    """Record from the default microphone until you stop talking; None if nobody spoke."""
    try:
        rec = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as e:
        log(f"microphone unavailable: {e}")
        return None
    read = lambda: rec.stdout.read(CHUNK * 2)  # noqa: E731
    try:
        for _ in range(17):  # the first half second is the mic settling
            read()
        floor = sorted(_level(read()) for _ in range(10))[5]
        loud = max(floor * 2.5, floor + 1500)
        before, audio, run, quiet, n = [], [], 0, 0, 0
        while not stop.is_set():
            buf = read()
            if len(buf) < CHUNK * 2:
                log("microphone stopped")
                return None
            n += 1
            voiced = _level(buf) > loud
            if not audio:
                before = (before + [buf])[-10:]   # keep 300 ms from just before you start
                run = run + 1 if voiced else 0
                if run >= 3:
                    audio = before[:]
                elif n * 0.03 > VOICE_WAIT:
                    return None
                continue
            audio.append(buf)
            quiet = 0 if voiced else quiet + 1
            if quiet * 0.03 >= VOICE_PAUSE or len(audio) * 0.03 >= VOICE_MAX:
                return b"".join(audio)
        return None
    finally:
        rec.terminate()
        rec.wait()


def transcribe(pcm: bytes) -> str:
    """Speech to text with Whisper over the Wyoming protocol."""
    host, _, port = WHISPER_HOST.rpartition(":")
    fmt = {"rate": RATE, "width": 2, "channels": 1}
    with socket.create_connection((host, int(port)), timeout=60) as sock:
        def send(kind: str, data: dict, payload: bytes = b"") -> None:
            body = json.dumps(data).encode()
            head = {"type": kind, "version": "1.0.0", "data_length": len(body), "payload_length": len(payload) or None}
            sock.sendall(json.dumps(head).encode() + b"\n" + body + payload)
        send("transcribe", {"language": "en"})
        send("audio-start", fmt)
        for i in range(0, len(pcm), RATE * 2):
            send("audio-chunk", fmt, pcm[i:i + RATE * 2])
        send("audio-stop", {})
        f = sock.makefile("rb")
        while line := f.readline():
            ev = json.loads(line)
            data = ev.get("data") or {}
            if ev.get("data_length"):
                data = json.loads(f.read(ev["data_length"]))
            if ev.get("payload_length"):
                f.read(ev["payload_length"])
            if ev.get("type") == "transcript":
                return (data.get("text") or "").strip()
    raise ConnectionError("Whisper closed the connection")


def whisper_ok() -> bool:
    host, _, port = WHISPER_HOST.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=2).close()
        return True
    except OSError:
        return False


def spoken_item(text: str) -> str:
    """What to search for in a dictated request: drops "add", "please", "to Alex's folder" and so on."""
    t = re.sub(r"^\s*(please\s+)?(add|put|save|play)\s+", "", text.strip(" .!?"), flags=re.I)
    t = re.sub(r"[\s,]+please$", "", t, flags=re.I)
    t = re.sub(r"\s+(to|in|into|on)\s+(\w+(['’]s|s)?\s+)?(folder|page|deck|button)$", "", t, flags=re.I)
    return t.strip(" ,.!?")


def add_spoken(text: str, path: Path, label: str, slot: int | None = None) -> str:
    item = spoken_item(text)
    if not item:
        return "I didn't catch anything to add."
    try:
        found = search(item)
    except LookupError:
        return (f"I heard “{item}”, but searching Spotify by name needs the Spotify key. "
                f"On the server, paste it at http://127.0.0.1:{PORT}/spotify/setup.")
    except urllib.error.HTTPError as e:
        return f"I heard “{item}”, but the Spotify search failed ({e.code})."
    if not found:
        return f"I heard “{item}”, but couldn't find it on Spotify."
    return add_found(path, label, found, slot)


# ── push-to-talk: record for as long as the deck key is held ──
HALLUCINATIONS = re.compile(r"[\W_]*|\W*(thank you|thanks for watching|you|bye)\W*", re.I)  # Whisper on silence


class Talk:
    """The recording while a key is held. One at a time; a new press replaces it."""
    current: Talk | None = None
    lock = threading.Lock()

    def __init__(self, path: Path, label: str, slot: int | None):
        self.path, self.label, self.slot, self.chunks = path, label, slot, []
        self.rec = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        while buf := self.rec.stdout.read(CHUNK * 2):
            self.chunks.append(buf)
            if time.monotonic() - self.started > 60:  # a key wedged down: stop anyway
                break
        self.rec.terminate()

    def finish(self) -> bytes:
        self.rec.terminate()
        self.rec.wait()
        self.thread.join(2)
        return b"".join(self.chunks)


def cmd_voice_start(spec: str, slot: int | None = None) -> dict:
    folder = deck_folder(spec)
    if not folder:
        return {"speech": f"There's no deck folder called {spec}.", "ok": False}
    with Listen.lock:  # pressing a key ends a listen-mode wait elsewhere
        if Listen.current:
            Listen.current.stop.set()
    with Talk.lock:
        if Talk.current:
            Talk.current.finish()
        try:
            Talk.current = Talk(*folder, slot)
        except OSError as e:
            Talk.current = None
            return {"speech": f"The microphone isn't available: {e}", "ok": False}
    log(f"recording for {spec}")
    return {"ok": True}


def cmd_voice_stop() -> dict:
    with Talk.lock:
        talk, Talk.current = Talk.current, None
    if not talk:
        return {"speech": "The deck wasn't recording.", "heard": False, "added": False}
    pcm = talk.finish()
    log(f"recorded {len(pcm) / (RATE * 2):.1f}s for {talk.path.relative_to(DECK)}")
    if len(pcm) < RATE * 2 * 0.6:
        return {"speech": "That was too short to hear anything.", "heard": False, "added": False}
    try:
        text = transcribe(pcm)
    except (OSError, ValueError) as e:
        log(f"whisper failed: {e!r}")
        return {"speech": "I couldn't make out what you said: speech-to-text isn't answering.", "heard": True, "added": False}
    log(f"heard: {text!r}")
    if HALLUCINATIONS.fullmatch(text):
        return {"speech": "I didn't hear anything.", "heard": False, "added": False}
    speech = add_spoken(text, talk.path, talk.label, talk.slot)
    return {"speech": speech, "heard": True, "added": speech.startswith("Added ")}


def cmd_voice_cancel() -> dict:
    with Talk.lock:
        talk, Talk.current = Talk.current, None
    if talk:
        talk.finish()
    return {"ok": True}


# ── your Spotify login (listen mode reads what you're playing) ──
# Authorization code with PKCE: needs only the Client ID, and the refresh token stays in USER_TOKEN.
_login: dict = {}   # state -> code verifier, while a login is in progress
_user: dict = {}


def client_id() -> str | None:
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    return None if not cid or "PASTE" in cid else cid


def save_keys(cid: str, secret: str) -> None:
    """Write the Spotify app's Client ID and secret into manager/.env and use them right away."""
    env = HERE / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    for name, value in (("SPOTIFY_CLIENT_ID", cid), ("SPOTIFY_CLIENT_SECRET", secret)):
        if not value:
            continue
        new = f"{name}={value}"
        lines = [new if ln.startswith(f"{name}=") else ln for ln in lines]
        if new not in lines:
            lines.append(new)
        os.environ[name] = value
    env.touch(mode=0o600)
    env.chmod(0o600)  # it holds the secret
    env.write_text("\n".join(lines) + "\n")
    _token.clear()  # the old app-only token belongs to the old key


SETUP_FORM = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Spotify key</title>
<body style="font:16px system-ui;max-width:36em;margin:3em auto;padding:0 16px">
<h1>Spotify key for the Stream Deck</h1>
<p>From your app at <b>developer.spotify.com/dashboard</b> › Settings. Saved only on this computer, in
<code>streamdeck-yaml/manager/.env</code>.</p>
<p>In that app's settings, also add this Redirect URI: <code>{redirect}</code></p>
<form method=post action=/spotify/setup>
<p><label>Client ID<br><input name=client_id autocomplete=off spellcheck=false style="width:100%;font:inherit;padding:6px"
 placeholder="{cid_hint}"></label></p>
<p><label>Client secret<br><input name=client_secret type=password autocomplete=off style="width:100%;font:inherit;padding:6px"
 placeholder="{secret_hint}"></label></p>
<p><button style="font:inherit;padding:6px 14px">Save and connect Spotify</button></p>
</form>
<p style="color:#666">Leave a box empty to keep what's saved. After saving you'll go to Spotify to approve listen mode once.</p>
"""


def login_url() -> str:
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(16)
    _login.clear()
    _login[state] = verifier
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id(), "response_type": "code", "redirect_uri": REDIRECT, "state": state,
        "code_challenge_method": "S256", "code_challenge": challenge,
        "scope": "user-read-playback-state user-read-currently-playing"})


def _save_user(r: dict) -> None:
    _user.update(value=r["access_token"], exp=time.time() + r.get("expires_in", 3600),
                 refresh=r.get("refresh_token") or _user.get("refresh"))
    USER_TOKEN.touch(mode=0o600)
    USER_TOKEN.chmod(0o600)
    USER_TOKEN.write_text(json.dumps({"refresh_token": _user["refresh"]}))


def _token_request(fields: dict) -> dict:
    return json.loads(http("https://accounts.spotify.com/api/token", urllib.parse.urlencode(fields).encode(),
                           {"Content-Type": "application/x-www-form-urlencoded"}))


def finish_login(code: str, state: str) -> None:
    verifier = _login.pop(state, None)
    if verifier is None:
        raise ValueError("That login link is stale. Start again from /spotify/login.")
    _save_user(_token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                               "client_id": client_id(), "code_verifier": verifier}))


def user_token() -> str | None:
    """An access token for your Spotify account, or None if you haven't logged in."""
    if _user.get("exp", 0) > time.time() + 60:
        return _user["value"]
    if not _user.get("refresh") and USER_TOKEN.exists():
        _user["refresh"] = json.loads(USER_TOKEN.read_text()).get("refresh_token")
    if not _user.get("refresh") or not client_id():
        return None
    _save_user(_token_request({"grant_type": "refresh_token", "refresh_token": _user["refresh"],
                               "client_id": client_id()}))
    return _user["value"]


def now_playing() -> dict | None:
    """Your Spotify playback (any device), or None when nothing is loaded."""
    token = user_token()
    if token is None:
        raise LookupError("nologin")
    raw = http("https://api.spotify.com/v1/me/player?additional_types=episode",
               headers={"Authorization": f"Bearer {token}"})
    return json.loads(raw) if raw.strip() else None  # 204: no active device


def playing_item(pb: dict) -> dict | None:
    """What a playback is playing from: its album or playlist, otherwise the song (or episode) itself."""
    item = pb.get("item") or {}
    ctx = pb.get("context") or {}
    sid = (ctx.get("uri") or "").split(":")[-1]  # spotify:album:ID, spotify:playlist:ID (or older spotify:user:…:playlist:ID)
    names = lambda xs: ", ".join(a["name"] for a in xs or [])  # noqa: E731
    if ctx.get("type") == "album" and (item.get("album") or {}).get("id") == sid:
        return {"kind": "album", "id": sid, "title": item["album"]["name"], "artist": names(item["album"].get("artists"))}
    if ctx.get("type") == "artist" and sid:
        try:
            return from_link("artist", sid)
        except Exception:  # noqa: BLE001
            pass
    if ctx.get("type") == "playlist" and sid:
        try:
            return from_link("playlist", sid)
        except Exception:  # noqa: BLE001  (private playlists have no public page)
            return {"kind": "playlist", "id": sid, "title": "Playlist", "artist": ""}
    if item.get("type") == "track":
        return {"kind": "track", "id": item["id"], "title": item["name"], "artist": names(item.get("artists"))}
    if item.get("type") == "episode":
        return {"kind": "episode", "id": item["id"], "title": item["name"],
                "artist": (item.get("show") or {}).get("name", "")}
    return None


def describe(it: dict) -> str:
    if it.get("kind") == "playlist":
        return f"the playlist {it['title']}"
    if it.get("kind") == "artist":
        return f"the artist {it['title']}"
    return f"{it['title']} by {it['artist']}" if it.get("artist") else it["title"]


def where(index: int) -> str:
    page, slot = divmod(index, PER_PAGE)
    return f"page {page + 1}, key {slot + 1}" if page else f"key {slot + 1}"


# ── commands ──
def cmd_add(item: str, who: str) -> str:
    p = person(who)
    if not p:
        return f"I don't have a folder for {who}. The folders are {', '.join(d for _, d in PEOPLE)}."
    slug, name = p
    m = LINK.search(item)
    try:
        found = from_link(m.group(1), m.group(2)) if m else search(item)
    except LookupError:
        return ("I can only add Spotify links until the Spotify key is set up. "
                "Paste a link like open.spotify.com/album/..., or add the key to streamdeck-yaml/manager/.env.")
    except urllib.error.HTTPError as e:
        return "That Spotify link doesn't open. Check it's complete." if m else f"Spotify search failed ({e.code})."
    if not found:
        return f"I couldn't find {item} on Spotify."
    return add_found(folder_path(slug), f"{name}'s folder", found)


def add_found(path: Path, label: str, found: dict, slot: int | None = None) -> str:
    """Add to a folder: on key `slot` (the blank key you held) if given and still free, else the first free key."""
    with LOCK:
        items = read_folder(path)
        if any(i["id"] == found["id"] for i in items):
            return f"{describe(found)} is already in {label}."
        found = dict(found)
        if slot is not None:
            placed = place(items)
            for s, it in placed.items():  # pin everything where it is now, so nothing shifts around the new one
                it["slot"] = s
            while slot in placed:  # taken since (another add): the next free key
                slot += 1
            found["slot"] = slot
        items.append(found)
        write_folder(path, items)
        at = next(s for s, it in place(items).items() if it["id"] == found["id"])
    log(f"added {found['kind']} {found['id']} ({found['title']}) to {path.relative_to(DECK)} at key {at}")
    return f"Added {describe(found)} to {label}, {where(at)}."


# ── listen mode ──
class Listen:
    """One listen-mode session; a newer one (for another folder) stops it."""
    current: Listen | None = None
    lock = threading.Lock()

    def __init__(self, path: Path, label: str):
        self.path, self.label = path, label
        self.take, self.stop = threading.Event(), threading.Event()


def signature(pb: dict | None) -> tuple | None:
    """What counts as 'something new started': the album or playlist playing from, else the song.
    (So an album moving on to its next track doesn't count.)"""
    if not pb or not pb.get("is_playing"):
        return None
    return (pb.get("context") or {}).get("uri") or (pb.get("item") or {}).get("id")


def cmd_listen(spec: str, voice: bool = True, slot: int | None = None) -> dict:
    """Wait for you to start something on Spotify, then add it. Returns speech plus whether
    this call owns the deck's "Listening…" indicator (so Home Assistant knows to clear it)."""
    folder = deck_folder(spec)
    if not folder:
        return {"speech": f"There's no deck folder called {spec}.", "clear": True}
    path, label = folder
    voice = voice and whisper_ok()
    if not client_id() and not voice:
        return {"speech": f"Listen mode needs the Spotify key. On the server, paste it at http://127.0.0.1:{PORT}/spotify/setup.", "clear": True}
    with Listen.lock:
        old = Listen.current
        if old and old.path == path:  # held a second blank key in the same folder: take what's on now
            old.take.set()
            return {"speech": "Taking what's playing now.", "clear": False}
        if old:
            old.stop.set()
        me = Listen.current = Listen(path, label)
    try:
        if voice:  # voice first: say what to add
            log(f"listening on the microphone for {path.relative_to(DECK)}")
            pcm = record_speech(me.stop)
            if me.stop.is_set():
                return {"speech": "Stopped listening.", "clear": False}
            if pcm and not me.take.is_set():
                try:
                    text = transcribe(pcm)
                except (OSError, ValueError) as e:
                    log(f"whisper failed: {e!r}")
                    return {"speech": "I couldn't make out what you said: speech-to-text isn't answering.", "clear": True}
                log(f"heard: {text!r}")
                if not HALLUCINATIONS.fullmatch(text):
                    return {"speech": add_spoken(text, path, label, slot), "clear": True}
        if not client_id():
            return {"speech": f"I didn't hear anything. Watching Spotify instead needs the Spotify key: "
                              f"paste it at http://127.0.0.1:{PORT}/spotify/setup.", "clear": True}
        try:
            start = signature(now_playing())
        except LookupError:
            return {"speech": f"Listen mode isn't connected to your Spotify yet. On the server, open "
                              f"http://127.0.0.1:{PORT}/spotify/login once.", "clear": True}
        log(f"listening for {path.relative_to(DECK)} (playing at start: {start})")
        deadline = time.monotonic() + LISTEN_SECONDS
        while True:
            me.take.wait(1.5)
            if me.stop.is_set():
                return {"speech": "Stopped listening.", "clear": False}  # the newer session owns the indicator
            pb = now_playing()
            sig = signature(pb)
            if me.take.is_set() or (sig and sig != start):
                found = playing_item(pb) if sig else None
                if not found:
                    return {"speech": "Nothing is playing on Spotify, so nothing was added.", "clear": True}
                return {"speech": add_found(path, label, found, slot), "clear": True}
            if time.monotonic() > deadline:
                return {"speech": f"I didn't hear anything new on Spotify in {LISTEN_SECONDS} seconds, "
                                  "so nothing was added.", "clear": True}
    finally:
        with Listen.lock:
            if Listen.current is me:
                Listen.current = None


def cmd_listen_take() -> dict:
    with Listen.lock:
        if Listen.current:
            Listen.current.take.set()
            return {"speech": "Taking what's playing now.", "clear": False}
    return {"speech": "The deck isn't listening right now. Hold a blank key to start.", "clear": False}


def cmd_remove(item: str, who: str) -> str:
    p = person(who)
    if not p:
        return f"I don't have a folder for {who}."
    slug, name = p
    with LOCK:
        items = read_folder(folder_path(slug))
        if not items:
            return f"{name}'s folder is already empty."
        q = item.strip().lower()
        if q in ("last", "the last one", "the last", "that", "it", "the newest one", "the latest one"):
            idx = len(items) - 1
        else:
            m = LINK.search(item)
            ids = [i["id"] for i in items]
            if m and m.group(2) in ids:
                idx = ids.index(m.group(2))
            else:
                names = [f"{i['title']} by {i.get('artist', '')}".lower() for i in items]
                titles = [i["title"].lower() for i in items]
                hit = (difflib.get_close_matches(q, titles, n=1, cutoff=0.5)
                       or difflib.get_close_matches(q, names, n=1, cutoff=0.5))
                if not hit:
                    contains = [t for t in titles if q in t]
                    hit = contains[:1]
                if not hit:
                    return f"I can't find {item} in {name}'s folder."
                idx = titles.index(hit[0]) if hit[0] in titles else names.index(hit[0])
        gone = items.pop(idx)
        write_folder(folder_path(slug), items)
    log(f"removed {gone['id']} ({gone['title']}) from {slug}")
    return f"Removed {describe(gone)} from {name}'s folder."


def cmd_remove_id(spec: str, sid: str) -> str:
    folder = deck_folder(spec)
    if not folder:
        return f"There's no deck folder called {spec}."
    path, label = folder
    with LOCK:
        items = read_folder(path)
        keep = [i for i in items if i["id"] != sid]
        if len(keep) == len(items):
            return f"That one is already gone from {label}."
        gone = next(i for i in items if i["id"] == sid)
        write_folder(path, keep)
    log(f"removed {sid} ({gone['title']}) from {path.relative_to(DECK)}")
    return f"Removed {describe(gone)} from {label}."


def cmd_list(who: str) -> str:
    p = person(who)
    if not p:
        return f"I don't have a folder for {who}."
    slug, name = p
    items = read_folder(folder_path(slug))
    if not items:
        return f"{name}'s folder is empty."
    shown = [describe(i) for i in items[:8]]
    more = f", and {len(items) - 8} more" if len(items) > 8 else ""
    listing = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + (" and " if not more else ", ") + shown[-1]
    return f"{name}'s folder has {len(items)} item{'s' * (len(items) != 1)}: {listing}{more}."


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _page(self, code: int, title: str, text: str, location: str | None = None) -> None:
        raw = (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
               f"<body style='font:16px system-ui;max-width:36em;margin:3em auto;padding:0 16px'>"
               f"<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>").encode()
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        if url.path == "/health":
            return self._send(200, {"ok": True, "people": [d for _, d in PEOPLE], "search": spotify_token_ok(),
                                    "listen": spotify_user_ok(), "voice": whisper_ok()})
        if url.path == "/spotify/setup":
            saved = lambda name: os.environ.get(name) and "PASTE" not in os.environ[name]  # noqa: E731
            raw = SETUP_FORM.format(redirect=REDIRECT,
                                    cid_hint="saved (paste to replace)" if client_id() else "paste the Client ID",
                                    secret_hint="saved (paste to replace)" if saved("SPOTIFY_CLIENT_SECRET")
                                    else "paste the Client secret").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return self.wfile.write(raw)
        if url.path == "/spotify/login":
            if not client_id():
                return self._page(302, "Spotify not set up", "Paste the key first.", "/spotify/setup")
            return self._page(302, "Spotify login", "Redirecting to Spotify…", login_url())
        if url.path == "/spotify/callback":
            if "error" in q:
                return self._page(200, "Spotify login cancelled", q["error"])
            try:
                finish_login(q.get("code", ""), q.get("state", ""))
            except Exception as e:  # noqa: BLE001
                log(f"spotify login failed: {e!r}")
                return self._page(200, "Spotify login failed", str(e))
            log("spotify login saved")
            return self._page(200, "Spotify connected", "Listen mode is ready: hold a blank cover key on the deck, "
                                                        "then start an album, playlist or song in Spotify.")
        self._send(404, {"speech": "Not found."})

    def do_POST(self):  # noqa: N802
        if self.path == "/spotify/setup":
            # only this computer's own setup page may change the key (not some other website in the browser)
            if self.headers.get("Origin") not in (None, "null", f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
                return self._page(403, "Not allowed", "Open http://127.0.0.1:8765/spotify/setup directly.")
            form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()))
            cid, secret = form.get("client_id", "").strip(), form.get("client_secret", "").strip()
            if (cid and not re.fullmatch(r"[0-9a-fA-F]{32}", cid)) or (secret and not re.fullmatch(r"[0-9a-fA-F]{32}", secret)):
                return self._page(200, "That doesn't look right",
                                  "The Client ID and secret are each 32 letters and digits (0-9, a-f). Go back and check.")
            save_keys(cid, secret)
            log("spotify key saved from /spotify/setup")
            if not client_id():
                return self._page(200, "Saved", "Now paste the Client ID too.")
            return self._page(303, "Saved", "Going to Spotify…", "/spotify/login")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            item, who = str(body.get("item", "")).strip(), str(body.get("person", "")).strip()
            slot = body.get("slot")
            try:  # the blank key held, from the deck (Home Assistant may send 5.0)
                slot = int(float(slot)) if slot not in (None, "", "None") else None
            except (TypeError, ValueError):
                slot = None
            if self.path in ("/api/voice_start", "/api/voice_stop", "/api/voice_cancel"):
                reply = (cmd_voice_start(str(body.get("folder", "")), slot) if self.path == "/api/voice_start"
                         else cmd_voice_stop() if self.path == "/api/voice_stop" else cmd_voice_cancel())
                if "speech" in reply:
                    log(f"{self.path} {body} -> {reply['speech']}")
                return self._send(200, reply)
            if self.path in ("/api/listen", "/api/listen_take"):
                reply = (cmd_listen(str(body.get("folder", "")), body.get("voice", True) not in (False, "false", "False"), slot)
                         if self.path == "/api/listen" else cmd_listen_take())
                log(f"{self.path} {body} -> {reply['speech']}")
                return self._send(200, reply)
            if self.path == "/api/add":
                speech = cmd_add(item, who)
            elif self.path == "/api/remove":
                speech = cmd_remove(item, who)
            elif self.path == "/api/remove_id":
                speech = cmd_remove_id(str(body.get("folder", "")), str(body.get("id", "")))
            elif self.path == "/api/list":
                speech = cmd_list(who)
            else:
                return self._send(404, {"speech": "Unknown deck command."})
            log(f"{self.path} {body} -> {speech}")
            self._send(200, {"speech": speech})
        except Exception as e:  # noqa: BLE001
            log(f"error on {self.path}: {e!r}")
            self._send(200, {"speech": f"Something went wrong changing the deck: {e}"})

    def log_message(self, *a):  # quiet: we log our own lines
        pass


def spotify_token_ok() -> bool:
    try:
        return spotify_token() is not None
    except Exception:  # noqa: BLE001
        return False


def spotify_user_ok() -> bool:
    try:
        return user_token() is not None
    except Exception:  # noqa: BLE001
        return False


def setup_in_terminal() -> None:
    """python3 manager/deck_manager.py setup: paste the Spotify key and log in over SSH, no browser on this box."""
    import getpass
    hexkey = re.compile(r"[0-9a-fA-F]{32}")

    def ask(prompt: str, hidden: bool) -> str:
        while True:
            v = (getpass.getpass if hidden else input)(prompt).strip()
            if not v or hexkey.fullmatch(v):
                return v
            print("  That isn't it: the Client ID and secret are each 32 characters, 0-9 and a-f.")

    print("Spotify key for the Stream Deck (from developer.spotify.com/dashboard › your app › Settings).")
    print("Press Enter on a line to keep what's saved.")
    cid = ask(f"Client ID{' [saved]' if client_id() else ''}: ", hidden=False)
    secret = ask("Client secret (typing is hidden): ", hidden=True)
    if cid or secret:
        save_keys(cid, secret)
        print("Saved in streamdeck-yaml/manager/.env.")
    if not client_id():
        print("No Client ID saved, so stopping here.")
        return
    print(f"\nListen mode login. In the app's settings, the Redirect URIs must include {REDIRECT}")
    print("1. Open this link in any browser (your laptop or phone is fine) and approve:\n")
    print("   " + login_url() + "\n")
    print(f"2. The browser then goes to {REDIRECT}?code=… and shows an error page.")
    print("   That's expected: copy the whole address from the address bar and paste it here.")
    while True:
        back = input("Address: ").strip()
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(back).query))
        if "error" in q:
            print(f"Spotify says: {q['error']}. Nothing changed.")
            return
        if q.get("code") and q.get("state"):
            break
        print("  That address has no ?code=… in it. Paste the full address the browser ended up on.")
    try:
        finish_login(q["code"], q["state"])
    except Exception as e:  # noqa: BLE001
        print(f"Login failed: {e}")
        return
    r = subprocess.run(["systemctl", "--user", "restart", "deck-manager"], capture_output=True, text=True)
    print("Spotify connected. " + ("Restarted deck-manager; listen mode is on." if r.returncode == 0
                                   else "Now restart it: systemctl --user restart deck-manager"))


if __name__ == "__main__":
    load_env()
    if sys.argv[1:] == ["setup"]:
        sys.exit(setup_in_terminal())
    log(f"listening on 127.0.0.1:{PORT}; name search {'on' if spotify_token_ok() else 'off (links only)'}; "
        f"listen mode {'on' if spotify_user_ok() else f'needs login at http://127.0.0.1:{PORT}/spotify/login'}; "
        f"voice {'on' if whisper_ok() else f'off (no Whisper at {WHISPER_HOST})'}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
