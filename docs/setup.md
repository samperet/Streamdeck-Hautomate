# Setting it up

About half an hour, most of it in Home Assistant. You can try the deck in the browser emulator
first (steps 1–2) without any hardware.

## What you need

- **A Stream Deck**: the XL (32 keys) or the MK.2 / original (15 keys), plugged into…
- **a Linux computer with Docker** (a mini PC or laptop next to the speaker is ideal). The deck
  runs in a container; the tools are plain Python 3 with PyYAML (`sudo apt install python3-yaml`).
- **Home Assistant** with the **Spotify** integration (a Spotify Premium account). The steps below
  assume Home Assistant's config folder is on the same computer (e.g. Home Assistant in Docker);
  if it isn't, copy the files `tools/install_ha.py` writes yourself.
- **A speaker Spotify Connect can play to** that Home Assistant also sees as a `media_player`
  (a WiiM, Sonos, Chromecast, an Echo…). Volume, play/pause and the cover come from it.

Optional extras: a second speaker for sound effects, an LG webOS TV (TV page and karaoke), a
Jellyfin server (films), a microphone on the computer and a Whisper server (adding by voice).

## 1. Get the code and describe your house

```sh
git clone https://github.com/samperet/Streamdeck-Hautomate.git ~/Streamdeck-Hautomate
cd ~/Streamdeck-Hautomate
cp settings.example.yaml settings.yaml
```

Edit `settings.yaml`: at least `deck` (`xl` or `mk2`), `player` (your speaker's entity ID, from
Home Assistant › Settings › Entities) and `spotify_device` (its name in the Spotify app's device
list). Every key is explained in the file and in [configuration.md](configuration.md).

## 2. Build it, and try the emulator

```sh
python3 gen_config.py
```

This writes `configuration.yaml` (the deck's pages) and `emulator/streamdeck-emulator-local.html`.
The first build downloads the album covers, which takes a few minutes (Spotify slows down quick
requests; it retries). Open the page in a browser: in **Dev** mode the house is simulated, so you
can press everything.

## 3. Connect to Home Assistant

Make a long-lived token (your profile › Security › Long-lived access tokens) and put it in `.env`
next to `compose.yaml`:

```sh
HASS_HOST=localhost:8123
HASS_TOKEN=eyJ...
WEBSOCKET_PROTOCOL=ws
```

Then install the Home Assistant side (scripts for playing albums, volume, sleep, the TV, the games;
the Assist sentences; the Stream Deck and Restart dashboards; the deck manager service):

```sh
bash homeassistant/install.sh
```

It fills your devices from `settings.yaml` into the packages (`tools/install_ha.py`), turns on
`packages:` in Home Assistant's `configuration.yaml` (keeping a backup), and starts the deck
manager as a user service. **Restart Home Assistant** afterwards. Later changes to the packages
only need `python3 tools/install_ha.py` and *Developer tools › YAML › Scripts / Automations* reload.

## 4. Start the deck

```sh
docker compose up -d --build
docker compose logs -f          # watch it connect to Home Assistant and draw Home
```

The container needs USB access (`privileged`, plus `99-streamdeck.rules`). If the log says
**No Stream Deck found**, check `lsusb | grep 0fd9`: if the deck isn't listed there, reseat the
cable or try another port.

Changes to `settings.yaml` or the album lists: run `python3 gen_config.py`; the deck reloads its
configuration by itself. Changes to `visualizer/`: `docker compose restart`.

## 5. Optional

**Rebuild automatically.** `bash autobuild/install.sh` installs systemd user units that re-run
`gen_config.py` whenever `albums/`, `sounds/`, `karaoke/` or `gen_config.py` change.

**Adding albums and people's folders by voice.** The deck manager (`manager/deck_manager.py`,
started by `install.sh`) looks things up on Spotify:

1. Create an app at [developer.spotify.com](https://developer.spotify.com/dashboard) with the
   redirect URI `http://127.0.0.1:8765/spotify/callback`, and put its keys in `manager/.env`:
   ```sh
   SPOTIFY_CLIENT_ID=...
   SPOTIFY_CLIENT_SECRET=...
   ```
2. Open `http://127.0.0.1:8765/spotify/login` on the computer once (listen mode watches what you
   play). Over SSH: `python3 manager/deck_manager.py setup`.
3. For dictating into a held key, run a Whisper server (e.g. `rhasspy/wyoming-whisper`) on port
   10300 and have a microphone on the computer (`arecord` must work).

Then "add Kind of Blue by Miles Davis to Alex" works in Assist, and holding an empty key on a
person's page lets you say what to add.

**Sound effects** play on `sfx_speaker` while the music keeps going: drop `.mp3`/`.wav` files into
`sounds/` (Home Assistant serves them from `www/sfx/`).

**The TV.** `tv` is an LG webOS TV in Home Assistant. Turn on *Settings › General › Devices ›
External Devices › TV On With Mobile › Turn on via Wi-Fi* on the TV and set `tv_mac` and
`broadcast` for Wake-on-LAN. Films need `jellyfin_url` and `jellyfin_api_key` in Home Assistant's
`secrets.yaml`. Karaoke opens YouTube videos from `karaoke/songs.csv` in the TV's YouTube app.

**The font.** See [fonts/README.md](../fonts/README.md) to use Chicago, as in the photos.

## Troubleshooting

- **Albums don't play / "Spotify can't see the speaker"**: pick the speaker once in the Spotify
  app so Spotify Connect knows it, and check `spotify_device` matches its name there.
- **The emulator's Live mode can't connect**: from `file://` it uses the token in `.env`; inside
  Home Assistant (sidebar › Stream Deck) it uses your login.
- **`albums/add.py` stops with "rate-limiting this app"**: Spotify blocked the app's key for a
  while (it can be a day) after too many quick requests. Everything added so far is saved; run the
  same list again later, one list at a time.
