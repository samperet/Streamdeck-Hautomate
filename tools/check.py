#!/usr/bin/env python3
"""Sanity-check the deck before committing:  python3 tools/check.py

Checks the generated configuration.yaml (every page the same size: 32 keys on an XL, 15 on the 15-key deck; unique page names, every
go-to-page target exists, key images exist, Spotify IDs well formed), the album and
people lists, and that the emulator's script still parses (with node, if installed).
Exits 1 on any problem. Runs as the git pre-commit hook (.githooks/pre-commit).
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
SPOTIFY_ID = re.compile(r"[A-Za-z0-9]{22}")
problems = []


def bad(msg):
    problems.append(msg)


def check_config():
    cfg = yaml.safe_load((HERE / "configuration.yaml").read_text())
    pages = cfg.get("pages", []) + cfg.get("anonymous_pages", [])
    names = [p["name"] for p in pages]
    for n in {n for n in names if names.count(n) > 1}:
        bad(f"configuration.yaml: page name used twice: {n!r}")
    known = set(names)
    size = len(pages[0].get("buttons", [])) if pages else 32
    if size not in (15, 32):
        bad(f"page {pages[0]['name']!r}: {size} keys; a deck has 32 (XL) or 15")
    for p in pages:
        keys = p.get("buttons", [])
        if len(keys) != size:
            bad(f"page {p['name']!r}: {len(keys)} keys, not {size}")
        for i, b in enumerate(keys):
            where = f"page {p['name']!r} key {i}"
            for action in (b, b.get("long_press") or {}):
                if action.get("special_type") == "go-to-page" and action.get("special_type_data") not in known:
                    bad(f"{where}: goes to missing page {action.get('special_type_data')!r}")
            icon = b.get("icon") or ""
            if icon.startswith("spotify:"):
                m = re.fullmatch(r"spotify:(album|playlist|artist|track|episode)/(.+)", icon)
                if not m or not SPOTIFY_ID.fullmatch(m.group(2)):
                    bad(f"{where}: malformed Spotify icon {icon!r}")
            elif icon.startswith("/icons/") and not (HERE / icon.lstrip("/")).exists():
                bad(f"{where}: missing key image {icon}")
    return len(pages)


def check_lists():
    count = 0
    for f in sorted((HERE / "albums").glob("*.json")) + sorted((HERE / "people").glob("*.json")):
        rel = f.relative_to(HERE)
        try:
            items = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            bad(f"{rel}: not valid JSON ({e})")
            continue
        seen = set()
        for it in items:
            sid = it.get("id", "")
            if not SPOTIFY_ID.fullmatch(sid):
                bad(f"{rel}: bad Spotify ID {sid!r} ({it.get('title')})")
            if sid in seen:
                bad(f"{rel}: {it.get('title')!r} is listed twice")
            seen.add(sid)
        count += len(items)
    return count


def check_emulator_script():
    if not shutil.which("node"):
        return "skipped (no node)"
    html = (HERE / "emulator" / "template.html").read_text()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as t:
        t.write("\n".join(scripts))
    r = subprocess.run(["node", "--check", t.name], capture_output=True, text=True)
    Path(t.name).unlink()
    if r.returncode:
        bad("emulator/template.html: script doesn't parse:\n" + r.stderr.strip()[-600:])
    return "ok"


if __name__ == "__main__":
    n_pages = check_config()
    n_items = check_lists()
    js = check_emulator_script()
    if problems:
        print("\n".join("✗ " + p for p in problems))
        sys.exit(1)
    print(f"✓ {n_pages} pages, {n_items} albums/playlists in lists, emulator script {js}")
