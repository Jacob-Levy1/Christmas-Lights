#!/usr/bin/env python3
"""Run the tree's firmware on your laptop, with no hardware attached.

    python3 tools/build.py          # bundle the web app
    python3 tools/simulator.py      # then open http://localhost:8080/

This imports the board's real ``code.py`` against the pretend hardware in
``fake_hardware.py`` and hands it real HTTP requests, so the routes, the
auth, the geometry and the patterns here are the same code that runs on the
Pico -- not a second implementation that can drift away from it.  Only the
LEDs are faked, and the last frame is exposed at ``/api/sim/frame`` for the
automated tests to read.

A few ``/api/sim/...`` endpoints exist only here, for driving the pretend
hardware: reading the strip, reading the board's game grid, and pressing the
physical buttons.
"""

import binascii
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_hardware  # noqa: E402

ROOT = fake_hardware.ROOT

# Settings are kept in a scratch file, so a run always starts from the
# defaults and never writes to the machine's filesystem root.
_CONFIG = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_CONFIG.close()
os.unlink(_CONFIG.name)

FIRMWARE = fake_hardware.load_firmware(_CONFIG.name)
LOCK = threading.RLock()

STATUS_TEXT = {
    200: "OK", 204: "No Content", 304: "Not Modified", 400: "Bad Request",
    401: "Unauthorized", 404: "Not Found", 406: "Not Acceptable",
    429: "Too Many Requests", 500: "Internal Server Error",
}


def animate():
    """Stand in for the board's main loop."""
    while True:
        with LOCK:
            try:
                FIRMWARE.step(time.monotonic())
            except Exception as err:  # pragma: no cover
                print("render error:", err)
        time.sleep(1.0 / max(1, FIRMWARE.cfg["fps"]))


def frame_hex():
    flat = bytearray()
    for color in FIRMWARE.frame:
        flat.extend(color)
    return binascii.hexlify(flat).decode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    # -- plumbing --------------------------------------------------------
    def reply(self, code, body=b"", content_type="text/plain", headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code, STATUS_TEXT.get(code, ""))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def reply_json(self, payload, code=200):
        self.reply(code, json.dumps(payload).encode(), "application/json")

    def cookies(self):
        raw = self.headers.get("Cookie", "")
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                name, _, value = part.partition("=")
                out[name.strip()] = value.strip()
        return out

    def build_request(self, method, path):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        return fake_hardware.FakeRequest(
            body=body,
            method=method,
            headers={k: v for k, v in self.headers.items()},
            cookies=self.cookies(),
            path=path,
        )

    def dispatch(self, method):
        path = urlparse(self.path).path

        if path.startswith("/api/sim/"):
            return self.simulated(path, method)

        handler = FIRMWARE.server.routes.get((method, path))
        if handler is None:
            return self.reply(404, b"no such route", "text/plain")

        request = self.build_request(method, path)
        with LOCK:
            try:
                response = handler(request)
            except Exception as err:  # pragma: no cover
                import traceback

                traceback.print_exc()
                return self.reply(500, str(err).encode())
            body = response.payload()
            status = response.status
            code = status[0] if isinstance(status, tuple) else 200
            headers = dict(response.headers)
            content_type = getattr(response, "content_type", None) or "text/plain"
        headers.setdefault("Cache-Control", "no-store")
        return self.reply(code, body, content_type, headers)

    # -- simulator-only helpers -----------------------------------------
    def simulated(self, path, method):
        with LOCK:
            if path == "/api/sim/frame":
                return self.reply_json({"mode": FIRMWARE.mode, "hex": frame_hex()})
            if path == "/api/sim/grid":
                return self.reply_json(
                    {"grid": FIRMWARE.geo.build_grid(FIRMWARE.cfg["cols"])}
                )
            if path == "/api/sim/press" and method == "POST":
                return self.sim_press()
        return self.reply(404, b"no such route")

    def sim_press(self):
        """Press a real button on the pretend hardware.

        Goes through the firmware's own debouncing rather than faking the
        queue, so what the tests exercise is the real path.
        """
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")

        if not FIRMWARE.cfg["buttons_enabled"]:
            FIRMWARE.cfg["buttons_enabled"] = True
            FIRMWARE.controls.configure(FIRMWARE.cfg)

        label = payload.get("press")
        if label:
            pin = FIRMWARE.controls.pins.get(label)
            if pin is None:
                return self.reply_json({"error": "no button called %r" % label}, 400)
            now = time.monotonic()
            pin.press()
            FIRMWARE.handle_buttons(now)
            FIRMWARE.handle_buttons(now + 0.05)
            pin.release()
            FIRMWARE.handle_buttons(now + 0.1)
            FIRMWARE.handle_buttons(now + 0.15)
        return self.reply_json(
            {"wired": sorted(FIRMWARE.controls.pins), "queued": len(FIRMWARE.input_queue)}
        )

    # -- verbs -----------------------------------------------------------
    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_HEAD(self):
        self.dispatch("GET")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    page = os.path.join(ROOT, "www", "index.html.gz")
    if not os.path.isfile(page):
        raise SystemExit("run python3 tools/build.py first (no %s)" % page)

    threading.Thread(target=animate, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Tree simulator on http://127.0.0.1:%d/  (ctrl-c to stop)" % port)
    print("Running the real code.py; only the LEDs are pretend.")
    server.serve_forever()


if __name__ == "__main__":
    main()
