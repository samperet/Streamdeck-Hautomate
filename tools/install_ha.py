#!/usr/bin/env python3
"""Copy the Home Assistant packages and Assist sentences into Home Assistant's config folder,
filling in this house's devices.

    python3 tools/install_ha.py          # then reload scripts/automations, or restart Home Assistant

homeassistant/*.yaml are templates: "@@name@@" is replaced by `name` from settings.yaml (see
settings.example.yaml), e.g. @@player@@ -> media_player.living_room. homeassistant/deck.yaml (the
Assist sentences) goes to custom_sentences/en/, every other deck_*.yaml to packages/.
Also run by homeassistant/install.sh.
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TOKEN = re.compile(r"@@(\w+)@@")


def settings() -> dict:
    f = ROOT / "settings.yaml"
    if not f.exists():
        sys.exit("No settings.yaml: copy settings.example.yaml to settings.yaml and fill it in.")
    return yaml.safe_load(f.read_text()) or {}


def people_list(s: dict) -> str:
    """The Assist list of people (homeassistant/deck.yaml): each name, its nicknames and their
    possessives, e.g. in "(alex|alex's|alexs|al|al's|als)" out "Alex"."""
    out = []
    for p in s.get("people") or []:
        words = [p[0], p[1].lower(), *(n.lower() for n in (p[2] if len(p) > 2 else []))]
        forms = dict.fromkeys(f for w in words for f in (w, f"{w}'s", f"{w}s"))
        out.append(f'      - in: "({"|".join(forms)})"\n        out: "{p[1]}"')
    return "\n".join(out) or '      - in: "(nobody)"\n        out: "Nobody"'


def render(text: str, s: dict, name: str) -> str:
    s = s | {"people_list": people_list(s)}
    missing = sorted({m for m in TOKEN.findall(text) if s.get(m) in (None, "")})
    if missing:
        print(f"  {name}: no {', '.join(missing)} in settings.yaml; those parts won't work", file=sys.stderr)
    return TOKEN.sub(lambda m: str(s.get(m.group(1)) or ""), text)


def main():
    s = settings()
    ha = Path(s.get("ha_config", "~/homeassistant/config")).expanduser()
    if not (ha / "configuration.yaml").exists():
        sys.exit(f"{ha} doesn't look like Home Assistant's config folder (set ha_config in settings.yaml).")
    for src in sorted((ROOT / "homeassistant").glob("*.yaml")):
        dst = ha / ("custom_sentences/en" if src.name == "deck.yaml" else "packages") / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(render(src.read_text(), s, src.name))
        print(f"  {dst.relative_to(ha)}")


if __name__ == "__main__":
    main()
