#!/usr/bin/env python3
"""Exercise the real code.py against mock CircuitPython hardware.

    python3 tools/test_firmware.py

Stubs out board / neopixel / wifi / socketpool / adafruit_httpserver, imports
``code.py`` for real, then drives its actual route handlers and animation
step.  Catches the mistakes a simulator cannot: wrong handler logic, bad
pixel writes, or config changes that fail to resize a buffer.
"""

import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILURES = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))
    if not ok:
        FAILURES.append(name)


# ---------------------------------------------------------------- stubs ---
class FakeStrip:
    GRB = "GRB"

    def __init__(self, pin, count, brightness=1.0, auto_write=False, pixel_order="GRB"):
        self.pin = pin
        self.count = count
        self.brightness = brightness
        self.pixel_order = pixel_order
        self.buffer = [(0, 0, 0)] * count
        self.shows = 0
        self.deinited = False

    def __setitem__(self, index, value):
        if isinstance(index, slice):
            start = index.start or 0
            stop = index.stop if index.stop is not None else self.count
            if not isinstance(value, (list, tuple)):
                raise TypeError("expected a sequence of colours")
            if len(value) != stop - start:
                raise ValueError("length mismatch")
            for offset, color in enumerate(value):
                if len(color) != 3 or any(not isinstance(c, int) or c < 0 or c > 255 for c in color):
                    raise ValueError("bad colour %r" % (color,))
                self.buffer[start + offset] = tuple(color)
        else:
            self.buffer[index] = tuple(value)

    def __getitem__(self, index):
        return self.buffer[index]

    def fill(self, color):
        self.buffer = [tuple(color)] * self.count

    def show(self):
        self.shows += 1

    def deinit(self):
        self.deinited = True


class FakeRadio:
    def __init__(self):
        self.hostname = ""
        self.ipv4_address_ap = "192.168.4.1"
        self.ipv4_address = None
        self.stations_ap = [object()]
        self.ap = None

    def start_ap(self, ssid=None, password=None):
        self.ap = (ssid, password)

    def set_ipv4_address_ap(self, **kwargs):
        pass

    def connect(self, ssid, password, timeout=10):
        raise RuntimeError("no network in the test rig")


class FakeRequest:
    def __init__(self, body=b"", method="POST"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.body = body
        self.method = method
        self.query_params = {}
        self.headers = {}


class FakeResponse:
    def __init__(self, request, body="", status=(200, "OK"), **kwargs):
        self.request = request
        self.body = body
        self.status = status


class FakeJSONResponse(FakeResponse):
    def __init__(self, request, data, status=(200, "OK"), **kwargs):
        super().__init__(request, data, status)
        self.data = data


class FakeServer:
    def __init__(self, pool, root_path=None, debug=False):
        self.routes = {}
        self.request_buffer_size = 1024

    def route(self, path, method="GET", **kwargs):
        def decorate(func):
            self.routes[(method, path)] = func
            return func

        return decorate

    def start(self, host, port=80):
        pass

    def poll(self):
        pass


def install_stubs():
    board = types.ModuleType("board")
    board.GP0 = "GP0"
    board.GP1 = "GP1"
    sys.modules["board"] = board

    neopixel = types.ModuleType("neopixel")
    neopixel.NeoPixel = FakeStrip
    neopixel.GRB = "GRB"
    neopixel.RGB = "RGB"
    sys.modules["neopixel"] = neopixel

    wifi = types.ModuleType("wifi")
    wifi.radio = FakeRadio()
    sys.modules["wifi"] = wifi

    ipaddress = types.ModuleType("ipaddress")
    ipaddress.IPv4Address = lambda text: text
    sys.modules["ipaddress"] = ipaddress

    socketpool = types.ModuleType("socketpool")
    socketpool.SocketPool = lambda radio: object()
    sys.modules["socketpool"] = socketpool

    http = types.ModuleType("adafruit_httpserver")
    http.POST = "POST"
    http.GET = "GET"
    http.Request = FakeRequest
    http.Response = FakeResponse
    http.JSONResponse = FakeJSONResponse
    http.Server = FakeServer
    sys.modules["adafruit_httpserver"] = http

    import gc

    if not hasattr(gc, "mem_free"):
        gc.mem_free = lambda: 123456


# ----------------------------------------------------------------- tests ---
def main():
    install_stubs()
    sys.path.insert(0, ROOT)

    # Keep the test out of the real /config.json: on a board that path is the
    # CIRCUITPY root, but here it would be the machine's filesystem root and
    # would leak state between runs.
    import tempfile

    import tree as tree_module

    handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    handle.close()
    os.unlink(handle.name)
    tree_module.CONFIG_PATH = handle.name

    import code as firmware  # this is the board's code.py, not the stdlib

    call = lambda method, path, body=b"": firmware.server.routes[(method, path)](
        FakeRequest(body, method)
    )

    check("boot created the strip", firmware.pixels.count == 300, firmware.pixels.count)
    check("hotspot started", firmware.wifi.radio.ap is not None, firmware.wifi.radio.ap)
    check(
        "routes registered",
        set(firmware.server.routes.keys())
        >= {
            ("GET", "/api/state"),
            ("GET", "/api/geometry"),
            ("GET", "/api/frame"),
            ("POST", "/api/frame"),
            ("POST", "/api/config"),
            ("POST", "/api/mode"),
            ("POST", "/api/test"),
        },
        sorted(firmware.server.routes.keys()),
    )

    # --- idle rendering ---
    firmware.step(1000.0)
    lit = sum(1 for c in firmware.pixels.buffer if sum(c) > 0)
    check("idle pattern reaches the strip", lit > 0, "%d pixels lit" % lit)

    # every pattern, rendered through the real push() path
    bad = []
    for name in firmware.patterns.NAMES:
        firmware.show.set_pattern(name, 0.0)
        for k in range(8):
            firmware.last_render_at = 0
            firmware.step(2000.0 + k)
        if any(len(c) != 3 or any(x < 0 or x > 255 for x in c) for c in firmware.pixels.buffer):
            bad.append(name)
    check("every pattern writes valid colours", not bad, bad)

    # --- live frames ---
    body = bytes([9, 20, 30] * 300)
    response = call("POST", "/api/frame", body)
    check("frame accepted", response.status[0] == 204, response.status)
    check("frame reached the strip", firmware.pixels.buffer[0] == (9, 20, 30), firmware.pixels.buffer[0])
    check("mode switched to live", firmware.mode == "live", firmware.mode)

    readback = call("GET", "/api/frame").data
    check("frame read-back matches", readback["hex"][:6] == "09141e", readback["hex"][:6])

    # hex bodies (for curl / debugging)
    call("POST", "/api/frame", ("ff0000" * 300).encode())
    check("hex frame accepted", firmware.pixels.buffer[0] == (255, 0, 0), firmware.pixels.buffer[0])

    # short frames pad with black instead of crashing
    call("POST", "/api/frame", bytes([1, 2, 3] * 10))
    check(
        "short frame padded",
        firmware.pixels.buffer[9] == (1, 2, 3) and firmware.pixels.buffer[10] == (0, 0, 0),
        "%s / %s" % (firmware.pixels.buffer[9], firmware.pixels.buffer[10]),
    )

    check("empty frame rejected", call("POST", "/api/frame", b"").status[0] == 400)
    check("garbage body rejected", call("POST", "/api/frame", b"not hex at all").status[0] == 400)

    # live mode times out back to the idle show
    firmware.step(firmware.last_frame_at + firmware.LIVE_TIMEOUT + 1)
    check("live mode times out to idle", firmware.mode == "idle", firmware.mode)

    # --- config ---
    state = call("POST", "/api/config", {"turns": 16, "top_ratio": 0.3}).data
    check("turns applied", state["config"]["turns"] == 16, state["config"]["turns"])
    check("loops add up", sum(firmware.geo.counts) == firmware.geo.n, firmware.geo.counts)
    check("no restart demanded for shape changes", state["restart_needed"] is False)

    state = call("POST", "/api/config", {"num_pixels": 150}).data
    check("pixel count resizes the strip", firmware.pixels.count == 150, firmware.pixels.count)
    check("frame buffer resized", len(firmware.frame) == 150, len(firmware.frame))
    firmware.last_render_at = 0
    firmware.step(3000.0)
    check("renders at the new length", firmware.pixels.shows > 0)

    state = call("POST", "/api/config", {"ap_ssid": "Tree2"}).data
    check("wifi change asks for a restart", state["restart_needed"] is True)

    state = call("POST", "/api/config", {"num_pixels": 99999, "turns": -4}).data
    check(
        "out-of-range settings clamped",
        state["config"]["num_pixels"] == 1200 and state["config"]["turns"] == 1,
        "%s px, %s turns" % (state["config"]["num_pixels"], state["config"]["turns"]),
    )
    call("POST", "/api/config", {"num_pixels": 300, "turns": 12, "top_ratio": 0.18})

    check("malformed json ignored", call("POST", "/api/config", b"{{{").data["config"]["turns"] == 12)
    check("unknown pin falls back", call("POST", "/api/config", {"pin": "GP99"}).data["config"]["pin"] == "GP0")
    call("POST", "/api/config", {"pin": "GP0"})

    # --- calibration ---
    result = call("POST", "/api/test", {"kind": "calibrate", "loop": 3, "index": 5, "save": True}).data
    check("front saved for a loop", result["calibrated"][3] is True, result["front"][3])
    check(
        "saved front becomes that loop's front pixel",
        firmware.geo.front_pixel(3) == firmware.geo.starts[3] + 5,
        firmware.geo.front_pixel(3),
    )

    call("POST", "/api/test", {"kind": "front_line"})
    white = [i for i, c in enumerate(firmware.pixels.buffer) if c == (255, 255, 255)]
    check(
        "front line lights one pixel per loop",
        len(white) == firmware.geo.turns
        and white == sorted(firmware.geo.front_pixel(k) for k in range(firmware.geo.turns)),
        "%d lit" % len(white),
    )

    call("POST", "/api/test", {"kind": "quadrants"})
    colors = {c for c in firmware.pixels.buffer}
    check("quadrant test paints four colours", len(colors) == 4, colors)

    call("POST", "/api/test", {"kind": "loop", "loop": 2})
    on = sum(1 for c in firmware.pixels.buffer if sum(c) > 0)
    check("single loop test lights that loop", on == firmware.geo.counts[2], "%d of %d" % (on, firmware.geo.counts[2]))

    # test mode also times out
    firmware.step(firmware.last_test_at + firmware.TEST_TIMEOUT + 1)
    check("test mode times out to idle", firmware.mode == "idle", firmware.mode)

    # --- mode + interpolation ---
    call("POST", "/api/mode", {"mode": "off"})
    check("all-off blanks the strip", all(c == (0, 0, 0) for c in firmware.pixels.buffer))
    check("idle mode restored", call("POST", "/api/mode", {"mode": "idle"}).data["mode"] == "idle")

    call("POST", "/api/test", {"kind": "calibrate", "loop": 0, "index": 0, "save": True})
    call("POST", "/api/test", {"kind": "calibrate", "loop": 11, "index": 4, "save": True})
    before = list(firmware.cfg["front"])
    state = call("POST", "/api/config", {"interpolate_front": True}).data
    after = state["config"]["front"]
    check("interpolation fills the gaps", after != before and all(x is not None for x in after),
          [round(x, 3) for x in after])

    # --- auto-cycle ---
    call("POST", "/api/config", {"idle_cycle": True, "idle_cycle_secs": 5})
    firmware.mode = "idle"
    firmware.cycle_started_at = 10000.0
    firmware.last_render_at = 0
    first = firmware.show.name
    firmware.step(10010.0)
    check("auto-cycle moves to the next pattern", firmware.show.name != first,
          first + " -> " + firmware.show.name)

    if os.path.exists(tree_module.CONFIG_PATH):
        saved = json.load(open(tree_module.CONFIG_PATH))
        check("config written to flash", saved.get("turns") == firmware.cfg["turns"], saved.get("turns"))
        os.unlink(tree_module.CONFIG_PATH)
    else:
        check("config written to flash", False, "nothing saved")

    print()
    if FAILURES:
        print("%d FAILURES: %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all firmware checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
