#!/usr/bin/env bash
# Install the Stream Deck intercom into Home Assistant and start the deck manager.
#   bash ~/streamdeck-yaml/homeassistant/install.sh
# Then restart Home Assistant (Settings > System > ⋮ > Restart Home Assistant).
set -euo pipefail
D=$(cd "$(dirname "$0")/.." && pwd)
HA=$(python3 -c "import yaml,os;print(os.path.expanduser((yaml.safe_load(open('$D/settings.yaml')) or {}).get('ha_config','~/homeassistant/config')))")

# 1. Assist phrases and the packages, with this house's devices filled in from settings.yaml
mkdir -p "$HA/custom_sentences/en" "$HA/packages"
python3 "$D/tools/install_ha.py"

# 2. Turn on packages in configuration.yaml (once). The file was created root-owned by the
#    container, but the folder is ours, so write a new copy beside it and swap it in.
if ! grep -q "packages: !include_dir_named packages" "$HA/configuration.yaml"; then
  cp -p "$HA/configuration.yaml" "$HA/configuration.yaml.before-deck-intercom"
  { cat "$HA/configuration.yaml"; printf '\n# Bundles of config, e.g. packages/deck_intercom.yaml (Stream Deck intercom)\nhomeassistant:\n  packages: !include_dir_named packages\n'; } > "$HA/.configuration.yaml.new"
  mv "$HA/.configuration.yaml.new" "$HA/configuration.yaml"
  echo "configuration.yaml: packages enabled (backup: configuration.yaml.before-deck-intercom)"
fi

# 3. Sidebar dashboards: Stream Deck (the emulator, from www/) and Restart. build.py stamps the
#    emulator link with ?v=<build> afterwards, so these are copied without one.
mkdir -p "$HA/dashboards"
cp "$D/homeassistant/dashboards/stream_deck.yaml" "$D/homeassistant/dashboards/restart.yaml" "$HA/dashboards/"
if ! grep -q "dashboards/stream_deck.yaml" "$HA/configuration.yaml"; then
  cp -p "$HA/configuration.yaml" "$HA/configuration.yaml.before-deck-dashboards"
  { cat "$HA/configuration.yaml"; cat <<'YAML'

# Stream Deck dashboard in the sidebar: the emulator, served from www/ (copied there by
# emulator/build.py whenever it rebuilds).
lovelace:
  dashboards:
    stream-deck:
      mode: yaml
      title: Stream Deck
      icon: mdi:view-grid
      show_in_sidebar: true
      filename: dashboards/stream_deck.yaml
    restart-ha:
      mode: yaml
      title: Restart
      icon: mdi:restart
      show_in_sidebar: true
      require_admin: true
      filename: dashboards/restart.yaml
YAML
  } > "$HA/.configuration.yaml.new"
  mv "$HA/.configuration.yaml.new" "$HA/configuration.yaml"
  echo "configuration.yaml: dashboards added (backup: configuration.yaml.before-deck-dashboards)"
fi
python3 "$D/emulator/build.py" >/dev/null  # puts the page in www/ and stamps the dashboard link

# 4. The deck manager, as a service that starts with the computer
U=~/.config/systemd/user
mkdir -p "$U"
cat > "$U/deck-manager.service" <<EOF
[Unit]
Description=Stream Deck manager (Assist -> people folders)

[Service]
ExecStart=/usr/bin/python3 $D/manager/deck_manager.py
WorkingDirectory=$D
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable deck-manager.service >/dev/null
systemctl --user restart deck-manager.service
# keep user services (manager, emulator rebuilds) running without anyone logged in
loginctl enable-linger "$USER" 2>/dev/null || echo "Note: run 'sudo loginctl enable-linger $USER' so the manager runs before anyone logs in."
sleep 1
curl -fsS http://127.0.0.1:8765/health && echo
echo "Done. Now restart Home Assistant: Settings > System > ⋮ (top right) > Restart Home Assistant."
