# Christmas tree lights

One LED strip wrapped in a spiral around a tree, driven by a Raspberry Pi
Pico 2 W running CircuitPython.

* **Nobody connected** — the tree runs an idle pattern on its own.
* **Phone connected** — the phone takes over and pushes frames: games,
  finger painting, photos.
* **Admin tab** — tree shape, per-loop front calibration, brightness,
  wifi.

Stop playing and the idle show comes back on its own after a few seconds.

```
   ┌─ phone / browser ──────────────┐        ┌─ Pico 2 W ──────────────┐
   │ games, drawing, photo mapping  │ frames │ geometry + calibration  │
   │ (all the per-pixel maths)      ├───────►│ idle patterns           │
   │                                │◄───────┤ 300 WS2812 pixels       │
   └────────────────────────────────┘ mirror └─────────────────────────┘
```

The browser does the heavy per-pixel work and posts a finished frame; the
board keeps the geometry, the calibration and the idle animations. That
split is what makes the games smooth on a microcontroller, and it means the
tree still does something pretty when no one is holding a phone.

## Getting it running

1. Copy everything in this repository onto the `CIRCUITPY` drive — this
   repository *is* the drive's contents.
2. Wire the strip's data line to **GP0**, ground to **GND**, and power the
   strip from its own 5 V supply with its ground tied to the Pico's.
3. Eject the drive and power the board from a charger or battery.
4. On your phone, join the wifi network **ChristmasTree** (password
   `jingle123`) and open <http://192.168.4.1/>.

Change the hotspot name and password, or join your home wifi instead, in
**Admin → Wifi**. After a wifi or pin change, press **Restart tree**.

> Settings are saved to `config.json` on the board's own flash, which
> CircuitPython can only write to when a computer is *not* holding the
> `CIRCUITPY` drive (`boot.py` handles the switch). Plugged into a laptop,
> changes still apply but are not saved, and the app says so.

## Telling it about your tree

Go to **Admin**.

**Rotations around the tree** is the only number you really have to get
right: count how many times the strip goes round. The board shares the
pixels out between those loops, giving the lower loops more pixels than the
higher ones, because the tree is a cone — **Top width** sets how much
narrower the top is. The little bar chart shows the resulting loop lengths.

### Centring each loop on the front

The strip is a spiral, so each loop starts at a different angle. Without
telling the board where "the front" is on every loop, a picture would shear
sideways as it climbs.

Under **Centre each loop on the front** one pixel lights up. Slide until it
is pointing straight at the front of the tree, press **Save front**, and the
wizard moves on to the next loop. Do them all, or do a handful and press
**Fill in the rest** — hand-wrapped strips drift gradually, so the loops in
between can be interpolated from the ones you set.

Then press **Front line** under *Check it*: it lights one pixel per loop,
all at the front. When the calibration is right they form a straight
vertical line up the front of the tree. **Quadrants** paints front red,
right green, back blue and left yellow, which is the quickest way to see
whether `Winds clockwise` is the right way round.

The calibration survives a change to the rotation count — the angles are
re-interpolated onto the new loops — but it is worth re-checking the front
line afterwards.

## What you can do with it

| Tab | What it does |
| --- | --- |
| **Play** | Snake, Rings, Dodge, Simon |
| **Draw** | Finger-paint straight onto the tree |
| **Photo** | Put a picture on the tree |
| **Patterns** | Pick and tune the idle show |
| **Admin** | Shape, calibration, hardware, wifi |

The tree drawing at the top of the page is live: while you are playing it
shows what you are sending, and the rest of the time it mirrors whatever the
board is doing by itself.

### Snake

The tree is treated as a cylinder of cells, one row per loop. **▲** and
**▼** jump a whole layer. **◀** and **▶** go round the tree — and when you
pass the front you climb to the next layer, the same way the strip itself
does. Set `Snake stays on its ring` in **Admin → Games** if you would rather
it wrapped on the spot.

You can also swipe on the tree picture instead of using the pad, and use the
arrow keys on a laptop.

### Rings

Tetris on a cylinder. Blocks fall down the tree and wrap around it; fill a
complete ring and it clears. **▲** rotates, **▼** drops.

### Photos

300 pixels is a very small screen, so bold, high-contrast pictures work far
better than detailed ones. Two ways to map one:

* **Front** (default) projects the picture onto the half of the tree facing
  you, *corrected for the cone*: a pixel's horizontal position on screen is
  `radius × sin(angle)`, not the angle itself, so the picture is not
  stretched at the edges and still reads as itself when you stand in front
  of the tree. The back of the tree gets a dim wash of the same colours.
* **Wrap** wraps the picture the whole way round, for patterns and
  gradients rather than pictures of things.

The **Spin** slider rotates the picture around the tree continuously, and
**Turn** nudges where it starts.

### Physical buttons (optional)

Bluetooth is not an option here: the Pico 2 W's radio has BLE silicon, but
CircuitPython does not expose it — the `_bleio` docs say Pico W boards
support BLE only through an *external* AirLift co-processor, and the
[feature request](https://github.com/adafruit/circuitpython/issues/7693) has
been open and unassigned since 2023. Five wired buttons do the same job with
no extra library and nothing to pair or charge.

Wire each momentary switch between its pin and **ground** — the internal
pull-ups mean no resistors. The defaults are GP2–GP6, changeable in
**Admin → Buttons**, which also reports any pin it could not claim.

| | While the tree runs its own show | While a phone is playing |
| --- | --- | --- |
| **◀ ▶** | previous / next idle pattern | controller for the phone's game |
| **▲ ▼** | brightness down / up | controller for the phone's game |
| **select** | start Snake on the tree | the game's action button |
| **hold select** | leave the game / all off | — |

So the buttons work two ways. On their own they drive the board directly,
including a full game of Snake with no phone involved — the on-board game
uses the same grid as the browser's, built by `Geometry.build_grid()`, so
the two behave identically. When a phone *is* driving, the presses are
queued and the web app collects them from `/api/input`, which turns the
buttons into a handheld controller for the phone's games.

Pattern and brightness changes made with the buttons are written to flash
about twenty seconds after the last press, so holding a direction does not
wear the filesystem out.

Directions auto-repeat when held; `select` does not, so a hold is always a
distinct gesture.

### Idle patterns

Sixteen of them: Rainbow Spiral, Candy Cane, Twinkle Stars, Snowfall,
Fireplace, Rising Rings, Spiral Comet, Meteor Shower, Plasma, Aurora,
Breathe, Spiral Wipe, Ring Chase, Classic String, Warm Glow and Solid.
Speed, brightness and palette are adjustable, and *Cycle through patterns*
rotates them on a timer.

These run on the board, so they keep going once you close the page.

## Files

| File | What it is |
| --- | --- |
| `code.py` | Boot, wifi, HTTP API, main loop |
| `tree.py` | Config, the geometry model (per-pixel angle/height tables) and the game grid |
| `patterns.py` | The idle animations |
| `buttons.py` | Debounced reader for the optional GPIO buttons |
| `snake.py` | Snake played on the board itself, with the buttons |
| `boot.py` | Makes the flash writable when no computer is attached |
| `www/` | The web app (`app.js` core, `games.js`, `paint.js`, `admin.js`) |
| `tools/simulator.py` | Runs the whole web app on a laptop, no hardware |
| `tools/test_firmware.py` | Tests `code.py` against mock hardware |

### Developing without the tree

```sh
python3 tools/simulator.py      # http://localhost:8080/
python3 tools/test_firmware.py  # checks the real code.py
```

The simulator serves the real `www/` files and re-implements the board's API
on top of the real `tree.py` and `patterns.py`, so geometry, calibration and
the idle patterns behave exactly as they do on hardware — only the LEDs are
faked. It also exposes `GET /api/frame`, which is how the browser tests
assert what ended up on the tree. **Its routes mirror `code.py`, so if you
add an endpoint to one, add it to the other.**

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/state` | Mode, config, pattern list, free memory |
| `GET` | `/api/geometry` | Loop lengths and the per-pixel angle/height tables (hex) |
| `GET` | `/api/frame` | The frame currently on the tree, as hex |
| `GET` | `/api/input` | Drain queued button presses (used as a controller by the web app) |
| `POST` | `/api/frame` | Show a frame: `3 × num_pixels` raw bytes, or the same as hex |
| `POST` | `/api/config` | Apply and save a partial config update |
| `POST` | `/api/mode` | `{"mode": "idle"}` or `{"mode": "off"}` |
| `POST` | `/api/test` | Calibration dot, front line, ring, quadrants, markers |
| `POST` | `/api/restart` | Reload CircuitPython |

```sh
# paint every pixel dim red, from anywhere on the network
python3 -c "import sys; sys.stdout.write('200000'*300)" \
  | curl -s --data-binary @- http://192.168.4.1/api/frame
```

A frame holds the tree until another arrives; four seconds of silence and
the idle show takes back over.

## Notes and limits

* **Power.** 300 WS2812s at full white is around 18 A. The default
  brightness is 45 %, and the games and patterns are mostly dark, but drive
  the strip from a proper 5 V supply rather than the Pico's USB rail.
* **Frame rate.** Frames are posted at about 15 per second, which is plenty
  for these games over a local hotspot. The idle patterns render on the
  board at the frame rate set in Admin (30 fps by default); `Fireplace` is
  the most expensive, so drop the rate if you add a much longer strip.
* **Columns vs pixels.** The game grid is 16 columns wide by default, but
  the top loop of a 300-pixel, 12-rotation tree only has 9 pixels. Cells
  with no pixel of their own borrow the nearest one in the same loop, so
  nothing is invisible — neighbouring columns just share a light near the
  top. Admin tells you when that is happening.
* **One player at a time.** Whoever posted the most recent frame owns the
  tree.
* **Memory.** Admin shows the board's free memory. If you extend this a lot
  further and run short, compile the modules with `mpy-cross` (`tree.mpy`,
  `patterns.mpy`) — the `www/` files cost nothing, as they are streamed
  straight from flash.
