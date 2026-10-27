#!/usr/bin/env bash
# Keep emulator/streamdeck-emulator-local.html up to date: rebuild it whenever the
# albums, gen_config.py, configuration.yaml or the emulator's own files change.
# Runs as your user (no sudo). Install:  bash ~/streamdeck-yaml/autobuild/install.sh
# Remove:  systemctl --user disable --now deck-config-gen.path deck-emulator-build.path
set -euo pipefail
D=$(cd "$(dirname "$0")/.." && pwd)
U=~/.config/systemd/user
mkdir -p "$U"

# albums/*.json, sounds/, karaoke/ or gen_config.py changed -> regenerate configuration.yaml (which also rebuilds the page)
cat > "$U/deck-config-gen.path" <<EOF
[Unit]
Description=Watch the Stream Deck album lists and generator

[Path]
PathChanged=$D/albums
PathChanged=$D/sounds
PathChanged=$D/karaoke
PathChanged=$D/gen_config.py

[Install]
WantedBy=default.target
EOF
cat > "$U/deck-config-gen.service" <<EOF
[Unit]
Description=Regenerate the Stream Deck configuration and emulator

[Service]
Type=oneshot
WorkingDirectory=$D
ExecStart=/usr/bin/python3 $D/gen_config.py
EOF

# configuration.yaml edited directly, or the emulator template/builder changed -> rebuild the page
cat > "$U/deck-emulator-build.path" <<EOF
[Unit]
Description=Watch the Stream Deck configuration and emulator sources

[Path]
PathChanged=$D/configuration.yaml
PathChanged=$D/emulator/template.html
PathChanged=$D/emulator/build.py
PathChanged=$D/.env

[Install]
WantedBy=default.target
EOF
cat > "$U/deck-emulator-build.service" <<EOF
[Unit]
Description=Rebuild the Stream Deck emulator page

[Service]
Type=oneshot
WorkingDirectory=$D
ExecStart=/usr/bin/python3 $D/emulator/build.py
EOF

systemctl --user daemon-reload
systemctl --user enable --now deck-config-gen.path deck-emulator-build.path
echo "Watching. The page rebuilds a second or two after a change; refresh the browser to see it."
