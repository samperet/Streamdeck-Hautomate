#!/usr/bin/env python3
"""Check Spotify album IDs: python3 verify.py ID [ID ...] -> prints 'ID<TAB>title<TAB>artist' or 'ID<TAB>INVALID'."""
import json, re, sys, urllib.request
for sid in sys.argv[1:]:
    sid = re.sub(r".*album[/:]", "", sid).split("?")[0]
    try:
        d = json.load(urllib.request.urlopen(f"https://open.spotify.com/oembed?url=https://open.spotify.com/album/{sid}", timeout=15))
        html = urllib.request.urlopen(urllib.request.Request(f"https://open.spotify.com/album/{sid}", headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read().decode("utf8", "replace")
        m = re.search(r'<meta property="og:description" content="([^"]*)"', html)
        print(f"{sid}\t{d['title']}\t{(m.group(1) if m else '').split(' · ')[0]}")
    except Exception:
        print(f"{sid}\tINVALID")
