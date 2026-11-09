"""Stand-ins for the CircuitPython modules code.py imports.

With these in ``sys.modules``, the board's real ``code.py`` imports and runs
under ordinary Python.  Both the test rig and the simulator use them, so
neither has to reimplement the firmware's routes -- the thing they are meant
to be checking.  Only the hardware is pretend.
"""

import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeStrip:
    """A NeoPixel strip that records what was written to it."""

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
                self._check(color)
                self.buffer[start + offset] = tuple(color)
        else:
            self._check(value)
            self.buffer[index] = tuple(value)

    @staticmethod
    def _check(color):
        if len(color) != 3 or any(
            not isinstance(c, int) or c < 0 or c > 255 for c in color
        ):
            raise ValueError("bad colour %r" % (color,))

    def __getitem__(self, index):
        return self.buffer[index]

    def fill(self, color):
        self.buffer = [tuple(color)] * self.count

    def show(self):
        self.shows += 1

    def deinit(self):
        self.deinited = True


class FakePin:
    """A GPIO pin with a pull-up: high until the switch pulls it down."""

    instances = {}

    def __init__(self, pin):
        held = FakePin.instances.get(pin)
        if held is not None and not held.deinited:
            raise ValueError("%s in use" % pin)
        self.pin = pin
        self.direction = None
        self.pull = None
        self.value = True
        self.deinited = False
        FakePin.instances[pin] = self

    def deinit(self):
        self.deinited = True

    def press(self):
        self.value = False

    def release(self):
        self.value = True


class FakeRadio:
    def __init__(self):
        self.hostname = ""
        self.ipv4_address_ap = "127.0.0.1"
        self.ipv4_address = None
        self.stations_ap = [object()]
        self.ap = None

    def start_ap(self, ssid=None, password=None):
        self.ap = (ssid, password)

    def set_ipv4_address_ap(self, **kwargs):
        pass

    def connect(self, ssid, password, timeout=10):
        raise RuntimeError("no network in the test rig")


class FakeHeaders(dict):
    """Case-insensitive headers, like the real server's."""

    def __init__(self, values=None):
        super().__init__()
        for key, value in (values or {}).items():
            self[key.lower()] = value

    def get(self, key, default=None):
        return super().get(str(key).lower(), default)

    def get_directive(self, key, default=None):
        value = self.get(key, default)
        return value.split(";")[0].strip() if isinstance(value, str) else value


class FakeRequest:
    def __init__(self, body=b"", method="POST", headers=None, cookies=None, path="/"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        if isinstance(body, str):
            body = body.encode()
        self.body = body
        self.method = method
        self.path = path
        self.query_params = {}
        self.headers = FakeHeaders(headers)
        self.cookies = dict(cookies or {})


class FakeResponse:
    def __init__(self, request, body="", status=(200, "OK"), headers=None,
                 content_type=None, **kwargs):
        self.request = request
        self.body = body
        self.status = status
        self.headers = dict(headers or {})
        self.content_type = content_type or "text/plain"

    def payload(self):
        body = self.body
        if isinstance(body, str):
            body = body.encode()
        return body or b""


class FakeJSONResponse(FakeResponse):
    def __init__(self, request, data, status=(200, "OK"), headers=None, **kwargs):
        super().__init__(request, data, status, headers, "application/json")
        self.data = data

    def payload(self):
        return json.dumps(self.data).encode()


class FakeFileResponse(FakeResponse):
    def __init__(self, request, filename="index.html", root_path=None, status=(200, "OK"),
                 headers=None, content_type=None, buffer_size=1024, **kwargs):
        super().__init__(request, "", status, headers, content_type)
        self.filename = filename
        self.root_path = root_path or ""
        self.buffer_size = buffer_size

    def payload(self):
        # Board paths like /www/index.html.gz live under the repo here.
        path = os.path.join(ROOT, (self.root_path + "/" + self.filename).lstrip("/"))
        path = os.path.normpath(path.replace("//", "/"))
        with open(path, "rb") as handle:
            return handle.read()


class FakeServer:
    def __init__(self, pool, root_path=None, debug=False):
        self.routes = {}
        self.root_path = root_path
        self.request_buffer_size = 1024
        self.socket_timeout = 1

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
    """Put the pretend hardware in sys.modules, before importing code.py."""
    board = types.ModuleType("board")
    for number in range(0, 29):
        setattr(board, "GP%d" % number, "GP%d" % number)
    sys.modules["board"] = board

    neopixel = types.ModuleType("neopixel")
    neopixel.NeoPixel = FakeStrip
    neopixel.GRB = "GRB"
    neopixel.RGB = "RGB"
    neopixel.GRBW = "GRBW"
    neopixel.RGBW = "RGBW"
    sys.modules["neopixel"] = neopixel

    digitalio = types.ModuleType("digitalio")
    digitalio.DigitalInOut = FakePin
    digitalio.Direction = types.SimpleNamespace(INPUT="input", OUTPUT="output")
    digitalio.Pull = types.SimpleNamespace(UP="up", DOWN="down")
    sys.modules["digitalio"] = digitalio

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
    http.FileResponse = FakeFileResponse
    http.Server = FakeServer
    sys.modules["adafruit_httpserver"] = http

    import gc

    if not hasattr(gc, "mem_free"):
        gc.mem_free = lambda: 123456


def load_firmware(config_path=None):
    """Import the real code.py against the stubs and hand it back."""
    install_stubs()
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    import tree as tree_module

    if config_path is not None:
        tree_module.CONFIG_PATH = config_path

    import code as firmware  # the board's code.py, not the stdlib module

    return firmware
