#!/usr/bin/env python3
"""Run the normal Stream Deck config, and hand the deck to the visualizer on request.

Only one program can hold the Stream Deck at a time, so this runs
home-assistant-streamdeck-yaml as a child process and watches the Home Assistant
toggle input_boolean.deck_visualizer (created on first run):

  toggle on   -> stop the normal deck, run the microphone visualizer
  any key, the toggle turned off, or 10 minutes of quiet
              -> stop the visualizer, start the normal deck again

Settings come from the same .env as the deck: HASS_HOST, HASS_TOKEN,
WEBSOCKET_PROTOCOL; optional VISUALIZER_ENTITY, AUDIO_DEVICE, VISUALIZER_SILENCE_MINUTES.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import shutil
import signal
import threading
import time

import websockets

import visualizer

ENTITY = os.environ.get("VISUALIZER_ENTITY", "input_boolean.deck_visualizer")
DECK_CMD = ["python", os.path.join(os.path.dirname(os.path.abspath(__file__)), "deck_run.py")]  # the deck + push-to-talk
CONFIG = os.environ.get("STREAMDECK_CONFIG", "/app/configuration.yaml")
SILENCE_MIN = float(os.environ.get("VISUALIZER_SILENCE_MINUTES", "10"))
# Covers emulator/build.py already downloaded (sp-<id>.jpg, mounted by compose.yaml), and
# where home-assistant-streamdeck-yaml looks before fetching a `spotify:` icon itself.
COVERS = os.environ.get("DECK_COVERS", "/covers")
ASSETS = "/opt/home-assistant-streamdeck-yaml/assets"


def log(msg: str) -> None:
    print(f"[visualizer-supervisor] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def deck_brightness() -> int:
    try:
        import yaml
        with open(CONFIG) as f:
            return int(yaml.safe_load(f).get("brightness", 80))
    except Exception:  # noqa: BLE001
        return 80


def seed_covers() -> None:
    """Copy cached album covers in, so pages don't wait on Spotify the first time they show.

    The deck keeps the covers it downloads in its assets folder, which is lost whenever the
    container is recreated; its file for `spotify:album/ID` is spotify_album_ID.jpeg.
    """
    try:
        with open(CONFIG) as f:
            icons = set(re.findall(r"icon: spotify:(\w+)/(\w+)", f.read()))
    except OSError:
        return
    copied = 0
    for kind, sid in icons:
        src = os.path.join(COVERS, f"sp-{sid}.jpg")
        dst = os.path.join(ASSETS, f"spotify_{kind}_{sid}.jpeg")
        if os.path.exists(dst) or not os.path.isfile(src) or os.path.getsize(src) == 0:
            continue
        shutil.copyfile(src, dst)
        copied += 1
    if copied:
        log(f"pre-loaded {copied} album covers")


def open_deck(timeout: float = 5.0):
    """Open the first Stream Deck, waiting briefly for the previous owner to let go of it."""
    from StreamDeck.DeviceManager import DeviceManager

    end = time.monotonic() + timeout
    while True:
        try:
            for deck in DeviceManager().enumerate():
                if deck.is_visual():
                    deck.open()
                    deck.reset()
                    return deck
            raise RuntimeError("no Stream Deck found")
        except Exception as e:  # noqa: BLE001
            if time.monotonic() > end:
                raise RuntimeError(f"could not open the Stream Deck: {e}") from e
            time.sleep(0.3)


class Supervisor:
    def __init__(self) -> None:
        self.child: asyncio.subprocess.Process | None = None
        self.visualizing = False
        self.stop_vis = threading.Event()
        self.ws = None
        self.ids = itertools.count(1)
        self.shutdown = asyncio.Event()

    # ── the normal deck ──
    async def start_deck(self) -> None:
        if self.child is None or self.child.returncode is not None:
            log("starting the normal deck")
            seed_covers()
            self.child = await asyncio.create_subprocess_exec(*DECK_CMD)

    async def stop_deck(self) -> None:
        if self.child and self.child.returncode is None:
            log("pausing the normal deck")
            self.child.terminate()
            try:
                await asyncio.wait_for(self.child.wait(), 8)
            except asyncio.TimeoutError:
                self.child.kill()
                await self.child.wait()

    async def keep_deck_running(self) -> None:
        """Restart the normal deck if it exits on its own (e.g. the deck was unplugged)."""
        while not self.shutdown.is_set():
            if not self.visualizing and self.child and self.child.returncode is not None:
                log(f"normal deck exited ({self.child.returncode}); restarting in 5 s")
                await asyncio.sleep(5)
                if not self.visualizing:
                    await self.start_deck()
            await asyncio.sleep(1)

    # ── the visualizer ──
    async def visualize(self) -> None:
        if self.visualizing:
            return
        self.visualizing = True
        self.stop_vis.clear()
        try:
            await self.stop_deck()
            deck = await asyncio.to_thread(open_deck)
            try:
                log("visualizer on")
                why = await asyncio.to_thread(visualizer.run, deck, self.stop_vis, deck_brightness(),
                                              SILENCE_MIN, None, log)
                log(f"visualizer off ({why})")
            finally:
                deck.close()
        except Exception as e:  # noqa: BLE001
            log(f"visualizer failed: {e}")
        finally:
            await self.call("input_boolean", "turn_off", ENTITY)
            self.visualizing = False
            if not self.shutdown.is_set():
                await self.start_deck()

    # ── Home Assistant ──
    async def call(self, domain: str, service: str, entity_id: str) -> None:
        if self.ws is None:
            return
        try:
            await self.ws.send(json.dumps({"id": next(self.ids), "type": "call_service", "domain": domain,
                                           "service": service, "target": {"entity_id": entity_id}}))
        except Exception as e:  # noqa: BLE001
            log(f"could not call {domain}.{service}: {e}")

    async def request(self, ws, payload: dict) -> dict:
        """Send a command and wait for its result (only used before subscribing)."""
        payload["id"] = i = next(self.ids)
        await ws.send(json.dumps(payload))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == i:
                return msg

    async def ensure_toggle(self, ws) -> None:
        states = (await self.request(ws, {"type": "get_states"})).get("result") or []
        found = next((s for s in states if s["entity_id"] == ENTITY), None)
        if found is None and ENTITY == "input_boolean.deck_visualizer":
            r = await self.request(ws, {"type": "input_boolean/create", "name": "Deck visualizer",
                                        "icon": "mdi:equalizer"})
            log("created input_boolean.deck_visualizer" if r.get("success") else f"could not create the toggle: {r}")
        elif found is None:
            log(f"{ENTITY} does not exist in Home Assistant; create it as a Toggle helper")
        elif found["state"] == "on" and not self.visualizing:
            await self.call("input_boolean", "turn_off", ENTITY)  # left on from before a restart

    async def home_assistant(self) -> None:
        url = f"{os.environ.get('WEBSOCKET_PROTOCOL', 'ws')}://{os.environ['HASS_HOST']}/api/websocket"
        while not self.shutdown.is_set():
            try:
                async with websockets.connect(url, max_size=None) as ws:
                    await ws.recv()  # auth_required
                    await ws.send(json.dumps({"type": "auth", "access_token": os.environ["HASS_TOKEN"]}))
                    auth = json.loads(await ws.recv())
                    if auth.get("type") != "auth_ok":
                        log(f"Home Assistant refused the token ({auth.get('message', auth.get('type'))}); retrying in 60 s")
                        await asyncio.sleep(60)
                        continue
                    await self.ensure_toggle(ws)
                    sub = await self.request(ws, {"type": "subscribe_trigger", "trigger": {
                        "platform": "state", "entity_id": ENTITY}})
                    if not sub.get("success"):
                        raise RuntimeError(f"subscribe failed: {sub}")
                    self.ws = ws
                    log(f"watching {ENTITY}")
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") != "event" or msg.get("id") != sub["id"]:
                            continue
                        to = ((msg["event"].get("variables") or {}).get("trigger") or {}).get("to_state") or {}
                        if to.get("state") == "on":
                            asyncio.create_task(self.visualize())
                        elif to.get("state") == "off":
                            self.stop_vis.set()
            except Exception as e:  # noqa: BLE001
                log(f"Home Assistant connection: {e}; reconnecting in 10 s")
            self.ws = None
            await asyncio.sleep(10)

    async def main(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.shutdown.set)
        await self.start_deck()
        tasks = [asyncio.create_task(self.home_assistant()), asyncio.create_task(self.keep_deck_running())]
        await self.shutdown.wait()
        log("shutting down")
        self.stop_vis.set()
        for t in tasks:
            t.cancel()
        await self.stop_deck()


if __name__ == "__main__":
    asyncio.run(Supervisor().main())
