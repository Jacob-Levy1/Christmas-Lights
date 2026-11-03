/* Core of the tree web app.
 *
 * The board tells us where every pixel physically sits (angle around the
 * tree + height up the tree, already corrected by the admin calibration).
 * Everything here works in those terms, renders into one RGB byte frame and
 * posts it to /api/frame.  Games, drawing and photos are all just different
 * things that fill that frame.
 */
const App = {
  state: null,
  geo: null,

  n: 0,
  frame: new Uint8Array(0),

  // Per-pixel geometry, derived once whenever the config changes.
  ang: null,   // radians, 0 = front of the tree
  hgt: null,   // 0 at the bottom, 1 at the top
  rad: null,   // cone radius at that height, as a fraction of the base
  sinA: null,
  cosA: null,
  order: null, // back-to-front draw order for the preview

  // Game grid.
  rows: 0,
  cols: 0,
  cells: null,     // cells[row][col] -> Int32Array of pixel indices
  loopOfPixel: null,

  activity: null,  // whatever is currently driving the frame
  dirty: false,
  inflight: false,
  queued: false,
  lastSent: 0,
  lastPolled: 0,
  lastInput: 0,
  pollingInput: false,
  buttonsWired: false,
  polling: false,
  live: false,
  tab: 'play',

  /* ───────────────────────── boot ───────────────────────── */
  async boot() {
    this.canvas = document.getElementById('tree');
    this.ctx = this.canvas.getContext('2d');
    this.wireTabs();

    try {
      await this.refresh();
    } catch (err) {
      this.setStatus('no connection to the tree', 'bad');
      console.error(err);
      return;
    }

    Games.init();
    Paint.init();
    Admin.init();

    requestAnimationFrame(() => this.loop());
    setInterval(() => { if (!this.live) this.refresh().catch(() => {}); }, 6000);
  },

  async refresh() {
    this.state = await this.get('/api/state');
    if (!this.live) this.boardMode = this.state.mode;
    this.setGeometry(await this.get('/api/geometry'));
    this.setStatus();
    if (Admin.ready) Admin.syncFromState();
    if (Idle.ready) Idle.syncFromState();
  },

  /* ───────────────────────── http ───────────────────────── */
  async get(url) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error(url + ' -> ' + res.status);
    return res.json();
  },

  async post(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw new Error(url + ' -> ' + res.status);
    const text = await res.text();
    return text ? JSON.parse(text) : {};
  },

  /* ───────────────────── geometry ───────────────────── */
  setGeometry(geo) {
    this.geo = geo;
    const n = (this.n = geo.n);
    const u = unhex(geo.u);
    const v = unhex(geo.v);

    if (this.frame.length !== n * 3) this.frame = new Uint8Array(n * 3);

    this.ang = new Float32Array(n);
    this.hgt = new Float32Array(n);
    this.rad = new Float32Array(n);
    this.sinA = new Float32Array(n);
    this.cosA = new Float32Array(n);

    // Pixels before the first loop are the lead-in cable, and anything past
    // `used` is spare strip.  Neither is on the tree.
    this.offset = geo.offset || 0;
    this.used = geo.used === undefined ? n : geo.used;

    const taper = 1 - geo.top_ratio;
    for (let i = 0; i < n; i++) {
      const a = (u[i] / 256) * Math.PI * 2;
      const h = v[i] / 255;
      this.ang[i] = a;
      this.hgt[i] = h;
      this.rad[i] = 1 - h * taper;
      this.sinA[i] = Math.sin(a);
      this.cosA[i] = Math.cos(a);
    }

    // Back of the tree first, so nearer pixels paint over further ones.
    this.order = Array.from({ length: n }, (_, i) => i)
      .filter((i) => i >= this.offset && i < this.used)
      .sort((a, b) => this.cosA[a] - this.cosA[b]);

    // Which loop each pixel belongs to.
    this.loopOfPixel = new Int32Array(n);
    geo.counts.forEach((count, loop) => {
      const start = geo.starts[loop];
      for (let j = 0; j < count; j++) this.loopOfPixel[start + j] = loop;
    });

    this.buildCells(this.state ? this.state.config.cols : 16, u);
  },

  /* Group pixels into a rows x cols grid on the surface of the cone.
   * Rows run bottom to top regardless of which end of the strip is at the
   * bottom, so "up" always means up. */
  buildCells(cols, u) {
    u = u || unhex(this.geo.u);
    const turns = this.geo.turns;
    const rows = turns;

    const byHeight = Array.from({ length: turns }, (_, k) => k)
      .sort((a, b) => this.geo.heights[a] - this.geo.heights[b]);
    const rowOfLoop = new Int32Array(turns);
    byHeight.forEach((loop, row) => { rowOfLoop[loop] = row; });

    const buckets = [];
    for (let r = 0; r < rows; r++) {
      buckets.push(Array.from({ length: cols }, () => []));
    }
    const pixelsInRow = Array.from({ length: rows }, () => []);
    for (let i = 0; i < this.n; i++) {
      const row = rowOfLoop[this.loopOfPixel[i]];
      const col = Math.round((u[i] / 256) * cols) % cols;
      buckets[row][col].push(i);
      pixelsInRow[row].push(i);
    }

    // The loops near the top have fewer pixels than there are columns, so
    // some cells end up with none of their own.  Borrow the nearest pixel in
    // the same loop instead: neighbouring cells then share a pixel, which is
    // the best the real tree can do, and nothing on the grid is invisible.
    for (let row = 0; row < rows; row++) {
      const candidates = pixelsInRow[row];
      if (!candidates.length) continue;
      for (let col = 0; col < cols; col++) {
        if (buckets[row][col].length) continue;
        const wanted = col / cols;
        let best = candidates[0];
        let bestGap = 2;
        for (const i of candidates) {
          let gap = Math.abs(u[i] / 256 - wanted);
          if (gap > 0.5) gap = 1 - gap;
          if (gap < bestGap) { bestGap = gap; best = i; }
        }
        buckets[row][col].push(best);
      }
    }

    this.rows = rows;
    this.cols = cols;
    this.rowOfLoop = rowOfLoop;
    this.cells = buckets.map((row) => row.map((list) => Int32Array.from(list)));
  },

  /* ───────────────────── frame writing ───────────────────── */
  clear() {
    this.frame.fill(0);
    this.dirty = true;
  },

  setPixel(i, r, g, b) {
    const at = i * 3;
    const f = this.frame;
    f[at] = r; f[at + 1] = g; f[at + 2] = b;
  },

  addPixel(i, r, g, b) {
    const at = i * 3;
    const f = this.frame;
    f[at] = Math.min(255, f[at] + r);
    f[at + 1] = Math.min(255, f[at + 1] + g);
    f[at + 2] = Math.min(255, f[at + 2] + b);
  },

  /* Light a whole grid cell: every pixel that falls in it. */
  setCell(row, col, r, g, b) {
    if (row < 0 || row >= this.rows) return;
    const list = this.cells[row][((col % this.cols) + this.cols) % this.cols];
    for (let k = 0; k < list.length; k++) this.setPixel(list[k], r, g, b);
    this.dirty = true;
  },

  /* ───────────────────── transport ───────────────────── */
  async send() {
    // Never send once we have handed the tree back.  A queued frame used to
    // arrive after the hand-back, which put the board into live mode showing
    // a blank frame until it timed out -- the tree went dark for several
    // seconds every time you switched away from a game.
    if (!this.live) { this.queued = false; return; }
    if (this.inflight) { this.queued = true; return; }
    this.inflight = true;
    this.lastSent = performance.now();
    try {
      const res = await fetch('/api/frame', {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: this.frame,
      });
      // The board hands back any button presses on the same round trip, so
      // a controller costs us no extra requests while we are driving.
      if (this.buttonsWired) {
        try {
          const data = await res.json();
          for (const press of data.presses || []) {
            Games.input(press === 'select' ? 'action' : press);
          }
        } catch (err) { /* older board, or no body */ }
      }
    } catch (err) {
      this.setStatus('lost the tree — reconnecting', 'bad');
    }
    this.inflight = false;
    if (this.queued && this.live) { this.queued = false; this.send(); }
    else this.queued = false;
  },

  /* Apply a config change on the board and pick up the new geometry. */
  async pushConfig(patch) {
    const payload = await this.post('/api/config', patch);
    this.state = payload;
    this.setGeometry(await this.get('/api/geometry'));
    this.setStatus();
    if (Admin.ready) Admin.syncFromState();
    return payload;
  },

  /* Collect presses from the physical buttons.
   *
   * Normally this is never needed: while the board is running its own show
   * it handles its own buttons, and while this page is driving the presses
   * ride back on the reply to each frame we post.  Kept for other clients
   * and for debugging. */
  async pollInput() {
    if (this.pollingInput) return;
    this.pollingInput = true;
    try {
      const data = await this.get('/api/input');
      for (const press of data.presses || []) {
        if (press === 'select') Games.input('action');
        else Games.input(press);
      }
    } catch (err) {
      /* nothing waiting, or the board is busy */
    }
    this.pollingInput = false;
  },

  /* Give the tree back to its own idle show. */
  async releaseTree() {
    this.live = false;
    this.queued = false;
    try {
      await this.post('/api/mode', { mode: 'idle' });
      await this.pollFrame();
      await this.refresh();
    } catch (err) {
      /* the board will time out back to idle by itself */
    }
  },

  /* Read back the frame the board is showing, for the preview. */
  async pollFrame() {
    if (this.polling) return;
    this.polling = true;
    try {
      const data = await this.get('/api/frame');
      this.boardMode = data.mode || this.boardMode;
      const hex = data.hex || '';
      const bytes = Math.min(this.frame.length, hex.length / 2);
      for (let i = 0; i < bytes; i++) {
        this.frame[i] = parseInt(hex.substr(i * 2, 2), 16);
      }
      for (let i = bytes; i < this.frame.length; i++) this.frame[i] = 0;
    } catch (err) {
      /* the tree may simply be busy; the next poll will catch up */
    }
    this.polling = false;
  },

  /* ───────────────────── activities ───────────────────── */
  startActivity(activity) {
    this.stopActivity(false);
    this.activity = activity;
    this.live = true;
    this.queued = false;
    // No clear() here either: the activity paints the whole frame on its
    // first tick, so blanking first would show one black frame.
    if (activity.start) activity.start();
    this.setStatus();
  },

  stopActivity(backToIdle = true) {
    if (this.activity && this.activity.stop) this.activity.stop();
    this.activity = null;
    this.live = false;
    this.queued = false;
    // The frame is deliberately *not* cleared: leaving the last one on show
    // until the board's own picture arrives is what stops the preview
    // blinking black on every switch.
    if (backToIdle) {
      this.post('/api/mode', { mode: 'idle' })
        .then(() => this.pollFrame())
        .catch(() => {});
      this.refresh().catch(() => {});
    }
    this.setStatus();
  },

  /* ───────────────────── main loop ───────────────────── */
  loop() {
    const now = performance.now();

    if (this.activity && this.activity.tick) {
      try {
        this.activity.tick(now);
      } catch (err) {
        console.error(err);
        this.toast('that game crashed — stopping', true);
        this.stopActivity();
      }
    }

    // Send at most ~15 frames a second, and keep a heartbeat going so the
    // board does not drop back to the idle show mid-game.
    if (this.live) {
      const since = now - this.lastSent;
      if ((this.dirty && since > 66) || since > 1500) {
        this.dirty = false;
        this.send();
      }
    } else if (now - this.lastPolled > PREVIEW_POLL_MS && !document.hidden) {
      // Not driving: mirror whatever the tree is doing by itself.  The board
      // serves requests synchronously and cannot update the strip while it
      // is answering us, so this is deliberately unhurried -- the preview is
      // a courtesy, the lights are the point.
      this.lastPolled = now;
      this.pollFrame();
    }

    this.updateNow();
    this.drawPreview();
    requestAnimationFrame(() => this.loop());
  },

  /* ───────────────────── preview ───────────────────── */
  drawPreview() {
    const canvas = this.canvas;
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.min(420, canvas.parentElement.clientWidth);
    const height = Math.round(width * 0.82);
    if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      canvas.style.height = height + 'px';
    }

    const ctx = this.ctx;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const cx = width / 2;
    const top = 14;
    const bottom = height - 16;
    const treeHeight = bottom - top;
    const baseRadius = width * 0.40;

    // Faint cone so you can read the shape even when the tree is dark.
    ctx.strokeStyle = 'rgba(120,160,220,.16)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cx - baseRadius, bottom);
    ctx.lineTo(cx - baseRadius * this.geo.top_ratio, top);
    ctx.lineTo(cx + baseRadius * this.geo.top_ratio, top);
    ctx.lineTo(cx + baseRadius, bottom);
    ctx.closePath();
    ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(cx, bottom, baseRadius, baseRadius * 0.17, 0, 0, Math.PI * 2);
    ctx.stroke();

    const frame = this.frame;
    const order = this.order;
    ctx.globalCompositeOperation = 'lighter';
    for (let k = 0; k < order.length; k++) {
      const i = order[k];
      const at = i * 3;
      const r = frame[at], g = frame[at + 1], b = frame[at + 2];
      if (r + g + b < 10) continue;

      const depth = (this.cosA[i] + 1) / 2;          // 0 = behind, 1 = front
      const x = cx + this.rad[i] * baseRadius * this.sinA[i];
      const y = bottom - this.hgt[i] * treeHeight;
      const size = 1.6 + 2.2 * depth;
      const alpha = 0.18 + 0.82 * depth;

      ctx.fillStyle = `rgba(${r},${g},${b},${alpha})`;
      ctx.beginPath();
      ctx.arc(x, y, size, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = `rgba(${r},${g},${b},${alpha * 0.14})`;
      ctx.beginPath();
      ctx.arc(x, y, size * 3, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalCompositeOperation = 'source-over';
  },

  /* ───────────────── what is on the tree ───────────────── */
  /* One line that always says what the tree is showing, and flares when
   * that changes, so switching between things is visible rather than
   * something you have to infer from the lights. */
  describeNow() {
    if (this.activity) {
      return {
        kind: 'live',
        label: this.activity.label || 'Your phone',
        sub: 'you are driving',
      };
    }
    const mode = this.boardMode || (this.state && this.state.mode) || 'idle';
    if (mode === 'live') {
      return { kind: 'live', label: 'Another device', sub: 'is driving the tree' };
    }
    if (mode === 'game') {
      return { kind: 'live', label: 'Snake', sub: 'on the tree, played with the buttons' };
    }
    if (mode === 'test') {
      const dark = this.frame && !this.frame.some((v) => v > 0);
      return dark
        ? { kind: 'off', label: 'Lights off', sub: '' }
        : { kind: 'test', label: 'Setup pattern', sub: 'from the admin tab' };
    }
    return {
      kind: 'idle',
      label: 'Idle show',
      sub: this.state ? this.patternLabel(this.state.config.idle_pattern) : '',
    };
  },

  updateNow() {
    const now = this.describeNow();
    const key = now.kind + '|' + now.label + '|' + now.sub;
    if (key === this._nowKey) return;
    const first = this._nowKey === undefined;
    this._nowKey = key;

    const box = document.getElementById('nowShowing');
    document.getElementById('nowLabel').textContent = now.label;
    document.getElementById('nowSub').textContent = now.sub;
    box.className = 'now ' + now.kind;
    if (first) return;

    // Restart the flare even if it is already running.
    void box.offsetWidth;
    box.classList.add('changed');
    clearTimeout(this._nowFlare);
    this._nowFlare = setTimeout(() => box.classList.remove('changed'), 1000);
  },

  /* ───────────────────── chrome ───────────────────── */
  wireTabs() {
    document.getElementById('tabs').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-tab]');
      if (!button) return;
      const name = button.dataset.tab;
      const previous = this.tab;
      this.tab = name;
      document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b === button));
      document.querySelectorAll('.tab').forEach((section) => {
        section.classList.toggle('on', section.id === 'tab-' + name);
      });
      // Leaving a tab that was driving the tree hands control back.
      if (this.activity && this.activity.tab !== name) this.stopActivity();
      // Admin's test patterns are not activities, so clear them by hand --
      // otherwise a calibration dot sits on the tree after you walk away.
      if (previous === 'admin' && name !== 'admin') this.releaseTree();
      if (name === 'idle') Idle.syncFromState();
      if (name === 'admin') Admin.syncFromState();
    });
  },

  setStatus(message, kind) {
    const el = document.getElementById('status');
    if (message) {
      el.textContent = message;
      el.className = 'status ' + (kind || '');
      return;
    }
    const state = this.state;
    if (!state) return;
    this.buttonsWired = !!(state.buttons && state.buttons.active);
    const bits = [];
    bits.push(this.live ? 'you are driving' : 'idle show: ' + this.patternLabel(state.config.idle_pattern));
    bits.push(state.config.num_pixels + ' px · ' + this.geo.turns + ' loops');
    el.textContent = bits.join(' · ');
    el.className = 'status ' + (this.live ? 'live' : '');

    const note = document.getElementById('stageNote');
    if (!state.persisted) {
      note.textContent = 'settings apply now but cannot be saved while the USB drive is mounted';
    } else {
      note.textContent = state.network.mode === 'ap'
        ? 'hotspot ' + state.config.ap_ssid + ' · http://' + state.network.address + '/'
        : 'http://' + state.network.address + '/';
    }
  },

  patternLabel(id) {
    const found = (this.state.patterns || []).find((p) => p.id === id);
    return found ? found.label : id;
  },

  toast(message, bad) {
    const el = document.getElementById('toast');
    el.textContent = message;
    el.className = 'toast show' + (bad ? ' bad' : '');
    clearTimeout(this._toast);
    this._toast = setTimeout(() => { el.className = 'toast'; }, 2400);
  },
};

/* How often the page asks the board what it is showing, in milliseconds.
 * Every request steals time from the strip, so keep this gentle. */
const PREVIEW_POLL_MS = 400;

function unhex(text) {
  const out = new Uint8Array(text.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(text.substr(i * 2, 2), 16);
  }
  return out;
}

function clamp(value, low, high) {
  return value < low ? low : value > high ? high : value;
}

/* Small HSV helper shared by the games. h, s, v are 0..1. */
function hsv(h, s, v) {
  h = ((h % 1) + 1) % 1;
  const i = Math.floor(h * 6);
  const f = h * 6 - i;
  const p = v * (1 - s);
  const q = v * (1 - f * s);
  const t = v * (1 - (1 - f) * s);
  let r, g, b;
  switch (i % 6) {
    case 0: r = v; g = t; b = p; break;
    case 1: r = q; g = v; b = p; break;
    case 2: r = p; g = v; b = t; break;
    case 3: r = p; g = q; b = v; break;
    case 4: r = t; g = p; b = v; break;
    default: r = v; g = p; b = q;
  }
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}
