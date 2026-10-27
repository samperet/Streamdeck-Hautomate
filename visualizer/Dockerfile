# The Stream Deck YAML image plus the microphone library for the visualizer.
# Build and start with:  sudo docker compose -f ~/streamdeck-yaml/compose.yaml up -d --build
FROM basnijholt/home-assistant-streamdeck-yaml:latest
RUN micromamba install --name base --channel conda-forge --yes python-sounddevice numpy && \
    micromamba clean --all --yes
# The base image ships streamdeck 0.9.5, which doesn't know newer decks' USB IDs (the old
# MK.2 with scissor keys, 0fd9:00a5, reported "No Stream Deck found"); 0.10.0 also knows the XL.
RUN /opt/conda/bin/python3 -m pip install --no-cache-dir --no-deps streamdeck==0.10.0
