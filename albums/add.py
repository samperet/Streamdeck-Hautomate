#!/usr/bin/env python3
"""Add albums to a genre list by name, with checked Spotify IDs.

    python3 albums/add.py <genre> < list.tsv        # lines "Artist<TAB>Album title"

Searches Spotify's catalogue (client credentials from manager/.env) and keeps a result only when
its artist matches and its title is the album asked for; albums already in albums/<genre>.json are
skipped. New ones are appended to the list, and their covers go straight into the emulator's cache
(emulator/.cache/sp-<id>.json/.jpg, as emulator/build.py stores oEmbed answers), so the build and the
deck (visualizer/supervisor.py) don't have to fetch them from the rate-limited oEmbed endpoint.
Prints one line per input: "+" added, "=" already there, "?" not found (fix the name and rerun).
The list is saved after every album. Spotify can rate-limit the app for a day: then this stops,
saying when to try again, rather than waiting; rerunning the same input skips what's in already.
albums/pending/ holds curated lists still to run.
"""
import base64
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE.parent / "emulator/.cache"


def env():
    for line in (HERE.parent / "manager/.env").read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip() and v.strip():
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def token():
    auth = base64.b64encode(f"{os.environ['SPOTIFY_CLIENT_ID']}:{os.environ['SPOTIFY_CLIENT_SECRET']}".encode()).decode()
    req = urllib.request.Request("https://accounts.spotify.com/api/token", b"grant_type=client_credentials",
                                 {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"})
    return json.load(urllib.request.urlopen(req, timeout=15))["access_token"]


class RateLimited(Exception):
    pass


def get(url, tok):
    for _ in range(6):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"}), timeout=15))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            wait = int(e.headers.get("Retry-After", "5"))
            if wait > 120:  # a long block (it can be a day): stop instead of hanging
                raise RateLimited(wait) from None
            time.sleep(wait + 1)
    raise RateLimited(60)


def norm(s):
    s = re.sub(r"[\u2010-\u2015]", " ", s)  # Spotify's "Run–D.M.C.": dashes the ASCII step would drop
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\s*[\(\[].*?[\)\]]", "", s)            # (Remastered 2009), [Deluxe]
    s = re.sub(r"\s+-\s+.*(remaster|edition|version|deluxe|anniversary).*$", "", s)
    s = re.sub(r"^the\s+|&|\band\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def same_title(want, got):
    w, g = norm(want), norm(got)
    if not w or not g:
        return False
    if w == g or g.startswith(w + " ") or w.startswith(g + " "):
        return True
    ws, gs = set(w.split()), set(g.split())
    return len(ws & gs) / max(len(ws), 1) >= 0.8 and len(gs) <= len(ws) + 3


def same_artist(want, artists):
    w = norm(want)
    return any(norm(a["name"]) == w or (len(w) > 3 and (w in norm(a["name"]) or norm(a["name"]) in w)) for a in artists)


def find(artist, title, tok):
    for q in (f'album:"{title}" artist:"{artist}"', f"{title} {artist}"):
        url = "https://api.spotify.com/v1/search?" + urllib.parse.urlencode({"q": q, "type": "album", "limit": 10, "market": "US"})
        items = [a for a in get(url, tok)["albums"]["items"] if a and same_artist(artist, a["artists"]) and same_title(title, a["name"])]
        if items:  # a studio album over a compilation or single; the closest title, then the plainest edition
            items.sort(key=lambda a: (a["album_type"] != "album", norm(a["name"]) != norm(title),
                                      bool(re.search(r"deluxe|expanded|anniversary|super|live", a["name"], re.I)), len(a["name"])))
            return items[0]
        time.sleep(0.15)
    return None


def main():
    genre = sys.argv[1]
    path = HERE / f"{genre}.json"
    albums = json.loads(path.read_text()) if path.exists() else []
    have = {a["id"] for a in albums} | {(norm(a["artist"]), norm(a["title"])) for a in albums}
    env()
    tok = token()

    def save():
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(albums, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(path)

    try:
        added = run(albums, have, tok, save)
    except RateLimited as e:
        print(f"Spotify is rate-limiting this app for {int(e.args[0]) // 3600} h {int(e.args[0]) % 3600 // 60} min; "
              f"what was added so far is saved. Run the same list again later.", file=sys.stderr)
        sys.exit(2)
    print(f"{genre}: {added} added, {len(albums)} in all", file=sys.stderr)


def run(albums, have, tok, save):
    added = 0
    for line in sys.stdin:
        if not line.strip() or line.startswith("#"):
            continue
        artist, _, title = (part.strip() for part in line.rstrip("\n").partition("\t"))
        if (norm(artist), norm(title)) in have:
            print(f"=\t{artist}\t{title}")
            continue
        a = find(artist, title, tok)
        time.sleep(0.15)
        if a is None:
            print(f"?\t{artist}\t{title}")
            continue
        if a["id"] in have:
            print(f"=\t{artist}\t{title}\t(as {a['name']})")
            continue
        have |= {a["id"], (norm(artist), norm(title))}
        albums.append({"artist": artist, "title": title, "id": a["id"], "spotify_title": a["name"]})
        save()
        added += 1
        print(f"+\t{artist}\t{title}\t{a['id']}\t{a['name']}")
        img = next((i["url"] for i in a.get("images", []) if i.get("width") == 300), (a.get("images") or [{}])[0].get("url"))
        CACHE.mkdir(exist_ok=True)
        meta = CACHE / f"sp-{a['id']}.json"
        if img and not meta.exists():
            try:
                (CACHE / f"sp-{a['id']}.jpg").write_bytes(urllib.request.urlopen(img, timeout=15).read())
                meta.write_text(json.dumps({"title": a["name"], "thumbnail_url": img}))
            except OSError as e:
                print(f"  (cover not cached: {e})", file=sys.stderr)
    return added


if __name__ == "__main__":
    main()
