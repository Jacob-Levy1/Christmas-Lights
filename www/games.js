/* Games played on the tree.
 *
 * The tree is treated as a cylinder of cells: `App.rows` loops tall (row 0 is
 * the bottom loop) by `App.cols` columns around, with column 0 at the front.
 * Lighting a cell lights every real pixel that falls inside it, so the higher
 * loops — which have fewer pixels — still look right.
 */
const Games = {
  current: null,
  id: null,

  init() {
    document.getElementById('gamePicker').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-game]');
      if (button) this.select(button.dataset.game);
    });

    document.getElementById('pad').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-dir]');
      if (button) this.input(button.dataset.dir);
    });

    document.getElementById('simonPad').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-quad]');
      if (button) this.input('quad' + button.dataset.quad);
    });

    document.getElementById('snakeDifficulty').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-diff]');
      if (!button) return;
      const wanted = button.dataset.diff;
      document.querySelectorAll('#snakeDifficulty button').forEach((b) => {
        b.classList.toggle('on', b === button);
      });
      this.showDifficulty(wanted);
      try {
        await App.pushConfig({ snake_difficulty: wanted });
        // Restart so the new rules take effect straight away.
        if (this.current && App.activity === this.current) this.start();
        App.toast(wanted + ' snake');
      } catch (err) {
        App.toast('could not change the difficulty', true);
      }
    });

    document.getElementById('gameStart').addEventListener('click', () => this.start());
    document.getElementById('gameStop').addEventListener('click', () => {
      App.stopActivity();
      this.setScore('');
    });

    document.addEventListener('keydown', (event) => {
      const keys = {
        ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
        w: 'up', s: 'down', a: 'left', d: 'right', ' ': 'action',
      };
      if (keys[event.key] && App.tab === 'play') {
        event.preventDefault();
        this.input(keys[event.key]);
      }
    });

    this.wireSwipe(document.getElementById('tree'));
    this.select('snake');
  },

  /* Swiping on the tree preview steers too. */
  wireSwipe(element) {
    let x0 = 0, y0 = 0, down = false;
    element.addEventListener('pointerdown', (event) => {
      down = true; x0 = event.clientX; y0 = event.clientY;
    });
    element.addEventListener('pointerup', (event) => {
      if (!down) return;
      down = false;
      if (App.tab !== 'play') return;
      const dx = event.clientX - x0;
      const dy = event.clientY - y0;
      if (Math.abs(dx) < 24 && Math.abs(dy) < 24) { this.input('action'); return; }
      if (Math.abs(dx) > Math.abs(dy)) this.input(dx > 0 ? 'right' : 'left');
      else this.input(dy > 0 ? 'down' : 'up');
    });
  },

  /* Say in plain words what the chosen difficulty does. */
  showDifficulty(level) {
    const hint = document.getElementById('difficultyHint');
    const words = {
      gentle: 'Top and bottom wrap round, and you may cross your own tail. Slow.',
      normal: 'Top and bottom wrap round, but crossing your own tail ends the game.',
      hard: 'The top and bottom of the tree are walls, your tail is fatal, and it is quick.',
    };
    hint.textContent = words[level] || words.normal;
    document.querySelectorAll('#snakeDifficulty button').forEach((b) => {
      b.classList.toggle('on', b.dataset.diff === level);
    });
  },

  select(id) {
    this.id = id;
    document.querySelectorAll('#gamePicker button').forEach((b) => {
      b.classList.toggle('on', b.dataset.game === id);
    });
    const meta = this.catalogue[id];
    document.getElementById('gameTitle').textContent = meta.title;
    document.getElementById('gameHint').textContent = meta.hint;
    document.getElementById('pad').hidden = id === 'simon';
    document.getElementById('simonPad').hidden = id !== 'simon';
    const isSnake = id === 'snake';
    document.getElementById('snakeDifficulty').hidden = !isSnake;
    document.getElementById('difficultyHint').hidden = !isSnake;
    if (isSnake) this.showDifficulty(App.state.config.snake_difficulty || 'normal');
    if (App.activity) App.stopActivity();
    this.setScore('');
  },

  start() {
    if (!this.id) return;
    this.current = this.catalogue[this.id].make();
    this.current.tab = 'play';
    this.current.label = this.catalogue[this.id].title;
    App.startActivity(this.current);
  },

  input(dir) {
    if (!this.current || App.activity !== this.current) {
      // First press starts the game, so you can just grab the pad and go.
      if (dir !== 'action') { this.start(); return; }
      this.start();
      return;
    }
    if (this.current.input) this.current.input(dir);
  },

  setScore(text) {
    document.getElementById('gameScore').textContent = text;
  },

  catalogue: {
    snake: {
      title: '🐍 Snake',
      hint: '▲ and ▼ jump a whole layer. ◀ and ▶ snake round the tree — pass the front and you climb to the next layer, exactly the way the strip does.',
      make: () => makeSnake(),
    },
    tetris: {
      title: '🧱 Rings',
      hint: 'Blocks fall down the tree and wrap around it. Fill a complete ring and it clears. ▲ rotates, ▼ drops.',
      make: () => makeRings(),
    },
    dodger: {
      title: '☄️ Dodge',
      hint: 'You are the blue light on the bottom ring. Slide round the tree to dodge the falling baubles.',
      make: () => makeDodger(),
    },
    simon: {
      title: '🔔 Simon',
      hint: 'The tree flashes a quarter at a time. Tap them back in the same order.',
      make: () => makeSimon(),
    },
  },
};

/* ───────────────────────────── Snake ───────────────────────────── */
/* How forgiving each difficulty is.  Kept in step with SNAKE_DIFFICULTIES
 * in tree.py, which the board's own Snake uses. */
const SNAKE_RULES = {
  gentle: { wrap: true, selfCollide: false, start: 420, fastest: 180, step: 10 },
  normal: { wrap: true, selfCollide: true, start: 320, fastest: 110, step: 12 },
  hard: { wrap: false, selfCollide: true, start: 240, fastest: 80, step: 14 },
};

function makeSnake() {
  const rows = App.rows;
  const cols = App.cols;
  const spiral = App.state.config.snake_wrap !== 'ring';
  const rules = SNAKE_RULES[App.state.config.snake_difficulty] || SNAKE_RULES.normal;

  let body = [];
  let dir = 'right';
  let pending = null;
  let food = null;
  let score = 0;
  let interval = rules.start;
  let last = 0;
  let dead = 0;

  function step(cell, direction) {
    let { r, c } = cell;
    if (direction === 'up') r += 1;
    else if (direction === 'down') r -= 1;
    else if (direction === 'right') {
      c += 1;
      if (c >= cols) { c = 0; if (spiral) r += 1; }
    } else if (direction === 'left') {
      c -= 1;
      if (c < 0) { c = cols - 1; if (spiral) r -= 1; }
    }
    // Unless the ends are walls, the top of the tree joins the bottom.
    if (rules.wrap) r = ((r % rows) + rows) % rows;
    return { r, c };
  }

  function placeFood() {
    for (let tries = 0; tries < 400; tries++) {
      const spot = { r: Math.floor(Math.random() * rows), c: Math.floor(Math.random() * cols) };
      if (!body.some((p) => p.r === spot.r && p.c === spot.c)) return spot;
    }
    return { r: 0, c: 0 };
  }

  function reset() {
    body = [{ r: Math.floor(rows / 3), c: 0 }];
    dir = 'right';
    pending = null;
    score = 0;
    interval = rules.start;
    dead = 0;
    food = placeFood();
    Games.setScore('0');
  }

  function advance() {
    if (pending) { dir = pending; pending = null; }
    const head = step(body[0], dir);

    // With walls on, the ends of the tree are fatal; with them off, step()
    // has already wrapped the row round.
    if (!rules.wrap && (head.r < 0 || head.r >= rows)) {
      dead = performance.now();
      return;
    }
    if (rules.selfCollide
      && body.some((p, index) => index < body.length - 1 && p.r === head.r && p.c === head.c)) {
      dead = performance.now();
      return;
    }

    body.unshift(head);
    if (head.r === food.r && head.c === food.c) {
      score += 1;
      interval = Math.max(rules.fastest, interval - rules.step);
      food = placeFood();
      Games.setScore(String(score));
    } else {
      body.pop();
    }
  }

  return {
    start: reset,

    input(key) {
      const opposite = { up: 'down', down: 'up', left: 'right', right: 'left' };
      if (key === 'action') { reset(); return; }
      if (!['up', 'down', 'left', 'right'].includes(key)) return;
      if (body.length > 1 && opposite[key] === dir) return;
      pending = key;
    },

    tick(now) {
      if (dead) {
        // Flash the whole tree red, then start over.
        const since = now - dead;
        const level = since % 300 < 150 ? 120 : 10;
        App.clear();
        for (let r = 0; r < rows; r++) {
          for (let c = 0; c < cols; c++) App.setCell(r, c, level, 0, 0);
        }
        if (since > 1500) reset();
        return;
      }

      if (now - last >= interval) { last = now; advance(); }

      App.clear();

      // Food pulses so you can spot it round the back of the tree.
      const pulse = 150 + Math.round(105 * Math.sin(now / 160));
      App.setCell(food.r, food.c, pulse, Math.round(pulse * 0.15), 0);

      for (let k = body.length - 1; k >= 0; k--) {
        const part = body[k];
        if (k === 0) {
          App.setCell(part.r, part.c, 255, 255, 240);
        } else {
          const fade = 1 - (k / (body.length + 3)) * 0.65;
          App.setCell(part.r, part.c, Math.round(20 * fade), Math.round(225 * fade), Math.round(70 * fade));
        }
      }
    },
  };
}

/* ───────────────────────── Rings (Tetris) ───────────────────────── */
const RING_PIECES = [
  { cells: [[0, 0], [1, 0], [2, 0], [3, 0]], color: [0, 220, 220] },
  { cells: [[0, 0], [1, 0], [0, 1], [1, 1]], color: [240, 220, 0] },
  { cells: [[0, 0], [1, 0], [2, 0], [1, 1]], color: [190, 0, 220] },
  { cells: [[0, 0], [1, 0], [1, 1], [2, 1]], color: [0, 230, 60] },
  { cells: [[1, 0], [2, 0], [0, 1], [1, 1]], color: [230, 30, 30] },
  { cells: [[0, 0], [1, 0], [2, 0], [2, 1]], color: [255, 140, 0] },
  { cells: [[0, 0], [1, 0], [2, 0], [0, 1]], color: [40, 90, 255] },
];

function makeRings() {
  const rows = App.rows;
  const cols = App.cols;
  let board, piece, score, lines, interval, last, dead;

  function reset() {
    board = Array.from({ length: rows }, () => new Array(cols).fill(null));
    score = 0;
    lines = 0;
    interval = 520;
    last = 0;
    dead = 0;
    spawn();
    Games.setScore('0');
  }

  function spawn() {
    const template = RING_PIECES[Math.floor(Math.random() * RING_PIECES.length)];
    piece = {
      cells: template.cells.map((p) => [p[0], p[1]]),
      color: template.color,
      c: Math.floor(Math.random() * cols),
      r: rows - 1,
    };
    if (hits(piece, piece.c, piece.r)) dead = performance.now();
  }

  function worldCells(p, c, r) {
    return p.cells.map(([dc, dr]) => [(((c + dc) % cols) + cols) % cols, r - dr]);
  }

  function hits(p, c, r) {
    return worldCells(p, c, r).some(([col, row]) => {
      if (row < 0) return true;
      if (row >= rows) return false; // still coming in from the top
      return board[row][col] !== null;
    });
  }

  function lock() {
    worldCells(piece, piece.c, piece.r).forEach(([col, row]) => {
      if (row >= 0 && row < rows) board[row][col] = piece.color;
    });
    let cleared = 0;
    for (let row = 0; row < rows; row++) {
      if (board[row].every((cell) => cell !== null)) {
        board.splice(row, 1);
        board.push(new Array(cols).fill(null));
        cleared += 1;
        row -= 1;
      }
    }
    if (cleared) {
      lines += cleared;
      score += [0, 100, 300, 600, 1000][cleared] || 1000;
      interval = Math.max(160, 520 - lines * 18);
      Games.setScore(score + ' · ' + lines + ' rings');
    }
    spawn();
  }

  function rotate() {
    const turned = piece.cells.map(([dc, dr]) => [dr, -dc]);
    const minC = Math.min(...turned.map((p) => p[0]));
    const minR = Math.min(...turned.map((p) => p[1]));
    const normalised = turned.map(([dc, dr]) => [dc - minC, dr - minR]);
    const candidate = { cells: normalised, color: piece.color };
    if (!hits(candidate, piece.c, piece.r)) piece.cells = normalised;
  }

  return {
    start: reset,

    input(key) {
      if (dead) { reset(); return; }
      if (key === 'left' && !hits(piece, piece.c - 1, piece.r)) piece.c -= 1;
      else if (key === 'right' && !hits(piece, piece.c + 1, piece.r)) piece.c += 1;
      else if (key === 'up' || key === 'action') rotate();
      else if (key === 'down') {
        while (!hits(piece, piece.c, piece.r - 1)) piece.r -= 1;
        lock();
      }
    },

    tick(now) {
      if (dead) {
        const level = (now - dead) % 400 < 200 ? 140 : 15;
        App.clear();
        for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) App.setCell(r, c, level, 0, 0);
        if (now - dead > 2000) reset();
        return;
      }

      if (now - last >= interval) {
        last = now;
        if (hits(piece, piece.c, piece.r - 1)) lock();
        else piece.r -= 1;
      }

      App.clear();
      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols; col++) {
          const color = board[row][col];
          if (color) App.setCell(row, col, color[0] >> 1, color[1] >> 1, color[2] >> 1);
        }
      }
      worldCells(piece, piece.c, piece.r).forEach(([col, row]) => {
        if (row >= 0 && row < rows) App.setCell(row, col, piece.color[0], piece.color[1], piece.color[2]);
      });
    },
  };
}

/* ───────────────────────────── Dodge ───────────────────────────── */
function makeDodger() {
  const rows = App.rows;
  const cols = App.cols;
  let player, falling, score, interval, last, spawnEvery, dead;

  function reset() {
    player = 0;
    falling = [];
    score = 0;
    interval = 260;
    spawnEvery = 3;
    last = 0;
    dead = 0;
    Games.setScore('0');
  }

  return {
    start: reset,

    input(key) {
      if (dead) { reset(); return; }
      if (key === 'left') player = (player - 1 + cols) % cols;
      else if (key === 'right') player = (player + 1) % cols;
      else if (key === 'action') reset();
    },

    tick(now) {
      if (dead) {
        const level = (now - dead) % 300 < 150 ? 140 : 10;
        App.clear();
        for (let c = 0; c < cols; c++) App.setCell(0, c, level, 0, 0);
        if (now - dead > 1600) reset();
        return;
      }

      if (now - last >= interval) {
        last = now;
        score += 1;
        Games.setScore(String(score));
        interval = Math.max(90, 260 - score * 1.2);

        for (const bauble of falling) bauble.r -= 1;
        for (const bauble of falling) {
          if (bauble.r <= 0 && bauble.c === player) dead = performance.now();
        }
        falling = falling.filter((bauble) => bauble.r > 0);

        if (score % spawnEvery === 0) {
          const count = 1 + Math.floor(Math.random() * Math.max(1, Math.floor(cols / 7)));
          for (let k = 0; k < count; k++) {
            falling.push({ r: rows - 1, c: Math.floor(Math.random() * cols), hue: Math.random() });
          }
        }
      }

      App.clear();
      for (let c = 0; c < cols; c++) App.setCell(0, c, 0, 12, 24);
      for (const bauble of falling) {
        const [r, g, b] = hsv(bauble.hue, 0.9, 1);
        App.setCell(bauble.r, bauble.c, r, g, b);
      }
      const glow = 180 + Math.round(70 * Math.sin(now / 140));
      App.setCell(0, player, 60, 180, glow);
      App.setCell(1, player, 20, 60, glow >> 1);
    },
  };
}

/* ───────────────────────────── Simon ───────────────────────────── */
const SIMON_COLORS = [[255, 40, 40], [40, 230, 90], [50, 120, 255], [255, 200, 40]];

function makeSimon() {
  let sequence, position, phase, showIndex, showUntil, flash, best;

  function reset() {
    sequence = [];
    best = Number(localStorage.getItem('simonBest') || 0);
    extend();
  }

  function extend() {
    sequence.push(Math.floor(Math.random() * 4));
    position = 0;
    phase = 'show';
    showIndex = 0;
    showUntil = 0;
    Games.setScore(sequence.length - 1 + ' · best ' + best);
  }

  function paintQuadrant(quad, level) {
    const cols = App.cols;
    const from = Math.round((quad * cols) / 4);
    const to = Math.round(((quad + 1) * cols) / 4);
    const [r, g, b] = SIMON_COLORS[quad];
    for (let c = from; c < to; c++) {
      for (let row = 0; row < App.rows; row++) {
        App.setCell(row, c, Math.round(r * level), Math.round(g * level), Math.round(b * level));
      }
    }
  }

  return {
    start: reset,

    input(key) {
      if (!key.startsWith('quad')) {
        if (key === 'action') reset();
        return;
      }
      if (phase !== 'input') return;
      const quad = Number(key.slice(4));
      flash = { quad, until: performance.now() + 220 };

      if (quad === sequence[position]) {
        position += 1;
        if (position === sequence.length) {
          if (sequence.length > best) {
            best = sequence.length;
            localStorage.setItem('simonBest', String(best));
          }
          setTimeout(extend, 420);
          phase = 'wait';
        }
      } else {
        phase = 'lost';
        flash = { quad, until: performance.now() + 1400, bad: true };
      }
    },

    tick(now) {
      App.clear();

      if (phase === 'lost') {
        const level = now % 300 < 150 ? 0.5 : 0.05;
        for (let quad = 0; quad < 4; quad++) paintQuadrant(quad, level * 0.4);
        paintQuadrant(sequence[position], level);
        if (now > flash.until) reset();
        return;
      }

      if (phase === 'show') {
        if (!showUntil) showUntil = now + 520;
        const remaining = showUntil - now;
        if (remaining > 160) {
          paintQuadrant(sequence[showIndex], 1);
        } else if (remaining <= 0) {
          showIndex += 1;
          showUntil = showIndex < sequence.length ? now + 520 : 0;
          if (showIndex >= sequence.length) phase = 'input';
        }
        return;
      }

      // Waiting for the player: a dim hint of every quarter.
      for (let quad = 0; quad < 4; quad++) paintQuadrant(quad, 0.06);
      if (flash && now < flash.until) paintQuadrant(flash.quad, 1);
    },
  };
}
