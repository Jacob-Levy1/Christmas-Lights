#!/usr/bin/env python3
"""Run the tree's web app on your laptop, with no hardware attached.

    python3 tools/simulator.py          # then open http://localhost:8080/

It serves the real files from ``www/`` and re-implements the board's HTTP API
on top of the real :mod:`tree` and :mod:`patterns` modules, so geometry,
calibration and the idle patterns behave exactly as they do on the Pico.
Only the LEDs are faked -- the last frame is kept in memory and exposed at
``/api/sim/frame`` as hex, which is also what the automated tests read.

This is a development aid: the routes here mirror ``code.py``, so if you add
an endpoint there, add it here too.
"""

import binascii
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import patterns  # noqa: E402
import tree  # noqa: E402

WWW = os.path.join(ROOT, "www")
CONTENT_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".json": "application/json",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


class Tree:
    """Everything code.py keeps in module globals, minus the LEDs."""

    def __init__(self):
        self.cfg = dict(tree.DEFAULTS)
        self.cfg["front"] = []
        self.cfg["calibrated"] = []
        self.geo = tree.Geometry(self.cfg)
        self.frame = [(0, 0, 0)] * self.geo.n
        self.show = patterns.Patterns(self.geo, self.cfg)
        self.mode = "idle"
        self.started = time.monotonic()
        self.last_frame_at = 0.0
        self.lock = threading.Lock()

    def rebuild(self):
        self.geo.rebuild(self.cfg)
        if len(self.frame) != self.geo.n:
            self.frame = [(0, 0, 0)] * self.geo.n
        self.show.set_geometry(self.geo)

    def state(self):
        return {
            "mode": self.mode,
            "clients": 1,
            "network": {"mode": "ap", "address": "127.0.0.1", "hostname": self.cfg["hostname"]},
            "config": self.cfg,
            "persisted": True,
            "patterns": [{"id": n, "label": l} for n, l in patterns.CATALOGUE],
            "palettes": sorted(patterns.PALETTES.keys()),
            "restart_keys": list(tree.RESTART_KEYS),
            "uptime": int(time.monotonic() - self.started),
            "free_memory": 128 * 1024,
        }

    def blank(self):
        for i in range(self.geo.n):
            self.frame[i] = (0, 0, 0)

    def render_idle(self, now):
        self.show.render(self.frame, now)


TREE = Tree()


def idle_thread():
    while True:
        with TREE.lock:
            if TREE.mode == "live" and time.monotonic() - TREE.last_frame_at > 4.0:
                TREE.mode = "idle"
            if TREE.mode == "idle":
                TREE.render_idle(time.monotonic())
        time.sleep(1.0 / max(1, TREE.cfg["fps"]))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    # -- helpers ---------------------------------------------------------
    def reply(self, code, body=b"", content_type="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def reply_json(self, payload, code=200):
        self.reply(code, json.dumps(payload).encode(), "application/json")

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def read_json(self):
        body = self.read_body()
        try:
            return json.loads(body) if body else {}
        except ValueError:
            return {}

    # -- GET -------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/state":
            with TREE.lock:
                return self.reply_json(TREE.state())
        if path == "/api/geometry":
            with TREE.lock:
                return self.reply_json(TREE.geo.describe())
        if path in ("/api/frame", "/api/sim/frame"):
            with TREE.lock:
                flat = bytearray()
                for color in TREE.frame:
                    flat.extend(color)
                return self.reply_json(
                    {"mode": TREE.mode, "hex": binascii.hexlify(flat).decode()}
                )

        if path == "/":
            path = "/index.html"
        target = os.path.normpath(os.path.join(WWW, path.lstrip("/")))
        if not target.startswith(WWW) or not os.path.isfile(target):
            return self.reply(404, b"not found", "text/plain")
        extension = os.path.splitext(target)[1]
        with open(target, "rb") as handle:
            data = handle.read()
        return self.reply(200, data, CONTENT_TYPES.get(extension, "text/plain"))

    # -- POST ------------------------------------------------------------
    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/frame":
            body = self.read_body()
            with TREE.lock:
                n = TREE.geo.n
                if len(body) == n * 3:
                    data = body
                else:
                    try:
                        data = binascii.unhexlify(body.strip())
                    except Exception:
                        data = body if len(body) % 3 == 0 else b""
                count = min(n, len(data) // 3)
                if count == 0:
                    return self.reply_json({"error": "bad frame"}, 400)
                for i in range(count):
                    at = i * 3
                    TREE.frame[i] = (data[at], data[at + 1], data[at + 2])
                for i in range(count, n):
                    TREE.frame[i] = (0, 0, 0)
                TREE.mode = "live"
                TREE.last_frame_at = time.monotonic()
            return self.reply(204)

        if path == "/api/config":
            incoming = self.read_json()
            with TREE.lock:
                changes = tree.sanitise(incoming)
                restart = any(
                    key in changes and changes[key] != TREE.cfg[key] for key in tree.RESTART_KEYS
                )
                TREE.cfg.update(changes)
                if incoming.get("interpolate_front"):
                    TREE.cfg["front"] = tree.interpolate_front(
                        TREE.cfg["front"], TREE.cfg["calibrated"]
                    )
                TREE.rebuild()
                if TREE.cfg["idle_pattern"] != TREE.show.name:
                    TREE.show.set_pattern(TREE.cfg["idle_pattern"], time.monotonic())
                payload = TREE.state()
                payload["restart_needed"] = restart
            return self.reply_json(payload)

        if path == "/api/mode":
            wanted = self.read_json().get("mode", "idle")
            with TREE.lock:
                if wanted == "idle":
                    TREE.mode = "idle"
                elif wanted == "off":
                    TREE.mode = "test"
                    TREE.blank()
                return self.reply_json({"mode": TREE.mode})

        if path == "/api/test":
            payload = self.read_json()
            with TREE.lock:
                return self.reply_json(run_test(payload))

        if path == "/api/restart":
            return self.reply_json({"restarting": True})

        return self.reply(404, b"not found", "text/plain")


def run_test(payload):
    """Mirror of code.py's /api/test handler."""
    geo, cfg, frame = TREE.geo, TREE.cfg, TREE.frame
    kind = payload.get("kind", "off")
    loop = int(payload.get("loop", 0)) % geo.turns
    index = int(payload.get("index", 0))

    TREE.mode = "test"
    TREE.blank()

    if kind == "calibrate":
        count = geo.counts[loop]
        start = geo.starts[loop]
        for j in range(count):
            frame[start + j] = (10, 6, 0)
        frame[start + (index % count)] = (255, 255, 255)
        if payload.get("save"):
            cfg["front"][loop] = (index % count) / count
            cfg["calibrated"][loop] = True
            if payload.get("interpolate"):
                cfg["front"] = tree.interpolate_front(cfg["front"], cfg["calibrated"])
            TREE.rebuild()
    elif kind == "front_line":
        for k in range(geo.turns):
            frame[geo.front_pixel(k)] = (255, 255, 255)
    elif kind == "loop":
        for i in geo.loop_pixels(loop):
            frame[i] = (0, 180, 255)
        frame[geo.front_pixel(loop)] = (255, 60, 0)
    elif kind == "markers":
        for i in range(0, geo.n, 10):
            frame[i] = (90, 90, 90)
        for i in range(0, geo.n, 50):
            frame[i] = (255, 0, 0)
    elif kind == "quadrants":
        colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 200, 0))
        for i in range(geo.n):
            frame[i] = colors[(geo.u[i] + 32) // 64 & 3]

    return {
        "mode": TREE.mode,
        "kind": kind,
        "loop": loop,
        "count": geo.counts[loop],
        "front": [round(x, 4) for x in cfg["front"]],
        "calibrated": cfg["calibrated"],
        "persisted": True,
    }


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    threading.Thread(target=idle_thread, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Tree simulator on http://127.0.0.1:%d/  (ctrl-c to stop)" % port)
    server.serve_forever()


if __name__ == "__main__":
    main()
