"""Optional physical buttons wired to spare GPIO pins.

Five momentary switches -- up, down, left, right and select -- each wired
between its pin and ground.  The internal pull-ups are used, so no resistors
are needed.  Any button you leave unconfigured is simply absent.

The buttons act in two ways, decided in ``code.py``:

* while the board is running its own show, they drive it directly (change
  pattern, change brightness, start the on-board Snake);
* while a phone is driving, the presses are queued and the web app picks
  them up, so you can play the phone's games with a real controller.

Nothing here assumes the pins exist.  A pin that is misspelled, missing on
this board, or already used by the LED strip is skipped with a warning that
the admin page shows, rather than stopping the tree from starting.
"""

import board
import digitalio

#: The buttons we know about, in the order the admin page lists them.
LABELS = ("up", "down", "left", "right", "select")

DEBOUNCE = 0.025  # seconds a reading must hold before we believe it
HOLD = 0.7  # how long "select" must be held to count as a hold
REPEAT_DELAY = 0.45  # before a held direction starts repeating
REPEAT_RATE = 0.12  # and how fast it repeats after that


class Buttons:
    """Debounced reader for the configured buttons."""

    def __init__(self, cfg):
        self.pins = {}
        self.state = {}
        self.warnings = []
        self.enabled = False
        self.configure(cfg)

    # -- setup -----------------------------------------------------------
    def release(self):
        """Give the pins back, so they can be reconfigured or reused."""
        for io in self.pins.values():
            try:
                io.deinit()
            except Exception:  # pragma: no cover
                pass
        self.pins = {}
        self.state = {}

    def configure(self, cfg):
        self.release()
        self.warnings = []
        self.enabled = bool(cfg.get("buttons_enabled"))
        if not self.enabled:
            return

        # The strip's data pin is spoken for.
        taken = set()
        strip_pin = cfg.get("pin")
        if strip_pin:
            taken.add(strip_pin)

        for label in LABELS:
            name = (cfg.get("button_" + label) or "").strip()
            if not name:
                continue
            if name in taken:
                self.warnings.append("%s: %s is already in use" % (label, name))
                continue

            pin = getattr(board, name, None)
            if pin is None:
                self.warnings.append("%s: this board has no pin called %s" % (label, name))
                continue

            try:
                io = digitalio.DigitalInOut(pin)
                io.direction = digitalio.Direction.INPUT
                io.pull = digitalio.Pull.UP
            except Exception as err:
                self.warnings.append("%s: could not claim %s (%s)" % (label, name, err))
                continue

            self.pins[label] = io
            taken.add(name)
            self.state[label] = {
                "raw": False,
                "down": False,
                "changed": 0.0,
                "since": 0.0,
                "repeat_at": 0.0,
                "held": False,
            }

    @property
    def active(self):
        return bool(self.pins)

    # -- reading ---------------------------------------------------------
    def poll(self, now):
        """Return a list of ``(label, kind)`` events since the last call.

        ``kind`` is ``press`` or ``release`` for any button, ``repeat`` while
        a direction is held down, and ``hold`` once when select is held.
        """
        events = []
        for label, io in self.pins.items():
            state = self.state[label]

            # Buttons pull their pin to ground, so pressed reads as low.
            raw = not io.value
            if raw != state["raw"]:
                state["raw"] = raw
                state["changed"] = now
            elif now - state["changed"] >= DEBOUNCE and raw != state["down"]:
                state["down"] = raw
                if raw:
                    state["since"] = now
                    state["held"] = False
                    state["repeat_at"] = now + REPEAT_DELAY
                    events.append((label, "press"))
                else:
                    events.append((label, "release"))

            if not state["down"]:
                continue

            if label == "select":
                if not state["held"] and now - state["since"] >= HOLD:
                    state["held"] = True
                    events.append((label, "hold"))
            elif now >= state["repeat_at"]:
                state["repeat_at"] = now + REPEAT_RATE
                events.append((label, "repeat"))

        return events
