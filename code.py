"""Christmas tree light controller.

A single strip is wrapped around the tree in a spiral.  The board serves a
web app over wifi:

  * nobody connected  -> the tree runs one of the idle patterns by itself
  * a phone connected -> the phone pushes full frames (games, drawing,
                         photos) and the tree shows them immediately
  * the admin tab     -> tree shape, per-loop "front" calibration, idle
                         pattern, brightness and wifi settings

The heavy pixel maths for games and images happens in the browser; the board
exposes its geometry (see :mod:`tree`) and accepts raw RGB frames, which
keeps the Pico's job small and the games smooth.
"""

import binascii
import gc
import json
import time

import board
import ipaddress
import neopixel
import socketpool
import wifi
from adafruit_httpserver import POST, JSONResponse, Request, Response, Server

import buttons as button_driver
import patterns
import snake as snake_game
import tree

def body_json(request):
    """Parse a JSON request body, tolerating empty or malformed bodies."""
    try:
        body = request.body
        if not body:
            return {}
        if not isinstance(body, str):
            body = body.decode("utf-8")
        value = json.loads(body)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


MODE_IDLE = "idle"
MODE_LIVE = "live"
MODE_TEST = "test"
MODE_GAME = "game"  # the on-board game, played with the physical buttons

#: Back to the idle show this long after the last frame from a phone.
LIVE_TIMEOUT = 4.0
#: Back to the idle show this long after the last admin test command.
TEST_TIMEOUT = 300.0

cfg = tree.load_config()
config_persisted = True

geo = tree.Geometry(cfg)
frame = [(0, 0, 0)] * geo.n
show = patterns.Patterns(geo, cfg)

mode = MODE_IDLE
last_frame_at = 0.0
last_test_at = 0.0
last_render_at = 0.0
cycle_started_at = 0.0
onboard_game = None
controls = None
input_queue = []
config_dirty = False
last_config_save = 0.0

#: How long a button press waits to be collected by the web app.
INPUT_TTL = 2.0
#: Button-driven changes are applied at once but written to flash lazily, so
#: holding a direction does not hammer the filesystem.
CONFIG_SAVE_DELAY = 20.0

pixels = None
_readback = None
_hardware_signature = None
_active_brightness = None


# --------------------------------------------------------------------------
# LED hardware
# --------------------------------------------------------------------------
def build_pixels():
    """(Re)create the NeoPixel object to match the current config."""
    global pixels, _hardware_signature, _active_brightness

    signature = (cfg["pin"], cfg["num_pixels"], cfg["order"])
    if pixels is not None and signature == _hardware_signature:
        return

    if pixels is not None:
        try:
            pixels.deinit()
        except Exception:  # pragma: no cover
            pass
        pixels = None
        gc.collect()

    pin = getattr(board, cfg["pin"], None)
    if pin is None:
        print("config: unknown pin %r, falling back to GP0" % cfg["pin"])
        cfg["pin"] = "GP0"
        pin = board.GP0

    order = getattr(neopixel, cfg["order"], neopixel.GRB)
    pixels = neopixel.NeoPixel(
        pin,
        cfg["num_pixels"],
        brightness=cfg["idle_brightness"],
        auto_write=False,
        pixel_order=order,
    )
    _hardware_signature = signature
    _active_brightness = cfg["idle_brightness"]


def set_brightness(value):
    global _active_brightness
    if value != _active_brightness:
        pixels.brightness = value
        _active_brightness = value


_slice_assign = True


def push(colors):
    """Write a whole frame to the strip and latch it."""
    global _slice_assign
    if _slice_assign:
        try:
            pixels[0 : geo.n] = colors
            pixels.show()
            return
        except (TypeError, ValueError) as err:
            print("pixel slice assignment unavailable (%s); using per-pixel writes" % err)
            _slice_assign = False
    for i in range(geo.n):
        pixels[i] = colors[i]
    pixels.show()


def blank():
    pixels.fill((0, 0, 0))
    pixels.show()


def rebuild_geometry():
    """Re-derive the tree geometry and resize every buffer to match."""
    global geo, frame
    geo.rebuild(cfg)
    if len(frame) != geo.n:
        frame = [(0, 0, 0)] * geo.n
    show.set_geometry(geo)


# --------------------------------------------------------------------------
# Networking
# --------------------------------------------------------------------------
def start_network():
    """Bring up wifi.  Returns (mode, address) and always ends up usable."""
    try:
        wifi.radio.hostname = cfg["hostname"]
    except Exception:
        pass

    if cfg["wifi_mode"] == "sta" and cfg["sta_ssid"]:
        print("Joining wifi network %r ..." % cfg["sta_ssid"])
        try:
            wifi.radio.connect(cfg["sta_ssid"], cfg["sta_password"], timeout=15)
            address = wifi.radio.ipv4_address
            if address is not None:
                print("Joined. Open http://%s/" % address)
                return "sta", str(address)
        except Exception as err:
            print("Could not join (%s); starting own hotspot instead." % err)

    print("Starting hotspot %r ..." % cfg["ap_ssid"])
    wifi.radio.start_ap(ssid=cfg["ap_ssid"], password=cfg["ap_password"])
    wifi.radio.set_ipv4_address_ap(
        ipv4=ipaddress.IPv4Address("192.168.4.1"),
        netmask=ipaddress.IPv4Address("255.255.255.0"),
        gateway=ipaddress.IPv4Address("192.168.4.1"),
    )
    address = wifi.radio.ipv4_address_ap
    print("Hotspot up. Join %r then open http://%s/" % (cfg["ap_ssid"], address))
    return "ap", str(address)


def advertise_mdns():
    try:
        import mdns

        server = mdns.Server(wifi.radio)
        server.hostname = cfg["hostname"]
        server.advertise_service(service_type="_http", protocol="_tcp", port=80)
        print("Also reachable at http://%s.local/" % cfg["hostname"])
    except Exception as err:
        print("mDNS unavailable (%s)" % err)


def connected_clients():
    try:
        return len(wifi.radio.stations_ap)
    except Exception:
        return -1


# --------------------------------------------------------------------------
# Boot
# --------------------------------------------------------------------------
build_pixels()
# Brief startup blink so you can see the strip is alive.
pixels[0] = (40, 0, 0)
pixels.show()
time.sleep(0.3)
blank()

controls = button_driver.Buttons(cfg)
if controls.active:
    print("Buttons wired: %s" % ", ".join(sorted(controls.pins.keys())))
for warning in controls.warnings:
    print("Button warning -- %s" % warning)

network_mode, network_address = start_network()
advertise_mdns()

pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, "/www", debug=False)
server.request_buffer_size = 2048


# --------------------------------------------------------------------------
# Physical buttons
# --------------------------------------------------------------------------
def stop_onboard_game():
    global onboard_game
    onboard_game = None


def start_onboard_game(now):
    """Start Snake on the board itself, played with the buttons."""
    global onboard_game, mode
    onboard_game = snake_game.Snake(geo, cfg["cols"], cfg["snake_wrap"] != "ring")
    onboard_game.reset(now)
    mode = MODE_GAME
    set_brightness(cfg["brightness"])


def next_pattern(offset, now):
    global config_dirty
    current = patterns.NAMES.index(show.name) if show.name in patterns.NAMES else 0
    show.set_pattern(patterns.NAMES[(current + offset) % len(patterns.NAMES)], now)
    cfg["idle_pattern"] = show.name
    config_dirty = True


def nudge_brightness(step):
    global config_dirty
    value = cfg["idle_brightness"] + step
    cfg["idle_brightness"] = min(1.0, max(0.02, value))
    set_brightness(cfg["idle_brightness"])
    config_dirty = True


def handle_buttons(now):
    """Read the buttons and act on them.

    While a phone is driving the tree the presses are only queued, so the web
    app can use them as a handheld controller for its own games.  The rest of
    the time they drive the board directly.
    """
    global mode, cycle_started_at, last_render_at, last_test_at

    if controls is None or not controls.active:
        return

    for label, kind in controls.poll(now):
        if kind == "release":
            continue

        # Always queue the press for the web app to collect.
        if kind in ("press", "repeat"):
            input_queue.append((now, label))
            if len(input_queue) > 24:
                del input_queue[0]

        if mode == MODE_LIVE:
            continue  # the phone is in charge; it will read the queue

        if onboard_game is not None:
            if kind in ("press", "repeat") and label != "select":
                onboard_game.input(label)
            elif label == "select" and kind == "press":
                onboard_game.reset(now)
            elif label == "select" and kind == "hold":
                stop_onboard_game()
                mode = MODE_IDLE
                cycle_started_at = now
                set_brightness(cfg["idle_brightness"])
            continue

        if mode == MODE_TEST:
            # Any press leaves a calibration pattern and goes back to idle.
            mode = MODE_IDLE
            cycle_started_at = now
            set_brightness(cfg["idle_brightness"])
            continue

        if label == "select":
            if kind == "press":
                start_onboard_game(now)
            elif kind == "hold":
                mode = MODE_TEST
                last_test_at = now
                blank()
        elif kind in ("press", "repeat"):
            if label == "left":
                next_pattern(-1, now)
            elif label == "right":
                next_pattern(1, now)
            elif label == "up":
                nudge_brightness(0.05)
            elif label == "down":
                nudge_brightness(-0.05)
            last_render_at = 0.0


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def state_payload():
    return {
        "mode": mode,
        "clients": connected_clients(),
        "network": {
            "mode": network_mode,
            "address": network_address,
            "hostname": cfg["hostname"],
        },
        "config": cfg,
        "persisted": config_persisted,
        "patterns": [{"id": name, "label": label} for name, label in patterns.CATALOGUE],
        "palettes": sorted(patterns.PALETTES.keys()),
        "restart_keys": list(tree.RESTART_KEYS),
        "uptime": int(time.monotonic()),
        "free_memory": gc.mem_free(),
        "buttons": {
            "active": controls.active if controls else False,
            "wired": sorted(controls.pins.keys()) if controls else [],
            "warnings": controls.warnings if controls else [],
            "labels": list(button_driver.LABELS),
        },
        "onboard_game": onboard_game is not None,
    }


@server.route("/api/state")
def route_state(request: Request):
    return JSONResponse(request, state_payload())


@server.route("/api/geometry")
def route_geometry(request: Request):
    return JSONResponse(request, geo.describe())


@server.route("/api/input")
def route_input(request: Request):
    """Button presses waiting to be used as a controller by the web app.

    Each call drains the queue.  Presses older than a couple of seconds are
    dropped rather than replayed, so a page that reconnects does not get a
    burst of stale input.
    """
    now = time.monotonic()
    presses = [label for when, label in input_queue if now - when < INPUT_TTL]
    del input_queue[:]
    return JSONResponse(request, {"presses": presses, "mode": mode})


@server.route("/api/config", POST)
def route_config(request: Request):
    """Apply (and try to save) a partial config update."""
    global config_persisted

    incoming = body_json(request)
    changes = tree.sanitise(incoming)
    restart_needed = any(
        key in changes and changes[key] != cfg[key] for key in tree.RESTART_KEYS
    )
    cfg.update(changes)

    if incoming.get("interpolate_front"):
        cfg["front"] = tree.interpolate_front(cfg["front"], cfg["calibrated"])

    if any(key in changes for key in tree.BUTTON_KEYS):
        controls.configure(cfg)

    build_pixels()
    rebuild_geometry()
    if onboard_game is not None:
        stop_onboard_game()
    if cfg["idle_pattern"] != show.name:
        show.set_pattern(cfg["idle_pattern"], time.monotonic())
    set_brightness(cfg["idle_brightness"] if mode == MODE_IDLE else cfg["brightness"])

    config_persisted = tree.save_config(cfg)
    payload = state_payload()
    payload["restart_needed"] = restart_needed
    return JSONResponse(request, payload)


@server.route("/api/frame")
def route_read_frame(request: Request):
    """The frame the tree is showing right now, so the web page can mirror it.

    Polled by the browser while the board is running its own idle show.
    """
    global _readback
    if _readback is None or len(_readback) != geo.n * 3:
        _readback = bytearray(geo.n * 3)
    flat = _readback
    for i in range(geo.n):
        color = frame[i]
        at = i * 3
        flat[at] = color[0]
        flat[at + 1] = color[1]
        flat[at + 2] = color[2]
    return JSONResponse(request, {"mode": mode, "hex": binascii.hexlify(flat).decode()})


@server.route("/api/frame", POST)
def route_frame(request: Request):
    """Accept a full frame of RGB bytes from a connected phone.

    The body is either ``3 * num_pixels`` raw bytes, or the same data as a
    hex string (handy for testing with curl).
    """
    global mode, last_frame_at

    body = request.body or b""
    expected = geo.n * 3

    # Raw binary is exactly three bytes per pixel; a hex body (handy from
    # curl) is exactly six characters per pixel.  Anything else is decoded as
    # best we can and rejected if it makes no sense at all.
    if len(body) == expected:
        data = body
    else:
        try:
            data = binascii.unhexlify(body.strip())
        except Exception:
            data = body if len(body) % 3 == 0 else b""

    count = min(geo.n, len(data) // 3)
    if count == 0:
        return JSONResponse(request, {"error": "bad frame"}, status=(400, "Bad Request"))

    for i in range(count):
        at = i * 3
        frame[i] = (data[at], data[at + 1], data[at + 2])
    for i in range(count, geo.n):
        frame[i] = (0, 0, 0)

    if onboard_game is not None:
        stop_onboard_game()
    mode = MODE_LIVE
    last_frame_at = time.monotonic()
    set_brightness(cfg["brightness"])
    push(frame)
    return Response(request, "", status=(204, "No Content"))


@server.route("/api/mode", POST)
def route_mode(request: Request):
    """Switch between the idle show and live (phone-driven) mode."""
    global mode, cycle_started_at

    wanted = body_json(request).get("mode", MODE_IDLE)

    if wanted == MODE_IDLE:
        if onboard_game is not None:
            stop_onboard_game()
        mode = MODE_IDLE
        cycle_started_at = time.monotonic()
        set_brightness(cfg["idle_brightness"])
    elif wanted == "off":
        mode = MODE_TEST
        blank()
    return JSONResponse(request, {"mode": mode})


@server.route("/api/test", POST)
def route_test(request: Request):
    """Admin helpers: calibration dot, front line, ring walk, markers."""
    global mode, last_test_at, config_persisted

    payload = body_json(request)
    kind = payload.get("kind", "off")
    loop = int(payload.get("loop", 0)) % geo.turns
    index = int(payload.get("index", 0))

    if onboard_game is not None:
        stop_onboard_game()
    mode = MODE_TEST
    last_test_at = time.monotonic()
    set_brightness(cfg["brightness"])

    for i in range(geo.n):
        frame[i] = (0, 0, 0)

    if kind == "calibrate":
        # Dim outline of the loop being calibrated plus one bright pixel you
        # drag around with the slider until it points at the front.
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
            rebuild_geometry()
            config_persisted = tree.save_config(cfg)
    elif kind == "front_line":
        # One pixel per loop, all at angle 0.  A correct calibration makes
        # these line up as a straight vertical line at the front.
        for k in range(geo.turns):
            frame[geo.front_pixel(k)] = (255, 255, 255)
    elif kind == "loop":
        for i in geo.loop_pixels(loop):
            frame[i] = (0, 180, 255)
        frame[geo.front_pixel(loop)] = (255, 60, 0)
    elif kind == "markers":
        # Every 10th pixel white, every 50th red: handy for counting.
        for i in range(0, geo.n, 10):
            frame[i] = (90, 90, 90)
        for i in range(0, geo.n, 50):
            frame[i] = (255, 0, 0)
    elif kind == "quadrants":
        # Front red, right green, back blue, left yellow: checks the wind
        # direction and the global rotation at a glance.
        colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 200, 0))
        for i in range(geo.n):
            frame[i] = colors[(geo.u[i] + 32) // 64 & 3]

    push(frame)
    return JSONResponse(
        request,
        {
            "mode": mode,
            "kind": kind,
            "loop": loop,
            "count": geo.counts[loop],
            "front": [round(x, 4) for x in cfg["front"]],
            "calibrated": cfg["calibrated"],
            "persisted": config_persisted,
        },
    )


@server.route("/api/restart", POST)
def route_restart(request: Request):
    import supervisor

    supervisor.reload()
    return JSONResponse(request, {"restarting": True})


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
def step(now):
    """One pass of the animation side of the main loop.

    Separated from the ``while`` below so it can be exercised off-hardware
    (see tools/test_firmware.py).
    """
    global mode, cycle_started_at, last_render_at, config_dirty, last_config_save

    handle_buttons(now)

    # Button-driven changes are written to flash well after the last one.
    if config_dirty and now - last_config_save > CONFIG_SAVE_DELAY:
        config_dirty = False
        last_config_save = now
        tree.save_config(cfg)

    if mode == MODE_GAME and onboard_game is not None:
        if now - last_render_at >= 1.0 / cfg["fps"]:
            last_render_at = now
            onboard_game.tick(now, frame)
            push(frame)
        return

    # Hand control back to the idle show once the phone stops sending.
    if mode == MODE_LIVE and now - last_frame_at > LIVE_TIMEOUT:
        mode = MODE_IDLE
        cycle_started_at = now
        set_brightness(cfg["idle_brightness"])
    elif mode == MODE_TEST and now - last_test_at > TEST_TIMEOUT:
        mode = MODE_IDLE
        cycle_started_at = now
        set_brightness(cfg["idle_brightness"])

    if mode != MODE_IDLE:
        return

    if now - last_render_at < 1.0 / cfg["fps"]:
        return
    last_render_at = now

    if cfg["idle_cycle"] and now - cycle_started_at >= cfg["idle_cycle_secs"]:
        cycle_started_at = now
        current = patterns.NAMES.index(show.name) if show.name in patterns.NAMES else 0
        show.set_pattern(patterns.NAMES[(current + 1) % len(patterns.NAMES)], now)
        cfg["idle_pattern"] = show.name

    show.render(frame, now)
    push(frame)


def run():
    global cycle_started_at

    server.start(network_address, port=80)
    cycle_started_at = time.monotonic()
    gc.collect()
    print("Ready. %d pixels, %d loops: %s" % (geo.n, geo.turns, geo.counts))

    while True:
        try:
            server.poll()
        except Exception as err:  # keep the lights on even if a client misbehaves
            print("server error:", err)
        step(time.monotonic())


if __name__ == "__main__":
    run()
