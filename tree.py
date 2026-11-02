"""Tree geometry and persisted configuration.

The strip is wrapped around the tree as one continuous spiral.  Loop 0 is the
first loop along the strip; loop ``turns - 1`` is the last.  Because the tree
is a cone, loops higher up are shorter, so the pixel count per loop tapers
from the circumference at the bottom down to ``top_ratio`` of it at the top.

Two per-pixel lookup tables are built once and then used by *everything*
(device-side idle patterns, and the browser for games / drawing / images):

    ``u[i]``  angle around the tree, 0..255, where 0 is the front of the tree
    ``v[i]``  height,                0..255, where 0 is the bottom of the tree

Calibration therefore happens in exactly one place: the per-loop "front"
offsets feed into ``u``, and every renderer gets it for free.
"""

import json

try:
    import binascii
except ImportError:  # pragma: no cover
    binascii = None

CONFIG_PATH = "/config.json"

#: Resolution of the coarse render grid used by sprite-style patterns and by
#: the browser games.  Angular columns x height rows.
GRID_W = 16
GRID_H = 24

DEFAULTS = {
    # --- hardware -------------------------------------------------------
    "num_pixels": 300,
    "pin": "GP0",
    "order": "GRB",
    "brightness": 0.45,
    # --- tree shape -----------------------------------------------------
    "turns": 12,  # how many times the strip goes around the tree
    "top_ratio": 0.18,  # top circumference as a fraction of the bottom
    "start_at_bottom": True,  # does pixel 0 sit at the bottom of the tree?
    "clockwise": True,  # direction the strip winds, seen from the front
    # Per-loop front calibration.  Each entry is 0..1: the fraction of the
    # way through that loop at which the pixel facing the front sits.
    "front": [],
    "calibrated": [],
    "rotation": 0.0,  # global spin applied on top of the calibration
    # --- idle animation -------------------------------------------------
    "idle_pattern": "rainbow_spiral",
    "idle_speed": 1.0,
    "idle_brightness": 0.45,
    "idle_palette": "classic",
    "idle_cycle": False,
    "idle_cycle_secs": 90,
    "fps": 30,
    # --- games ----------------------------------------------------------
    "cols": 16,  # angular columns of the game grid
    "snake_wrap": "spiral",  # "spiral" (wrapping climbs a layer) or "ring"
    # --- networking -----------------------------------------------------
    "wifi_mode": "ap",  # "ap" = own hotspot, "sta" = join your wifi
    "ap_ssid": "ChristmasTree",
    "ap_password": "jingle123",
    "sta_ssid": "",
    "sta_password": "",
    "hostname": "tree",
}

# Keys whose change needs the board to restart before it takes effect.
RESTART_KEYS = (
    "pin",
    "order",
    "wifi_mode",
    "ap_ssid",
    "ap_password",
    "sta_ssid",
    "sta_password",
    "hostname",
)

_INT_KEYS = ("num_pixels", "turns", "fps", "cols", "idle_cycle_secs")
_FLOAT_KEYS = (
    "top_ratio",
    "brightness",
    "idle_speed",
    "idle_brightness",
    "rotation",
)
_BOOL_KEYS = ("start_at_bottom", "clockwise", "idle_cycle")

_LIMITS = {
    "num_pixels": (1, 1200),
    "turns": (1, 64),
    "fps": (1, 60),
    "cols": (4, 48),
    "idle_cycle_secs": (5, 3600),
    "top_ratio": (0.02, 1.0),
    "brightness": (0.01, 1.0),
    "idle_speed": (0.05, 6.0),
    "idle_brightness": (0.01, 1.0),
    "rotation": (0.0, 1.0),
}


def _clamp(key, value):
    limits = _LIMITS.get(key)
    if limits is None:
        return value
    low, high = limits
    if value < low:
        return low
    if value > high:
        return high
    return value


def load_config():
    """Return the saved configuration merged over the defaults."""
    cfg = dict(DEFAULTS)
    cfg["front"] = []
    cfg["calibrated"] = []
    try:
        with open(CONFIG_PATH, "r") as handle:
            saved = json.load(handle)
        if isinstance(saved, dict):
            cfg.update(sanitise(saved))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    """Persist the config.  Returns True if it reached flash."""
    try:
        with open(CONFIG_PATH, "w") as handle:
            json.dump(cfg, handle)
        return True
    except OSError:
        # Read-only filesystem: the USB host owns the drive.  The settings
        # still apply for this session, the UI just warns about it.
        return False


def sanitise(raw):
    """Coerce an untrusted dict of settings into safe, in-range values."""
    out = {}
    for key, value in raw.items():
        if key not in DEFAULTS:
            continue
        try:
            if key in _INT_KEYS:
                out[key] = _clamp(key, int(value))
            elif key in _FLOAT_KEYS:
                out[key] = _clamp(key, float(value))
            elif key in _BOOL_KEYS:
                out[key] = bool(value)
            elif key == "front":
                out[key] = [float(x) % 1.0 for x in value]
            elif key == "calibrated":
                out[key] = [bool(x) for x in value]
            elif key in ("snake_wrap",):
                out[key] = "ring" if str(value) == "ring" else "spiral"
            elif key == "wifi_mode":
                out[key] = "sta" if str(value) == "sta" else "ap"
            else:
                out[key] = str(value)[:64]
        except (TypeError, ValueError):
            continue
    return out


def split_loops(num_pixels, turns, top_ratio):
    """Share ``num_pixels`` between ``turns`` loops of a tapering cone.

    Loop 0 is the bottom (longest) loop.  Uses largest-remainder rounding so
    the loop lengths always add back up to exactly ``num_pixels``.
    """
    turns = max(1, min(turns, num_pixels))
    if turns == 1:
        return [num_pixels]

    weights = []
    for k in range(turns):
        mid = (k + 0.5) / turns  # height of the middle of this loop, 0..1
        weights.append(1.0 - mid * (1.0 - top_ratio))
    total = sum(weights)

    ideal = [num_pixels * w / total for w in weights]
    counts = [max(1, int(x)) for x in ideal]

    short = num_pixels - sum(counts)
    if short > 0:
        order = sorted(range(turns), key=lambda k: ideal[k] - int(ideal[k]), reverse=True)
        i = 0
        while short > 0:
            counts[order[i % turns]] += 1
            short -= 1
            i += 1
    else:
        order = sorted(range(turns), key=lambda k: ideal[k] - int(ideal[k]))
        i = 0
        guard = 0
        while short < 0 and guard < turns * 4:
            k = order[i % turns]
            if counts[k] > 1:
                counts[k] -= 1
                short += 1
            i += 1
            guard += 1
    return counts


def _resample(values, want, fill=0.0):
    """Stretch/shrink a per-loop list to ``want`` entries.

    Used when the number of rotations changes: the calibration is kept as an
    angle per height, so it is interpolated onto the new loops rather than
    thrown away.
    """
    if want <= 0:
        return []
    if not values:
        return [fill] * want
    if len(values) == want:
        return list(values)
    if len(values) == 1:
        return [values[0]] * want
    out = []
    span = len(values) - 1
    for k in range(want):
        pos = (k / (want - 1)) * span if want > 1 else 0.0
        low = int(pos)
        high = min(low + 1, span)
        frac = pos - low
        a, b = values[low], values[high]
        if isinstance(a, bool) or isinstance(b, bool):
            # A loop counts as known if either neighbour it came from was:
            # the interpolated angle is as good as the one "fill in the
            # rest" would have produced anyway.
            out.append(bool(a) or bool(b))
        else:
            # shortest way round the circle
            delta = b - a
            if delta > 0.5:
                delta -= 1.0
            elif delta < -0.5:
                delta += 1.0
            out.append((a + delta * frac) % 1.0)
    return out


def interpolate_front(front, calibrated):
    """Fill in the loops you have not calibrated from the ones you have.

    Hand-wrapped strips drift gradually, so calibrating a few loops and
    interpolating the rest gets you most of the way there.  Returns a new
    list; loops outside the calibrated range copy the nearest known loop.
    """
    turns = len(front)
    known = [k for k in range(turns) if k < len(calibrated) and calibrated[k]]
    if not known:
        return list(front)

    out = list(front)
    first, last = known[0], known[-1]
    for k in range(turns):
        if k in known:
            continue
        if k < first:
            out[k] = front[first]
        elif k > last:
            out[k] = front[last]
        else:
            lo = max(j for j in known if j < k)
            hi = min(j for j in known if j > k)
            a, b = front[lo], front[hi]
            delta = b - a
            if delta > 0.5:
                delta -= 1.0
            elif delta < -0.5:
                delta += 1.0
            out[k] = (a + delta * ((k - lo) / (hi - lo))) % 1.0
    return out


class Geometry:
    """Maps strip position to a place on the tree."""

    def __init__(self, cfg):
        self.rebuild(cfg)

    def rebuild(self, cfg):
        num = max(1, int(cfg["num_pixels"]))
        turns = max(1, min(int(cfg["turns"]), num))
        top_ratio = float(cfg["top_ratio"])
        rotation = float(cfg.get("rotation", 0.0))
        clockwise = bool(cfg.get("clockwise", True))
        bottom_first = bool(cfg.get("start_at_bottom", True))

        self.n = num
        self.turns = turns
        self.top_ratio = top_ratio

        # Loop lengths, bottom (longest) first ...
        counts = split_loops(num, turns, top_ratio)
        # ... then put them in strip order.
        if not bottom_first:
            counts.reverse()
        self.counts = counts

        starts = []
        at = 0
        for count in counts:
            starts.append(at)
            at += count
        self.starts = starts

        # Keep calibration meaningful across a change in the loop count.
        front = _resample(cfg.get("front") or [], turns, 0.0)
        calibrated = _resample(cfg.get("calibrated") or [], turns, False)
        calibrated = [bool(x) for x in calibrated]
        cfg["front"] = front
        cfg["calibrated"] = calibrated
        self.front = front
        self.calibrated = calibrated

        # The pixel in each loop that faces the front of the tree.
        self.front_index = [
            int(round(front[k] * counts[k])) % counts[k] for k in range(turns)
        ]

        self.u = bytearray(num)
        self.v = bytearray(num)
        self.loop_of = bytearray(num)
        self.height_of = [0.0] * turns

        index = 0
        for k in range(turns):
            count = counts[k]
            offset = self.front_index[k]
            for j in range(count):
                angle = ((j - offset) % count) / count
                if not clockwise:
                    angle = (1.0 - angle) % 1.0
                angle = (angle + rotation) % 1.0

                along = (k + (j + 0.5) / count) / turns  # 0..1 along the strip
                height = along if bottom_first else 1.0 - along

                self.u[index] = int(angle * 256.0) & 255
                self.v[index] = int(height * 255.0 + 0.5) & 255
                self.loop_of[index] = k
                index += 1
            mid = (k + 0.5) / turns
            self.height_of[k] = mid if bottom_first else 1.0 - mid

        # Coarse grid cell for each pixel, for sprite-style renderers.
        self.grid_of = [0] * num
        for i in range(num):
            col = (self.u[i] * GRID_W) >> 8
            row = (self.v[i] * GRID_H) >> 8
            if row >= GRID_H:
                row = GRID_H - 1
            self.grid_of[i] = row * GRID_W + col

    def front_pixel(self, loop):
        """Index of the pixel in ``loop`` that should be facing you."""
        loop %= self.turns
        return self.starts[loop] + self.front_index[loop]

    def loop_pixels(self, loop):
        loop %= self.turns
        start = self.starts[loop]
        return range(start, start + self.counts[loop])

    def radius(self, height):
        """Cone radius (0..1 of the bottom radius) at a normalised height."""
        return 1.0 - height * (1.0 - self.top_ratio)

    def describe(self):
        """Compact JSON-able description for the browser."""
        if binascii is not None:
            u_hex = binascii.hexlify(self.u).decode()
            v_hex = binascii.hexlify(self.v).decode()
        else:  # pragma: no cover
            u_hex = "".join("%02x" % b for b in self.u)
            v_hex = "".join("%02x" % b for b in self.v)
        return {
            "n": self.n,
            "turns": self.turns,
            "top_ratio": self.top_ratio,
            "counts": self.counts,
            "starts": self.starts,
            "front": [round(x, 4) for x in self.front],
            "front_index": self.front_index,
            "calibrated": self.calibrated,
            "heights": [round(x, 4) for x in self.height_of],
            "grid_w": GRID_W,
            "grid_h": GRID_H,
            "u": u_hex,
            "v": v_hex,
        }
