"""Idle animations for the tree.

These run on the Pico itself, so they are written for speed: a 256-entry sine
table and a 256-entry rainbow table replace calls to ``math``, and every
pattern works from the pre-computed per-pixel ``u`` (angle) / ``v`` (height)
tables in :mod:`tree`.  Patterns that are really sprites (snow, fire) render
into a coarse ``GRID_W x GRID_H`` grid and then blit it onto the strip using
one lookup per pixel.

Global brightness is deliberately *not* applied here -- it is left to the
NeoPixel object, which scales in native code for free.
"""

import math
import random

from tree import GRID_H, GRID_W


def hsv(hue, sat=255, val=255):
    """Integer HSV -> RGB.  All arguments and results are 0..255."""
    hue &= 255
    region = hue // 43
    remainder = (hue - region * 43) * 6
    p = (val * (255 - sat)) >> 8
    q = (val * (255 - ((sat * remainder) >> 8))) >> 8
    t = (val * (255 - ((sat * (255 - remainder)) >> 8))) >> 8
    if region == 0:
        return (val, t, p)
    if region == 1:
        return (q, val, p)
    if region == 2:
        return (p, val, t)
    if region == 3:
        return (p, q, val)
    if region == 4:
        return (t, p, val)
    return (val, p, q)


SIN8 = bytearray(256)
for _i in range(256):
    SIN8[_i] = int(127.5 + 127.4 * math.sin(_i * math.pi / 128.0))

RAINBOW = [hsv(_h) for _h in range(256)]

#: Heat -> colour ramp for the fire pattern (black / red / orange / white).
FIRE = []
for _i in range(256):
    if _i < 85:
        FIRE.append((_i * 3, 0, 0))
    elif _i < 170:
        FIRE.append((255, (_i - 85) * 3, 0))
    else:
        FIRE.append((255, 255, (_i - 170) * 3))

BLACK = (0, 0, 0)

PALETTES = {
    "classic": [(255, 20, 20), (20, 200, 40), (255, 240, 200), (255, 170, 20)],
    "icy": [(20, 60, 255), (0, 200, 255), (235, 245, 255), (120, 140, 255)],
    "warm": [(255, 140, 20), (255, 200, 120), (255, 90, 0), (255, 235, 190)],
    "candy": [(255, 20, 40), (255, 255, 255), (255, 110, 160), (200, 0, 60)],
    "forest": [(10, 190, 60), (0, 140, 120), (210, 255, 220), (90, 220, 30)],
    "jewel": [(190, 0, 255), (255, 0, 120), (0, 120, 255), (255, 200, 0)],
    "rainbow": [RAINBOW[0], RAINBOW[64], RAINBOW[128], RAINBOW[192]],
}

#: (name, human label) in the order the admin UI should list them.
CATALOGUE = (
    ("rainbow_spiral", "Rainbow Spiral"),
    ("candy_cane", "Candy Cane"),
    ("twinkle_stars", "Twinkle Stars"),
    ("snowfall", "Snowfall"),
    ("fire", "Fireplace"),
    ("rising_rings", "Rising Rings"),
    ("comet", "Spiral Comet"),
    ("meteors", "Meteor Shower"),
    ("plasma", "Plasma"),
    ("aurora", "Aurora"),
    ("breathe", "Breathe"),
    ("spiral_wipe", "Spiral Wipe"),
    ("ring_chase", "Ring Chase"),
    ("classic_string", "Classic String"),
    ("warm_glow", "Warm Glow"),
    ("solid", "Solid Colour"),
)

NAMES = tuple(name for name, _ in CATALOGUE)


class Patterns:
    """Renders the idle animations into a flat list of RGB tuples."""

    def __init__(self, geometry, cfg):
        self.cfg = cfg
        self.name = cfg.get("idle_pattern", NAMES[0])
        if self.name not in NAMES:
            self.name = NAMES[0]
        self.t0 = 0.0
        self.set_geometry(geometry)

    # -- setup -----------------------------------------------------------
    def set_geometry(self, geometry):
        self.geo = geometry
        n = geometry.n
        self.n = n
        self.levels = bytearray(n)
        self.heat = bytearray(GRID_W * GRID_H)
        self.grid = bytearray(GRID_W * GRID_H)
        self.trail = bytearray(n)
        self.flakes = [
            [random.uniform(0, GRID_W), random.uniform(0, GRID_H), random.uniform(0.25, 0.7)]
            for _ in range(14)
        ]
        self.comets = [
            [random.uniform(0, n), random.uniform(0.25, 0.8), random.randrange(256)]
            for _ in range(3)
        ]
        self.string = bytearray(n)
        palette = self.palette()
        for i in range(n):
            self.string[i] = random.randrange(len(palette))
        self.head = 0.0

    def palette(self):
        return PALETTES.get(self.cfg.get("idle_palette", "classic"), PALETTES["classic"])

    def set_pattern(self, name, now=0.0):
        if name in NAMES:
            self.name = name
            self.cfg["idle_pattern"] = name
            self.t0 = now
            for i in range(self.n):
                self.trail[i] = 0
                self.levels[i] = 0
            for i in range(len(self.heat)):
                self.heat[i] = 0

    # -- main entry point ------------------------------------------------
    def render(self, out, now):
        t = (now - self.t0) * float(self.cfg.get("idle_speed", 1.0))
        handler = getattr(self, "_p_" + self.name, None)
        if handler is None:
            handler = self._p_rainbow_spiral
        handler(out, t)

    # -- helpers ---------------------------------------------------------
    def _blit(self, out, ramp):
        """Copy the coarse grid onto the strip through a brightness ramp."""
        grid = self.grid
        cells = self.geo.grid_of
        for i in range(self.n):
            out[i] = ramp[grid[cells[i]]]

    @staticmethod
    def _ramp(color):
        r, g, b = color
        return [((r * k) >> 8, (g * k) >> 8, (b * k) >> 8) for k in range(256)]

    # -- gradient patterns ----------------------------------------------
    def _p_rainbow_spiral(self, out, t):
        u, v = self.geo.u, self.geo.v
        phase = int(t * 45.0) & 255
        rb = RAINBOW
        for i in range(self.n):
            out[i] = rb[(u[i] + (v[i] >> 1) + phase) & 255]

    def _p_candy_cane(self, out, t):
        u, v = self.geo.u, self.geo.v
        phase = int(t * 40.0) & 255
        stripe = self.palette()[0]
        white = (255, 245, 235)
        for i in range(self.n):
            x = (u[i] + 3 * v[i] + phase) & 255
            out[i] = stripe if (x >> 5) & 1 else white

    def _p_plasma(self, out, t):
        u, v = self.geo.u, self.geo.v
        p1 = int(t * 30.0) & 255
        p2 = int(t * 17.0) & 255
        sin8, rb = SIN8, RAINBOW
        for i in range(self.n):
            a = sin8[(2 * u[i] + p1) & 255]
            b = sin8[(3 * v[i] - p2) & 255]
            out[i] = rb[(a + b) >> 1]

    def _p_aurora(self, out, t):
        u, v = self.geo.u, self.geo.v
        p1 = int(t * 14.0) & 255
        p2 = int(t * 9.0) & 255
        sin8 = SIN8
        for i in range(self.n):
            wave = sin8[(u[i] + p1) & 255]
            band = sin8[(2 * v[i] - p2 + (wave >> 2)) & 255]
            out[i] = (
                (band * band) >> 11,
                (band * 200) >> 8,
                (band * (120 + (wave >> 2))) >> 9,
            )

    def _p_rising_rings(self, out, t):
        v = self.geo.v
        phase = int(t * 60.0) & 255
        hue = int(t * 12.0) & 255
        r, g, b = RAINBOW[hue]
        sin8 = SIN8
        for i in range(self.n):
            level = sin8[(3 * v[i] - phase) & 255]
            level = (level * level) >> 8  # sharpen the rings
            out[i] = ((r * level) >> 8, (g * level) >> 8, (b * level) >> 8)

    def _p_warm_glow(self, out, t):
        phase = int(t * 12.0) & 255
        sin8 = SIN8
        for i in range(self.n):
            level = 200 + (sin8[(7 * i + phase) & 255] >> 3)
            out[i] = (level, (level * 150) >> 8, (level * 70) >> 8)

    def _p_breathe(self, out, t):
        palette = self.palette()
        color = palette[int(t * 0.12) % len(palette)]
        level = 25 + ((SIN8[int(t * 28.0) & 255] * 230) >> 8)
        r, g, b = color
        value = ((r * level) >> 8, (g * level) >> 8, (b * level) >> 8)
        for i in range(self.n):
            out[i] = value

    def _p_solid(self, out, _t):
        value = self.palette()[0]
        for i in range(self.n):
            out[i] = value

    # -- sparkle patterns ------------------------------------------------
    def _p_twinkle_stars(self, out, _t):
        levels = self.levels
        n = self.n
        for _ in range(max(1, n // 60)):
            levels[random.randrange(n)] = 255
        for i in range(n):
            level = levels[i]
            if level > 6:
                levels[i] = level - 6
            else:
                levels[i] = 0
            level = levels[i]
            out[i] = (
                8 + ((level * 247) >> 8),
                6 + ((level * 195) >> 8),
                14 + ((level * 145) >> 8),
            )

    def _p_classic_string(self, out, t):
        palette = self.palette()
        string, levels = self.string, self.levels
        n = self.n
        phase = int(t * 8.0)
        for _ in range(max(1, n // 40)):
            index = random.randrange(n)
            levels[index] = 255
            if random.random() < 0.25:
                string[index] = random.randrange(len(palette))
        for i in range(n):
            level = levels[i]
            target = 90 + (SIN8[(i * 11 + phase) & 255] >> 2)
            if level > target:
                level -= 7
                levels[i] = level
            elif level < target:
                levels[i] = level + 4
                level = levels[i]
            r, g, b = palette[string[i]]
            out[i] = ((r * level) >> 8, (g * level) >> 8, (b * level) >> 8)

    # -- travelling patterns ---------------------------------------------
    def _p_comet(self, out, t):
        n = self.n
        trail = self.trail
        self.head = (self.head + 0.9 + 2.4 * float(self.cfg.get("idle_speed", 1.0))) % n
        head = int(self.head)
        for i in range(n):
            level = trail[i]
            if level > 10:
                trail[i] = level - 10
            else:
                trail[i] = 0
        for k in range(3):
            trail[(head - k) % n] = 255
        r, g, b = RAINBOW[int(t * 10.0) & 255]
        for i in range(n):
            level = trail[i]
            out[i] = ((r * level) >> 8, (g * level) >> 8, (b * level) >> 8)

    def _p_meteors(self, out, _t):
        n = self.n
        trail = self.trail
        for i in range(n):
            level = trail[i]
            if level > 14:
                trail[i] = level - 14
            else:
                trail[i] = 0
        for comet in self.comets:
            comet[0] += comet[1] * 6.0
            if comet[0] >= n:
                comet[0] -= n
                comet[2] = random.randrange(256)
                comet[1] = random.uniform(0.25, 0.9)
            trail[int(comet[0])] = 255
        hue_of = self.comets[0][2]
        r, g, b = RAINBOW[hue_of]
        for i in range(n):
            level = trail[i]
            out[i] = ((r * level) >> 8, (g * level) >> 8, (b * level) >> 8)

    def _p_spiral_wipe(self, out, t):
        n = self.n
        palette = self.palette()
        cycle = t * 0.35
        color = palette[int(cycle) % len(palette)]
        edge = int((cycle % 1.0) * n)
        previous = palette[(int(cycle) - 1) % len(palette)]
        for i in range(n):
            out[i] = color if i < edge else previous

    def _p_ring_chase(self, out, t):
        turns = self.geo.turns
        loop_of = self.geo.loop_of
        position = (t * 1.6) % turns
        hue = int(t * 20.0) & 255
        r, g, b = RAINBOW[hue]
        for i in range(self.n):
            distance = abs(loop_of[i] - position)
            if distance > turns / 2:
                distance = turns - distance
            if distance >= 2.0:
                out[i] = BLACK
            else:
                level = int(255 * (1.0 - distance / 2.0))
                out[i] = ((r * level) >> 8, (g * level) >> 8, (b * level) >> 8)

    # -- grid / sprite patterns ------------------------------------------
    def _p_snowfall(self, out, _t):
        grid = self.grid
        for i in range(len(grid)):
            level = grid[i]
            grid[i] = level - 20 if level > 20 else 0
        for flake in self.flakes:
            flake[1] -= flake[2]
            if flake[1] < 0:
                flake[1] += GRID_H
                flake[0] = random.uniform(0, GRID_W)
                flake[2] = random.uniform(0.25, 0.7)
            flake[0] = (flake[0] + random.uniform(-0.15, 0.15)) % GRID_W
            cell = int(flake[1]) * GRID_W + int(flake[0])
            if 0 <= cell < len(grid):
                grid[cell] = 255
        if not hasattr(self, "_snow_ramp"):
            self._snow_ramp = self._ramp((225, 240, 255))
        ramp = self._snow_ramp
        cells = self.geo.grid_of
        for i in range(self.n):
            level = grid[cells[i]]
            out[i] = ramp[level] if level else (2, 4, 12)

    def _p_fire(self, out, _t):
        heat = self.heat
        width = GRID_W
        # 1. cool every cell a little
        for i in range(len(heat)):
            cooling = random.randrange(0, 14)
            heat[i] = heat[i] - cooling if heat[i] > cooling else 0
        # 2. drift heat upwards (row 0 is the base of the tree)
        for row in range(GRID_H - 1, 1, -1):
            base = row * width
            below = base - width
            under = below - width
            for col in range(width):
                heat[base + col] = (
                    heat[below + col] + heat[under + col] + heat[below + ((col + 1) % width)]
                ) // 3
        # 3. new sparks at the base
        for _ in range(3):
            cell = random.randrange(width * 2)
            value = heat[cell] + random.randrange(120, 255)
            heat[cell] = 255 if value > 255 else value
        cells = self.geo.grid_of
        fire = FIRE
        for i in range(self.n):
            out[i] = fire[heat[cells[i]]]
