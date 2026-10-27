# How it works

```
settings.yaml ─┐
albums/*.json ─┼─► gen_config.py ─► configuration.yaml ─► deck container (compose.yaml)
people/*.json ─┤                                            home-assistant-streamdeck-yaml
sounds/, karaoke/ ┘                                          + visualizer/deck_run.py
                 └─► emulator/build.py ─► emulator/streamdeck-emulator-local.html ─► Home Assistant www/

homeassistant/*.yaml ─► tools/install_ha.py ─► Home Assistant packages/ and custom_sentences/
manager/deck_manager.py ◄── Assist ("add … to Alex") and the deck (hold an empty key)
```

## The pieces

**`gen_config.py`** is the deck's layout, in code. It reads `settings.yaml` and the lists and writes
`configuration.yaml` for [home-assistant-streamdeck-yaml](https://github.com/basnijholt/home-assistant-streamdeck-yaml):
every page, key, icon and Home Assistant action. Never edit `configuration.yaml` by hand. Pages are
32 keys on the XL and 15 on the smaller deck; `tools/check.py` checks the result (it's also the git
pre-commit hook).

**The deck container** (`compose.yaml`, `visualizer/Dockerfile`) is the upstream image plus a
microphone library. `visualizer/supervisor.py` runs the deck and swaps in the visualizer when
`input_boolean.deck_visualizer` turns on (More › Visualizer). `visualizer/deck_run.py` patches the
library at start-up to add:

- **The Now Playing panel**: a key whose icon is `tile:<n>:<cover url>` shows the n-th ninth of the
  cover. The cover is scaled to span the 3×3 block *including* the gaps between keys and each key
  shows what's behind it, so the picture isn't stretched across the bezels.
- **The scrolling line**: three keys with `marquee:<n>:<artist>\n<song>\n<state>` draw one strip
  across the three keys, gaps included, so letters line up, and redraw it ~14 times a second.
  Each also has a small icon for what it does (Vol –, play/pause, Vol +).
- **Volume**: keys running `script.deck_volume` are handled on the deck, not in Home Assistant, so
  they can repeat and count taps. A tap steps by the level (1% under 15%, then 2, 3, 5%); quick taps
  build up to 5% then 10%; a slower tap, or a tap the other way, fine-tunes in 1%; holding slides
  to 0 or 100 in three seconds. It keeps its own running level (the speaker's state lags behind
  quick steps) and draws the level across the cover while it changes.
- **Navigation**: pages with the panel or the scrolling line stay open after a press (the library
  closes pop-up pages); tapping an album opens Now Playing, whose Back returns to that album page;
  More › Random picks any album on the deck; after a minute without a press, back to Home.
- **People's folders**: hold an empty key to listen (voice, then Spotify playback); hold a cover
  2.5 s and tap again to delete it. Both talk to the deck manager.
- **Fonts**: if `fonts/ChicagoFLF.ttf` exists, every key uses it.

**Home Assistant** (`homeassistant/`) does the house's side, as packages:

| File | What it does |
|---|---|
| `deck_intercom.yaml` | `script.play_spotify_album` (finds the speaker in Spotify Connect, takes it off other inputs, plays, sets a starting volume), listen mode, the Assist intents for people's folders |
| `deck_volume.yaml` | `script.deck_volume`: one step, for anything other than the deck |
| `deck_sleep.yaml` | the sleep timer and the automation that turns everything off |
| `deck_sfx.yaml` | sound effects on a second speaker |
| `deck_tv.yaml` | the TV: Wake-on-LAN, films from Jellyfin, karaoke in YouTube |
| `deck_games.yaml` | Lights Out, kept in an `input_text` |
| `deck.yaml` | Assist sentences ("add … to …") |

They're templates: `@@player@@` and the like are filled from `settings.yaml` by `tools/install_ha.py`.

**The deck manager** (`manager/deck_manager.py`, a user service on 127.0.0.1:8765) edits people's
folders: Assist's requests, recording and transcribing (Whisper) while a key is held, and watching
Spotify for what you start. Then it re-runs `gen_config.py`.

**The emulator** (`emulator/template.html`, built by `emulator/build.py`) renders the same
`configuration.yaml` in a browser. It mirrors the deck's own drawing (the scrolling line, the
cover tiles, the volume overlay, Lights Out, the visualizer from the computer's microphone), so
behaviour changes go in both `visualizer/` and `emulator/template.html`.

## Repository map

```
gen_config.py            the layout
settings.example.yaml    your house (copy to settings.yaml)
albums/                  genre lists, add.py, verify.py, pending lists
people/                  people's folders
homeassistant/           Home Assistant packages (templates), install.sh, dashboards
visualizer/              the deck container: deck_run.py, supervisor.py, visualizer.py
manager/                 the deck manager (voice and listen mode)
emulator/                the browser emulator and its builder
tools/                   check.py, install_ha.py, export_public.py
icons/, fonts/, sounds/, karaoke/
autobuild/               systemd user units that rebuild on changes
```
