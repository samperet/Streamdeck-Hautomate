# Configuration

Everything about your house lives in `settings.yaml` (copied from `settings.example.yaml`, and
git-ignored). The album lists and people's folders are JSON files you can edit by hand or with the
tools. After a change, run `python3 gen_config.py` (or let the autobuild units do it); after
changing the devices, also `python3 tools/install_ha.py`.

## settings.yaml

| Key | What it is |
|---|---|
| `deck` | `xl` (Stream Deck XL, 8×4) or `mk2` (Stream Deck MK.2 or original, 5×3). |
| `player` | The speaker albums play on: a Home Assistant `media_player`. |
| `spotify_device` | That speaker's name in Spotify Connect. |
| `spotify_device_id` | Optional: its Connect device ID, used if its name sometimes comes up blank. |
| `sources` | More › Amp: `[name in the speaker's source_list, key label]` pairs. |
| `lan_probe_ip` | Any device on your LAN (your router); only used to work out this computer's address. |
| `ha_config` | Home Assistant's config folder. |
| `sfx_speaker` | A second speaker that plays sound effects over the music. |
| `tv`, `tv_label` | An LG webOS TV and its label on More. Leave `tv` empty to drop More › TV and Karaoke. |
| `tv_mac`, `broadcast` | Wake-on-LAN for the TV. |
| `jellyfin_url`, `films` | Films on More › TV: `[name in Jellyfin, key label]`. |
| `people` | `[file in people/, name on the key, [other names Assist may hear]]`. |
| `person_genre` | e.g. `{alex: jazz}`: that genre's key sits on Alex's page instead of Home. |
| `top` | `[file in albums/, label]`: a ranked list with its own key at the end of Home's first row. |
| `genres` | `[file in albums/, label]` in Home's order. Home holds 14 on the XL and 9 on the 15-key deck; the rest go on More. Use `"\n"` in a label to break it over two lines. |

## Layouts

Both decks have the same pages; the 15-key deck is a tighter fit.

| Page | Stream Deck XL (8×4) | 15 keys (5×3) |
|---|---|---|
| Home | Genres in the left 5 columns; the cover across the right 3×3; People, More, Prev, Next, Mute below; the scrolling line (Vol –, play/pause, Vol +) under the cover | Two rows of genres; People, More and the scrolling line |
| A genre | 24 covers; Back, ‹ ›, and the scrolling line | 10 covers; Back, Vol – / +, ‹ › |
| Now Playing (after tapping an album) | The cover big, Back, Prev, Play, Next, Mute | The cover across the right 3×3; Back, the player and volume beside it |
| More | Amp, Sleep, Visualizer, TV, Karaoke, Sound Effects, Lights Out, Random, then extra genres | The same, flowing over three rows |

## Albums and genres

A genre is `albums/<file>.json`, a list of albums in the order they appear:

```json
[
 {"artist": "Miles Davis", "title": "Kind of Blue", "id": "1weenld61qoidwYuZ1GESA", "spotify_title": "Kind Of Blue"}
]
```

`id` is the Spotify album ID (the part after `/album/` in a share link). The easy way to add albums
is by name:

```sh
printf 'Nina Simone\tPastel Blues\nBill Evans Trio\tSunday at the Village Vanguard\n' | python3 albums/add.py jazz
```

Each line is `Artist<TAB>Album title`. `add.py` searches Spotify (it needs the Spotify keys in
`manager/.env`), keeps a result only when the artist and title match, prefers the standard edition
over deluxe and anniversary ones, skips albums already in the list and caches the cover. It prints
`+` added, `=` already there, `?` not found. Check what it added: `spotify_title` is the name it
matched. Spotify blocks an app's key for a long time after a burst of requests, so run one list at
a time. `albums/pending/` holds lists ready to run.

To add a genre: create `albums/<file>.json` (or run `add.py <file>` with a new name) and add
`[file, Label]` to `genres`.

`albums/verify.py ID…` prints the title and artist Spotify has for album IDs.

## People's folders

Each person in `people` gets `people/<file>.json`: albums, playlists and artists, in the same
format plus a `kind` (`album`, `playlist`, `artist`, `track`) and an optional `slot` (the key it was
added on). They're usually edited without touching the file:

- **By voice**, through Home Assistant Assist: "add Kind of Blue by Miles Davis to Alex", "add the
  playlist Sunday Morning to Riley's folder", "remove the last one from Alex", "what's in Riley's folder".
- **On the deck**: hold an empty key on someone's page and say what to add, or keep holding and
  start something on Spotify: the album or playlist it plays from lands on that key.
- **Removing**: hold a cover 2.5 s, let go, and tap it again within 5 s.

## Sound effects, karaoke, films

- **Sound effects**: audio files in `sounds/` (mp3, wav, m4a, aac, ogg, flac), one key each on
  More › Sound Effects, named after the file. `tools/make_glasses_sound.py` synthesizes the
  example clinking glasses.
- **Karaoke**: `karaoke/songs.csv` with columns `Rank,Song,Artist,Category,Karaoke Video`; the last
  is a YouTube link with a `v=` video ID. Keys play it on the TV; Stop closes YouTube.
- **Films**: `films` in settings; each key turns the TV on and plays that title from Jellyfin.

## The emulator

`python3 emulator/build.py` (run by `gen_config.py`) writes `emulator/streamdeck-emulator-local.html`,
one offline file with every cover, icon and font inside, and copies it into Home Assistant's
`www/` for the sidebar dashboard. **Dev** mode simulates the house; **Live** talks to Home
Assistant over its WebSocket, using the token in `.env` from `file://` or your login inside Home
Assistant.
