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
    # Pixels at the start of the strip that are not on the tree at all --
    # the lead-in from the controller.  They are always left dark.
    "start_offset": 0,
    # Manual pixel count per level, bottom of the tree first, so
    # ``loop_counts[0]`` is the bottom loop whichever end of the strip starts
    # there.  Empty means work them out from the cone shape above; a
    # hand-wrapped tree rarely matches the maths exactly.  They may add up to
    # less than the strip, and the pixels left over at the end stay dark.
    "loop_counts": [],
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
    # How forgiving Snake is.  "gentle": the top and bottom wrap round and
    # the snake may cross itself.  "normal": they still wrap, but crossing
    # yourself ends the game.  "hard": the ends are walls, and it is quicker.
    "snake_difficulty": "normal",
    # --- optional physical buttons --------------------------------------
    # Each switch goes between its pin and ground; the internal pull-ups do
    # the rest.  Leave a pin blank to leave that button out.
    "buttons_enabled": False,
    "button_up": "GP2",
    "button_down": "GP3",
    "button_left": "GP4",
    "button_right": "GP5",
    "button_select": "GP6",
    # --- admin login ----------------------------------------------------
    # The password is never stored, only a salted, stretched hash of it.
    # The shipped hash uses few rounds so it verifies quickly even on a
    # build with no native hashlib; the board re-hashes at its own round
    # count the first time you log in.
    "admin_email": "jacobelilevy@gmail.com",
    "admin_salt": "d2ce35207b4091025cf399c9d3e53724",
    "admin_hash": "289343372c075230e0cfd5c7b6083c5439399cd92193c200fd910452c344f9e3",
    "admin_rounds": 150,

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

_INT_KEYS = ("num_pixels", "turns", "fps", "cols", "idle_cycle_secs", "start_offset",
             "admin_rounds")
_FLOAT_KEYS = (
    "top_ratio",
    "brightness",
    "idle_speed",
    "idle_brightness",
    "rotation",
)
_BOOL_KEYS = ("start_at_bottom", "clockwise", "idle_cycle", "buttons_enabled")

#: How Snake behaves at each difficulty.  ``wrap`` means the top and bottom
#: of the tree join up instead of being walls; ``self_collide`` means running
#: into your own tail ends the game.  Speeds are the seconds between moves at
#: the start and the fastest it will ever get.
SNAKE_DIFFICULTIES = {
    "gentle": {"wrap": True, "self_collide": False, "start": 0.42, "fastest": 0.18, "step": 0.010},
    "normal": {"wrap": True, "self_collide": True, "start": 0.32, "fastest": 0.11, "step": 0.012},
    "hard": {"wrap": False, "self_collide": True, "start": 0.24, "fastest": 0.08, "step": 0.014},
}

#: Settings a guest may change without logging in: the things you would
#: want a visitor to be able to play with.  Everything else -- the tree's
#: shape, calibration, hardware, wifi and the login itself -- is admin only.
GUEST_KEYS = (
    "idle_pattern",
    "idle_speed",
    "idle_brightness",
    "idle_palette",
    "idle_cycle",
    "idle_cycle_secs",
    "snake_difficulty",
    "snake_wrap",
)

#: Never sent to a client that has not logged in.
SECRET_KEYS = ("ap_password", "sta_password", "admin_salt", "admin_hash")

#: Changing any of these re-claims the GPIO pins.
BUTTON_KEYS = ("buttons_enabled",) + tuple("button_" + name for name in
                                           ("up", "down", "left", "right", "select"))

_LIMITS = {
    "num_pixels": (1, 1200),
    "start_offset": (0, 1199),
    "admin_rounds": (1, 500000),
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


def public_config(cfg, authed):
    """A copy of the config safe to hand to a client.

    Without a login the wifi passwords and the password hash are withheld:
    anyone who can reach the tree can call /api/state, and the hotspot
    password should not be one of the things they get back.
    """
    out = dict(cfg)
    if not authed:
        for key in SECRET_KEYS:
            if key in out:
                out[key] = ""
    return out


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
            elif key == "loop_counts":
                out[key] = [max(1, int(x)) for x in value]
            elif key in ("snake_wrap",):
                out[key] = "ring" if str(value) == "ring" else "spiral"
            elif key == "snake_difficulty":
                wanted = str(value)
                out[key] = wanted if wanted in SNAKE_DIFFICULTIES else "normal"
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
        offset = min(max(0, int(cfg.get("start_offset", 0))), num - 1)
        usable = num - offset
        turns = max(1, min(int(cfg["turns"]), usable))
        top_ratio = float(cfg["top_ratio"])
        rotation = float(cfg.get("rotation", 0.0))
        clockwise = bool(cfg.get("clockwise", True))
        bottom_first = bool(cfg.get("start_at_bottom", True))

        self.n = num
        self.offset = offset
        self.usable = usable
        self.turns = turns
        self.top_ratio = top_ratio

        # A manual set of level lengths wins, as long as it still fits this
        # strip; otherwise work them out from the cone shape.  Manual lengths
        # are given bottom level first and may leave pixels spare at the end.
        manual = cfg.get("loop_counts") or []
        usable_manual = (
            len(manual) == turns
            and 0 < sum(manual) <= usable
            and all(c >= 1 for c in manual)
        )
        if usable_manual:
            counts = [int(c) for c in manual]  # bottom level first
            self.auto_counts = False
        else:
            counts = split_loops(usable, turns, top_ratio)  # bottom first too
            self.auto_counts = True
            if manual:
                # Stale override (the rotation count or strip length moved).
                cfg["loop_counts"] = []

        # Both paths produce bottom-first lengths; put them in strip order.
        self.level_counts = list(counts)
        if not bottom_first:
            counts = list(reversed(counts))
        self.counts = counts

        starts = []
        at = offset
        for count in counts:
            starts.append(at)
            at += count
        self.starts = starts
        #: One past the last pixel that is on the tree.  Anything from here to
        #: the end of the strip is spare and stays dark.
        self.used = at

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

        index = offset
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
        for i in range(offset, self.used):
            col = (self.u[i] * GRID_W) >> 8
            row = (self.v[i] * GRID_H) >> 8
            if row >= GRID_H:
                row = GRID_H - 1
            self.grid_of[i] = row * GRID_W + col

    def build_grid(self, cols):
        """Group the pixels into a ``turns`` x ``cols`` grid on the cone.

        Row 0 is the bottom loop whichever end of the strip starts there, so
        "up" always means up.  This is the same mapping ``www/app.js`` builds
        for the browser games -- keep the two in step.

        Loops near the top are shorter than ``cols``, so some cells have no
        pixel of their own; those borrow the nearest pixel in the same loop,
        which is the best the real tree can do and leaves nothing invisible.
        """
        cols = max(1, int(cols))
        turns = self.turns

        order = sorted(range(turns), key=lambda k: self.height_of[k])
        row_of_loop = [0] * turns
        for row, loop in enumerate(order):
            row_of_loop[loop] = row

        grid = [[[] for _ in range(cols)] for _ in range(turns)]
        in_row = [[] for _ in range(turns)]
        for i in range(self.offset, self.used):
            row = row_of_loop[self.loop_of[i]]
            col = int(self.u[i] * cols / 256.0 + 0.5) % cols
            grid[row][col].append(i)
            in_row[row].append(i)

        for row in range(turns):
            candidates = in_row[row]
            if not candidates:
                continue
            for col in range(cols):
                if grid[row][col]:
                    continue
                wanted = col / cols
                best = candidates[0]
                best_gap = 2.0
                for i in candidates:
                    gap = abs(self.u[i] / 256.0 - wanted)
                    if gap > 0.5:
                        gap = 1.0 - gap
                    if gap < best_gap:
                        best_gap = gap
                        best = i
                grid[row][col].append(best)

        self.row_of_loop = row_of_loop
        return grid

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
            "offset": self.offset,
            "usable": self.usable,
            "used": self.used,
            "auto_counts": self.auto_counts,
            "level_counts": self.level_counts,
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
