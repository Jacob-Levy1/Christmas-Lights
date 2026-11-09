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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_hardware
from fake_hardware import FakePin, FakeRequest, install_stubs  # noqa: F401

ROOT = fake_hardware.ROOT

FAILURES = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  [" + str(detail) + "]") if detail else ""))
    if not ok:
        FAILURES.append(name)


# ----------------------------------------------------------------- tests ---
def main():
    # Keep the test out of the real /config.json: on a board that path is the
    # CIRCUITPY root, but here it would be the machine's filesystem root and
    # would leak state between runs.
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    handle.close()
    os.unlink(handle.name)

    firmware = fake_hardware.load_firmware(handle.name)
    import tree as tree_module
    import pwhash

    # Sign in with a credential of this test's own making, so the real
    # default password never has to live in the repository.
    TEST_EMAIL = "tester@example.com"
    TEST_PASSWORD = "five-random-words-go-here"
    salt = pwhash.random_hex(16)
    digest, rounds = pwhash.hash_password(TEST_PASSWORD, salt)
    firmware.cfg["admin_email"] = TEST_EMAIL
    firmware.cfg["admin_salt"] = salt
    firmware.cfg["admin_hash"] = digest
    firmware.cfg["admin_rounds"] = rounds

    session = {}

    def call(method, path, body=b"", auth=True):
        request = rig_request(body, method, auth and session or None)
        return firmware.server.routes[(method, path)](request)

    def rig_request(body, method, cookies):
        return FakeRequest(body, method, cookies=cookies or {})

    def sign_in(email=TEST_EMAIL, password=TEST_PASSWORD):
        response = firmware.server.routes[("POST", "/api/login")](
            FakeRequest({"email": email, "password": password}, "POST")
        )
        cookie = response.headers.get("Set-Cookie", "")
        if "tree_session=" in cookie and response.status[0] == 200:
            token = cookie.split("tree_session=")[1].split(";")[0]
            if token:
                session["tree_session"] = token
        return response

    # ------------------------------------------------------------- auth ---
    check("starts signed out", call("GET", "/api/state").data["authed"] is False)
    check("wifi password is hidden from a guest",
          call("GET", "/api/state").data["config"]["ap_password"] == "",
          repr(call("GET", "/api/state").data["config"]["ap_password"]))
    check("admin email is not a secret",
          call("GET", "/api/state").data["config"]["admin_email"] == TEST_EMAIL)
    check("password hash is never handed out",
          call("GET", "/api/state").data["config"]["admin_hash"] == "")

    check("a guest cannot reshape the tree",
          call("POST", "/api/config", {"turns": 20}).status[0] == 401,
          call("POST", "/api/config", {"turns": 20}).status)
    check("and the tree is unchanged", firmware.geo.turns == 12, firmware.geo.turns)
    check("a guest cannot run the test patterns",
          call("POST", "/api/test", {"kind": "front_line"}).status[0] == 401)
    check("a guest cannot restart the board",
          call("POST", "/api/restart", {}).status[0] == 401)
    check("a guest cannot change the password",
          call("POST", "/api/password", {"current": "x"}).status[0] == 401)
    check("but a guest may change the idle pattern",
          call("POST", "/api/config", {"idle_pattern": "snowfall"}).status[0] == 200)
    check("and may play",
          call("POST", "/api/frame", bytes([1, 1, 1] * 300)).status[0] == 200)
    check("a guest mixing play and setup keys is refused whole",
          call("POST", "/api/config", {"idle_speed": 2.0, "turns": 9}).status[0] == 401
          and firmware.cfg["idle_speed"] != 2.0,
          firmware.cfg["idle_speed"])

    check("the wrong password is refused", sign_in(password="nope").status[0] == 401)
    check("the right password with the wrong email is refused",
          sign_in(email="someone@else.com").status[0] == 401)
    check("no session was handed out", call("GET", "/api/state").data["authed"] is False)

    # five failures in a row should hold the door shut
    for _ in range(5):
        sign_in(password="nope")
    locked = sign_in()
    check("too many attempts locks the login", locked.status[0] == 429, locked.status)
    firmware.locked_until = 0.0
    del firmware.login_failures[:]

    response = sign_in()
    check("signing in works", response.status[0] == 200 and session.get("tree_session"),
          response.status)
    check("the cookie is HttpOnly and same-site",
          "HttpOnly" in response.headers["Set-Cookie"]
          and "SameSite=Strict" in response.headers["Set-Cookie"],
          response.headers["Set-Cookie"])
    check("now signed in", call("GET", "/api/state").data["authed"] is True)
    check("and the wifi password comes back",
          call("GET", "/api/state").data["config"]["ap_password"] != "")
    check("the hash was re-stretched for this board",
          firmware.cfg["admin_rounds"] == pwhash.ROUNDS, firmware.cfg["admin_rounds"])
    check("and the old password still works after re-stretching",
          pwhash.verify_password(TEST_PASSWORD, firmware.cfg["admin_salt"],
                                 firmware.cfg["admin_hash"], firmware.cfg["admin_rounds"]))

    check("a made-up cookie is not a session",
          firmware.server.routes[("GET", "/api/state")](
              FakeRequest(b"", "GET", cookies={"tree_session": "0" * 48})
          ).data["authed"] is False)

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
    check("frame accepted", response.status[0] == 200, response.status)
    check("frame reply carries the button queue", "presses" in response.data, response.data)
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

    # Two 300-LED reels chained together.
    call("POST", "/api/config", {"num_pixels": 300, "turns": 12, "loop_counts": [], "start_offset": 0})
    call("POST", "/api/config", {"num_pixels": 600})
    check("a second reel doubles the strip", firmware.pixels.count == 600 and firmware.geo.n == 600,
          "%d pixels" % firmware.pixels.count)
    check("all 600 are spread over the levels",
          sum(firmware.geo.counts) == 600 and firmware.geo.used == 600,
          "%d in levels, used to %d" % (sum(firmware.geo.counts), firmware.geo.used))
    firmware.last_render_at = 0
    firmware.mode = "idle"
    firmware.show.set_pattern("solid", 0.0)
    firmware.step(4100.0)
    check("the far end of the second reel lights up",
          sum(firmware.pixels.buffer[599]) > 0, firmware.pixels.buffer[599])
    body = bytes([3, 4, 5] * 600)
    call("POST", "/api/frame", body)
    check("a 600-pixel frame is accepted whole",
          firmware.pixels.buffer[599] == (3, 4, 5), firmware.pixels.buffer[599])
    call("POST", "/api/config", {"num_pixels": 300})

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

    # -------------------------------------------------- raw level setup ---
    state = call("POST", "/api/config", {"turns": 3, "loop_counts": [20, 17, 15]}).data
    check("raw level lengths applied", firmware.geo.level_counts == [20, 17, 15],
          firmware.geo.level_counts)
    check("levels can use less than the whole strip",
          firmware.geo.used == 52 and firmware.geo.n == 300,
          "used %d of %d" % (firmware.geo.used, firmware.geo.n))
    check("not treated as automatic", firmware.geo.auto_counts is False)

    firmware.last_render_at = 0
    firmware.mode = "idle"
    firmware.show.set_pattern("solid", 0.0)
    firmware.step(9000.0)
    tail = [c for c in firmware.pixels.buffer[52:] if sum(c) > 0]
    on_tree = [c for c in firmware.pixels.buffer[:52] if sum(c) > 0]
    check("spare pixels past the last level stay dark", not tail,
          "%d of %d lit" % (len(tail), 300 - 52))
    check("the levels themselves light up", len(on_tree) == 52, len(on_tree))

    # level 0 must be the bottom whichever end of the strip starts there
    call("POST", "/api/config", {"start_at_bottom": False})
    heights = firmware.geo.height_of
    lowest = min(range(firmware.geo.turns), key=lambda k: heights[k])
    check("level 0 stays at the bottom when the strip starts at the top",
          firmware.geo.counts[lowest] == 20 and firmware.geo.level_counts == [20, 17, 15],
          "strip order %s, bottom loop has %d" % (firmware.geo.counts, firmware.geo.counts[lowest]))
    call("POST", "/api/config", {"start_at_bottom": True})

    # a set that does not fit is refused and the cone shape comes back
    state = call("POST", "/api/config", {"turns": 3, "loop_counts": [200, 200, 200]}).data
    check("levels that overrun the strip are refused",
          firmware.geo.auto_counts is True and state["config"]["loop_counts"] == [],
          firmware.geo.counts)

    # a lead-in and a tail at the same time
    call("POST", "/api/config", {"turns": 3, "start_offset": 10, "loop_counts": [20, 17, 15]})
    check("lead-in and spare tail together",
          firmware.geo.offset == 10 and firmware.geo.used == 62,
          "offset %d, used %d" % (firmware.geo.offset, firmware.geo.used))
    firmware.last_render_at = 0
    firmware.step(9100.0)
    check("neither lead-in nor tail is ever lit",
          not any(sum(c) for c in firmware.pixels.buffer[:10])
          and not any(sum(c) for c in firmware.pixels.buffer[62:]),
          "lead-in %s tail %s" % (firmware.pixels.buffer[:2], firmware.pixels.buffer[62:64]))

    grid = firmware.geo.build_grid(8)
    cells = {i for row in grid for cell in row for i in cell}
    check("the game grid uses only pixels on the tree",
          min(cells) == 10 and max(cells) == 61 and len(cells) == 52,
          "%d..%d (%d)" % (min(cells), max(cells), len(cells)))

    call("POST", "/api/config", {"turns": 12, "start_offset": 0, "loop_counts": []})

    # ---------------------------------------------------------- buttons ---
    check("buttons off by default", not firmware.controls.active)
    check("no input queued when there are no buttons", call("GET", "/api/input").data["presses"] == [])

    state = call("POST", "/api/config", {"buttons_enabled": True}).data
    check("buttons claim their pins", firmware.controls.active, sorted(firmware.controls.pins))
    check(
        "all five buttons wired",
        sorted(firmware.controls.pins) == ["down", "left", "right", "select", "up"],
        sorted(firmware.controls.pins),
    )
    check("no warnings for a clean wiring", firmware.controls.warnings == [], firmware.controls.warnings)
    check("state reports the buttons", state["buttons"]["active"] is True, state["buttons"])

    # a pin that clashes with the strip is refused, not fatal
    state = call("POST", "/api/config", {"button_up": "GP0"}).data
    check(
        "strip pin refused for a button",
        "up" not in firmware.controls.pins and len(firmware.controls.warnings) == 1,
        firmware.controls.warnings,
    )
    state = call("POST", "/api/config", {"button_up": "GP44"}).data
    check(
        "unknown pin refused with a warning",
        "up" not in firmware.controls.pins and firmware.controls.warnings,
        firmware.controls.warnings,
    )
    call("POST", "/api/config", {"button_up": "GP2"})
    check("good pin restored", "up" in firmware.controls.pins, sorted(firmware.controls.pins))

    def tap(label, at, hold=0.1):
        """Press and release a button across enough polls to debounce."""
        io = firmware.controls.pins[label]
        io.press()
        firmware.handle_buttons(at)
        firmware.handle_buttons(at + 0.05)
        io.release()
        firmware.handle_buttons(at + hold)
        firmware.handle_buttons(at + hold + 0.05)

    # --- idle: left/right change pattern, up/down change brightness ---
    firmware.mode = "idle"
    firmware.show.set_pattern("rainbow_spiral", 0.0)
    before = firmware.show.name
    tap("right", 5000.0)
    check("right steps to the next pattern", firmware.show.name != before,
          before + " -> " + firmware.show.name)
    forward = firmware.show.name
    tap("left", 5010.0)
    check("left steps back", firmware.show.name == before, firmware.show.name)

    firmware.cfg["idle_brightness"] = 0.5
    tap("up", 5020.0)
    check("up raises brightness", firmware.cfg["idle_brightness"] > 0.5, firmware.cfg["idle_brightness"])
    tap("down", 5030.0)
    tap("down", 5040.0)
    check("down lowers brightness", firmware.cfg["idle_brightness"] < 0.55, firmware.cfg["idle_brightness"])

    brightness_floor = firmware.cfg["idle_brightness"]
    for k in range(40):
        tap("down", 5100.0 + k)
    check("brightness cannot go to zero", firmware.cfg["idle_brightness"] >= 0.02,
          firmware.cfg["idle_brightness"])

    # --- select starts the on-board game ---
    tap("select", 6000.0)
    check("select starts the on-board game", firmware.onboard_game is not None and firmware.mode == "game",
          firmware.mode)

    firmware.last_render_at = 0
    firmware.step(6001.0)
    lit = sum(1 for c in firmware.pixels.buffer if sum(c) > 0)
    check("on-board game draws on the strip", lit > 0, "%d lit" % lit)

    game = firmware.onboard_game
    check("game grid matches the tree", len(game.grid) == firmware.geo.turns
          and len(game.grid[0]) == firmware.cfg["cols"],
          "%dx%d" % (len(game.grid), len(game.grid[0])))
    check("every game cell has a pixel", all(cell for row in game.grid for cell in row))

    # directions reach the game
    game.body = [(2, 2)]
    game.direction = "right"
    tap("up", 6010.0)
    check("buttons steer the on-board game", game.pending == "up" or game.direction == "up",
          "%s/%s" % (game.pending, game.direction))

    # the spiral wrap climbs a layer, like the strip does
    game.spiral = True
    check("going past the front climbs a layer",
          game._step((3, game.cols - 1), "right") == (4, 0),
          game._step((3, game.cols - 1), "right"))
    game.spiral = False
    check("ring mode stays on its layer",
          game._step((3, game.cols - 1), "right") == (3, 0),
          game._step((3, game.cols - 1), "right"))

    # On the default difficulty the top of the tree joins the bottom, so
    # running off the top wraps round instead of ending the game.  The walls
    # only come back on "hard" -- see the difficulty checks below.
    game.spiral = True
    game.body = [(game.rows - 1, 0)]
    game.direction = "up"
    game.dead_at = 0.0
    game._advance(6100.0)
    check("running off the top wraps round by default",
          game.dead_at == 0 and game.body[0][0] == 0,
          "head now at %s" % (game.body[0],))

    # --- difficulty ---
    for level, wraps, self_kills in (("gentle", True, False),
                                     ("normal", True, True),
                                     ("hard", False, True)):
        call("POST", "/api/config", {"snake_difficulty": level})
        fresh = firmware.snake_game.Snake(
            firmware.geo, firmware.cfg["cols"], True, firmware.cfg["snake_difficulty"]
        )
        rows = fresh.rows
        fresh.body = [(rows - 1, 2)]
        fresh.direction = "up"
        fresh.dead_at = 0.0
        fresh._advance(100.0)
        survived_top = fresh.dead_at == 0.0

        doubled = firmware.snake_game.Snake(
            firmware.geo, firmware.cfg["cols"], True, firmware.cfg["snake_difficulty"]
        )
        doubled.body = [(2, 2), (1, 2), (1, 3), (2, 3)]
        doubled.direction = "down"
        doubled.dead_at = 0.0
        doubled._advance(100.0)
        bit_itself = doubled.dead_at > 0.0

        check(
            "on-board snake: %s" % level,
            survived_top == wraps and bit_itself == self_kills,
            "wraps=%s self-kills=%s" % (survived_top, bit_itself),
        )

    check("difficulty is rejected if unknown",
          call("POST", "/api/config", {"snake_difficulty": "sideways"}).data["config"]["snake_difficulty"] == "normal")
    call("POST", "/api/config", {"snake_difficulty": "normal"})

    # the board's game picks the setting up when it starts
    firmware.mode = "idle"
    call("POST", "/api/config", {"snake_difficulty": "hard"})
    tap("select", 6500.0)
    check("on-board game uses the configured difficulty",
          firmware.onboard_game is not None
          and firmware.onboard_game.rules is firmware.tree.SNAKE_DIFFICULTIES["hard"],
          firmware.cfg["snake_difficulty"])
    call("POST", "/api/config", {"snake_difficulty": "normal"})
    firmware.mode = "idle"

    # --- holding select leaves the game ---
    io = firmware.controls.pins["select"]
    io.press()
    firmware.handle_buttons(6200.0)
    firmware.handle_buttons(6200.05)
    firmware.handle_buttons(6201.0)   # past the hold threshold
    io.release()
    firmware.handle_buttons(6201.2)
    check("holding select leaves the on-board game",
          firmware.onboard_game is None and firmware.mode == "idle", firmware.mode)

    # --- presses are queued for the web app while a phone is driving ---
    del firmware.input_queue[:]
    call("POST", "/api/frame", bytes([5, 5, 5] * firmware.geo.n))
    check("phone takes over", firmware.mode == "live", firmware.mode)
    tap("left", 7000.0)
    tap("select", 7010.0)
    queued = call("GET", "/api/input").data["presses"]
    check("presses queued for the web app", queued == ["left", "select"], queued)
    check("queue drains on read", call("GET", "/api/input").data["presses"] == [])

    # ... and the same queue rides back on a frame POST, so a phone that is
    # driving needs no extra request to read its controller.
    tap("right", 7020.0)
    reply = call("POST", "/api/frame", bytes([2, 2, 2] * firmware.geo.n)).data
    check("frame reply returns the presses", reply["presses"] == ["right"], reply)
    check("frame reply drains the queue",
          call("POST", "/api/frame", bytes([2, 2, 2] * firmware.geo.n)).data["presses"] == [])

    firmware.mode = "live"
    firmware.show.set_pattern("rainbow_spiral", 0.0)
    held = firmware.show.name
    tap("right", 7100.0)
    check("buttons do not change the board while a phone is driving",
          firmware.show.name == held, firmware.show.name)

    # stale presses are dropped rather than replayed
    del firmware.input_queue[:]
    firmware.input_queue.append((0.0, "up"))
    check("stale presses are dropped", call("GET", "/api/input").data["presses"] == [])

    # a phone frame stops the on-board game
    firmware.mode = "idle"
    tap("select", 7200.0)
    check("game started again", firmware.onboard_game is not None)
    call("POST", "/api/frame", bytes([1, 1, 1] * firmware.geo.n))
    check("a phone frame stops the on-board game",
          firmware.onboard_game is None and firmware.mode == "live", firmware.mode)

    # turning the buttons off releases the pins
    call("POST", "/api/config", {"buttons_enabled": False})
    check("disabling the buttons frees the pins", not firmware.controls.active
          and all(io.deinited for io in FakePin.instances.values() if io.pin.startswith("GP")
                  and io.pin != "GP0"),
          sorted(firmware.controls.pins))

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
