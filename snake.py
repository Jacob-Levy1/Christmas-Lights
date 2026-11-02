"""Snake, played on the tree by the physical buttons -- no phone needed.

Deliberately the same game as the phone's: the tree is a grid of cells, one
row per loop of the strip, and going past the front of the tree climbs to
the next loop, exactly the way the strip itself does.  ``tree.Geometry``
builds the same grid for both, so they line up.
"""

import random

BACKGROUND = (0, 0, 0)


class Snake:
    def __init__(self, geometry, cols, spiral=True):
        self.geo = geometry
        self.cols = max(4, int(cols))
        self.spiral = spiral
        self.grid = geometry.build_grid(self.cols)
        self.rows = len(self.grid)
        self.score = 0
        self.best = 0
        self.reset(0.0)

    def reset(self, now):
        self.body = [(self.rows // 3, 0)]
        self.direction = "right"
        self.pending = None
        self.score = 0
        self.interval = 0.34
        self.moved_at = now
        self.dead_at = 0.0
        self.food = self._place_food()

    # -- mechanics -------------------------------------------------------
    def _place_food(self):
        for _ in range(200):
            spot = (random.randrange(self.rows), random.randrange(self.cols))
            if spot not in self.body:
                return spot
        return (0, 0)

    def _step(self, cell, direction):
        row, col = cell
        if direction == "up":
            row += 1
        elif direction == "down":
            row -= 1
        elif direction == "right":
            col += 1
            if col >= self.cols:
                col = 0
                if self.spiral:
                    row += 1
        elif direction == "left":
            col -= 1
            if col < 0:
                col = self.cols - 1
                if self.spiral:
                    row -= 1
        return (row, col)

    def input(self, direction):
        if self.dead_at:
            return
        opposite = {"up": "down", "down": "up", "left": "right", "right": "left"}
        if direction in opposite:
            if len(self.body) > 1 and opposite[direction] == self.direction:
                return
            self.pending = direction

    def _advance(self, now):
        if self.pending:
            self.direction = self.pending
            self.pending = None

        head = self._step(self.body[0], self.direction)
        row = head[0]
        if row < 0 or row >= self.rows or head in self.body[:-1]:
            self.dead_at = now
            if self.score > self.best:
                self.best = self.score
            return

        self.body.insert(0, head)
        if head == self.food:
            self.score += 1
            self.interval = max(0.11, self.interval - 0.012)
            self.food = self._place_food()
        else:
            self.body.pop()

    # -- drawing ---------------------------------------------------------
    def _paint(self, out, row, col, color):
        if 0 <= row < self.rows:
            for i in self.grid[row][col % self.cols]:
                out[i] = color

    def tick(self, now, out):
        """Render one frame.  Returns False once the game has given up."""
        for i in range(self.geo.n):
            out[i] = BACKGROUND

        if self.dead_at:
            level = 130 if int((now - self.dead_at) * 4) % 2 == 0 else 10
            for row in range(self.rows):
                for col in range(self.cols):
                    self._paint(out, row, col, (level, 0, 0))
            if now - self.dead_at > 2.0:
                self.reset(now)
            return True

        if now - self.moved_at >= self.interval:
            self.moved_at = now
            self._advance(now)

        pulse = 150 + int(100 * (1 if int(now * 6) % 2 == 0 else 0.4))
        if pulse > 255:
            pulse = 255
        self._paint(out, self.food[0], self.food[1], (pulse, pulse // 7, 0))

        count = len(self.body)
        for k in range(count - 1, -1, -1):
            row, col = self.body[k]
            if k == 0:
                self._paint(out, row, col, (255, 255, 240))
            else:
                fade = 1.0 - (k / (count + 3)) * 0.65
                self._paint(out, row, col, (int(20 * fade), int(225 * fade), int(70 * fade)))
        return True
