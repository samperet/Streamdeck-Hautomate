# Fonts

The keys use the Stream Deck library's default font (Roboto) unless you put a font here.

The deck in the photos uses **Chicago**, the classic Macintosh system font. Its revivals aren't
clearly licensed for redistribution, so it isn't included. To use it, find a copy you're allowed
to use (for example *ChicagoFLF*), save it here as `fonts/ChicagoFLF.ttf`, then
`docker compose restart` and `python3 emulator/build.py`.

`compose.yaml` mounts this folder into the deck container at `/fonts`; `visualizer/deck_run.py`
uses `/fonts/ChicagoFLF.ttf` for every key when it exists, and `emulator/build.py` embeds it in
the emulator page. Chicago has no minus sign, which is why the volume key reads "Vol –" with an
en dash.
