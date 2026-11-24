/* Finger painting and photos, one square per light.
 *
 * The drawing surface *is* the tree's pixels: the strip unwrapped, one row
 * per loop, one square per LED, with the front of the tree down the middle
 * and the rows narrowing towards the top the way the real loops do.  Touch
 * a square and exactly that light comes on.  Nothing is sampled or scaled,
 * so what you draw is what the tree shows.
 *
 * Photos go through the same grid.  The picture is reduced to one colour
 * per LED and you see those colours, at that resolution -- 300 lights is a
 * very small screen, and it is more useful to see that honestly than to
 * admire a full-resolution preview of something the tree cannot show.
 */

/* The strip, laid out as the tree unwrapped. */
const Grid = {
  rows: [],      // bottom row first; each is the loop's cells, left to right
  widest: 1,

  build() {
    const geo = App.geo;
    const byHeight = Array.from({ length: geo.turns }, (_, k) => k)
      .sort((a, b) => geo.heights[a] - geo.heights[b]);

    this.rows = byHeight.map((loop) => {
      const start = geo.starts[loop];
      const cells = [];
      for (let j = 0; j < geo.counts[loop]; j++) {
        const index = start + j;
        // Angle as -0.5..0.5 with the front of the tree at 0, so the middle
        // of each row faces you and the back splits to the two edges.
        let angle = App.ang[index] / (Math.PI * 2);
        angle = ((angle + 0.5) % 1) - 0.5;
        cells.push({ index, angle });
      }
      cells.sort((a, b) => a.angle - b.angle);
      return cells;
    });
    this.widest = Math.max(1, ...this.rows.map((row) => row.length));
    return this;
  },

  /* The square size, in screen pixels, that would fit the whole tree in. */
  baseCell(canvas) {
    const wrap = canvas.parentElement;
    const available = (wrap && wrap.clientWidth) || canvas.clientWidth || 300;
    return Math.max(3, Math.floor(available / this.widest));
  },

  /* Zoom that makes a square big enough to hit with a finger. */
  comfortable(canvas, wanted) {
    const base = this.baseCell(canvas);
    return Math.max(1, Math.min(8, (wanted || 24) / base));
  },

  /* Size the canvas to the grid's shape at this zoom.  Past 1x it is wider
   * than the space it sits in, and the wrapper scrolls. */
  fit(canvas, zoom) {
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    const cell = Math.max(3, Math.round(this.baseCell(canvas) * (zoom || 1)));
    canvas.style.width = cell * this.widest + 'px';
    canvas.style.height = cell * this.rows.length + 'px';
    canvas.width = cell * this.widest * ratio;
    canvas.height = cell * this.rows.length * ratio;
    return cell;
  },

  cellSize(canvas) {
    return Math.max(1, canvas.width / this.widest);
  },

  /* Where a row starts, so each row is centred on the front of the tree. */
  rowLeft(row, cell, canvas) {
    return (canvas.width - this.rows[row].length * cell) / 2;
  },

  /* Which light is under a touch, or null. */
  cellAt(canvas, clientX, clientY) {
    const box = canvas.getBoundingClientRect();
    const cell = this.cellSize(canvas);
    const x = ((clientX - box.left) / box.width) * canvas.width;
    const y = ((clientY - box.top) / box.height) * canvas.height;

    let row = this.rows.length - 1 - Math.floor(y / cell);
    if (row < 0) row = 0;
    if (row >= this.rows.length) row = this.rows.length - 1;

    let col = Math.floor((x - this.rowLeft(row, cell, canvas)) / cell);
    if (col < 0) col = 0;
    if (col >= this.rows[row].length) col = this.rows[row].length - 1;
    return { row, col };
  },

  /* The lights a brush covers, kept round in angle rather than in columns:
   * a row higher up has fewer, wider cells, so stepping the same number of
   * columns there would cover much more of the tree. */
  brush(row, col, radius) {
    const centre = this.rows[row][col];
    const out = [centre.index];
    for (let dr = -radius; dr <= radius; dr++) {
      const r = row + dr;
      if (r < 0 || r >= this.rows.length || dr === 0 && radius === 0) continue;
      const cells = this.rows[r];
      const span = radius - Math.abs(dr);
      if (span < 0) continue;
      // nearest cell in this row to the brush's angle
      let near = 0;
      let best = 2;
      for (let c = 0; c < cells.length; c++) {
        let gap = Math.abs(cells[c].angle - centre.angle);
        if (gap > 0.5) gap = 1 - gap;
        if (gap < best) { best = gap; near = c; }
      }
      for (let c = near - span; c <= near + span; c++) {
        const wrapped = ((c % cells.length) + cells.length) % cells.length;
        out.push(cells[wrapped].index);
      }
    }
    return out;
  },

  /* Paint the grid from a frame of RGB bytes. */
  render(canvas, frame) {
    const ctx = canvas.getContext('2d');
    const cell = this.cellSize(canvas);
    ctx.fillStyle = '#05070d';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // A visible gutter once the squares are big enough to show one, so you
    // can see where one light ends and the next begins.
    const gap = cell > 14 ? 2 : cell > 6 ? 1 : 0;
    for (let row = 0; row < this.rows.length; row++) {
      const left = this.rowLeft(row, cell, canvas);
      const y = canvas.height - (row + 1) * cell;
      for (let col = 0; col < this.rows[row].length; col++) {
        const at = this.rows[row][col].index * 3;
        const r = frame[at], g = frame[at + 1], b = frame[at + 2];
        ctx.fillStyle = (r + g + b) === 0 ? '#121a2b' : `rgb(${r},${g},${b})`;
        ctx.fillRect(Math.round(left + col * cell), Math.round(y),
                     Math.ceil(cell - gap), Math.ceil(cell - gap));
      }
    }
  },
};

const Paint = {
  draw: { brush: 'color', size: 2, color: '#ff2d2d', zoom: 1, mode: 'draw' },
  photo: {
    projection: 'front', fit: 'cover', image: null,
    brightness: 105, contrast: 135, saturation: 165, spin: 0, offset: 0, turn: 0,
  },

  init() {
    this.paintCanvas = document.getElementById('paint');
    this.photoCanvas = document.getElementById('photo');
    // Full-resolution working copy of the picture; never shown, only sampled.
    this.source = document.createElement('canvas');
    this.source.width = 160;
    this.source.height = 220;
    this.sourceCtx = this.source.getContext('2d', { willReadFrequently: true });

    this.pixels = new Uint8Array(App.n * 3);       // what you have drawn
    this.photoPixels = new Uint8Array(App.n * 3);  // the picture, per light

    this.wireDraw();
    this.wirePhoto();
    this.relayout();

    // A tab that has just been shown can be measured; one that is hidden
    // cannot, which is why the sizing waits for this rather than happening
    // once at startup.
    document.getElementById('tabs').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-tab]');
      if (!button) return;
      if (button.dataset.tab === 'draw') this.layoutDraw();
      if (button.dataset.tab === 'photo') this.layoutPhoto();
    });
  },

  /* The grid changes whenever the tree's shape does. */
  relayout() {
    Grid.build();
    if (this.pixels.length !== App.n * 3) this.pixels = new Uint8Array(App.n * 3);
    if (this.photoPixels.length !== App.n * 3) this.photoPixels = new Uint8Array(App.n * 3);
    this.layoutDraw();
    this.layoutPhoto();

    const size = document.getElementById('brushSize');
    document.getElementById('brushOut').textContent =
      size.value === '1' ? 'one light' : size.value + ' lights across';
  },

  /* Size the drawing grid.  A hidden tab measures zero, so this does
   * nothing until the tab is actually on screen -- the first attempt
   * happens at startup, when Play is showing and this one is not. */
  layoutDraw() {
    const wrap = document.getElementById('paintWrap');
    if (!wrap || !wrap.clientWidth) return false;

    const firstTime = !this.drawSized;
    this.drawSized = true;
    if (firstTime) {
      // Start close enough in to actually hit a square: the whole tree
      // across a phone leaves them about nine pixels wide.
      this.draw.zoom = Grid.comfortable(this.paintCanvas, 26);
      document.getElementById('zoomLevel').value = Math.round(this.draw.zoom * 100);
    }
    this.applyZoom(firstTime);
    return true;
  },

  layoutPhoto() {
    const wrap = this.photoCanvas.parentElement;
    if (!wrap || !wrap.clientWidth) return false;
    Grid.fit(this.photoCanvas, 1);
    if (this.photo.image) this.samplePhoto();
    else Grid.render(this.photoCanvas, this.photoPixels);
    return true;
  },

  /* Resize the drawing canvas, keeping whatever was in the middle there.
   * Zoomed in, the front of the tree is what you want in view, so the
   * first look starts centred on it rather than at the back-left edge. */
  applyZoom(centreOnFront) {
    const wrap = document.getElementById('paintWrap');
    const canvas = this.paintCanvas;
    const was = wrap.scrollWidth > wrap.clientWidth
      ? (wrap.scrollLeft + wrap.clientWidth / 2) / wrap.scrollWidth
      : 0.5;

    const cell = Grid.fit(canvas, this.draw.zoom);
    Grid.render(canvas, this.pixels);

    const middle = centreOnFront ? 0.5 : was;
    wrap.scrollLeft = Math.max(0, middle * wrap.scrollWidth - wrap.clientWidth / 2);

    const out = document.getElementById('zoomOut');
    if (out) out.textContent = cell + ' px a light';
  },

  /* ───────────────────────── drawing ───────────────────────── */
  wireDraw() {
    const canvas = this.paintCanvas;
    let drawing = false;
    let hue = 0;

    const stroke = (event) => {
      if (this.draw.mode !== 'draw') return;
      const spot = Grid.cellAt(canvas, event.clientX, event.clientY);
      if (!spot) return;
      let colour;
      if (this.draw.brush === 'erase') {
        colour = [0, 0, 0];
      } else if (this.draw.brush === 'rainbow') {
        hue = (hue + 0.03) % 1;
        colour = hsv(hue, 1, 1);
      } else {
        const hex = this.draw.color;
        colour = [parseInt(hex.substr(1, 2), 16), parseInt(hex.substr(3, 2), 16),
                  parseInt(hex.substr(5, 2), 16)];
      }
      for (const index of Grid.brush(spot.row, spot.col, this.draw.size - 1)) {
        const at = index * 3;
        this.pixels[at] = colour[0];
        this.pixels[at + 1] = colour[1];
        this.pixels[at + 2] = colour[2];
      }
      Grid.render(canvas, this.pixels);
      this.startDraw();
      App.dirty = true;
    };

    canvas.addEventListener('pointerdown', (event) => {
      if (this.draw.mode !== 'draw') return;
      event.preventDefault();
      drawing = true;
      canvas.setPointerCapture(event.pointerId);
      stroke(event);
    });
    canvas.addEventListener('pointermove', (event) => { if (drawing) stroke(event); });
    canvas.addEventListener('pointerup', () => { drawing = false; });
    canvas.addEventListener('pointercancel', () => { drawing = false; });

    document.getElementById('brushColor').addEventListener('input', (event) => {
      this.draw.color = event.target.value;
      this.draw.brush = 'color';
      this.syncBrushButtons();
    });
    document.getElementById('brushRainbow').addEventListener('click', () => {
      this.draw.brush = this.draw.brush === 'rainbow' ? 'color' : 'rainbow';
      this.syncBrushButtons();
    });
    document.getElementById('brushErase').addEventListener('click', () => {
      this.draw.brush = this.draw.brush === 'erase' ? 'color' : 'erase';
      this.syncBrushButtons();
    });
    document.getElementById('brushSize').addEventListener('input', (event) => {
      this.draw.size = Number(event.target.value);
      document.getElementById('brushOut').textContent =
        this.draw.size === 1 ? 'one light' : this.draw.size + ' lights across';
    });
    document.getElementById('zoomLevel').addEventListener('input', (event) => {
      this.draw.zoom = Number(event.target.value) / 100;
      this.applyZoom(false);
    });
    document.getElementById('zoomFit').addEventListener('click', () => {
      this.draw.zoom = 1;
      document.getElementById('zoomLevel').value = 100;
      this.applyZoom(false);
    });

    document.getElementById('paintMode').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-mode]');
      if (!button) return;
      this.draw.mode = button.dataset.mode;
      document.querySelectorAll('#paintMode button').forEach((b) => {
        b.classList.toggle('on', b === button);
      });
      // In move mode the browser does the scrolling, which it will only do
      // if we stop claiming the touch for drawing.
      document.getElementById('paintWrap').classList.toggle('move', this.draw.mode === 'move');
    });

    document.getElementById('paintClear').addEventListener('click', () => {
      this.pixels.fill(0);
      Grid.render(this.paintCanvas, this.pixels);
      if (App.activity && App.activity.name === 'draw') App.dirty = true;
      else App.stopActivity();
    });
  },

  syncBrushButtons() {
    document.getElementById('brushRainbow').classList.toggle('on', this.draw.brush === 'rainbow');
    document.getElementById('brushErase').classList.toggle('on', this.draw.brush === 'erase');
  },

  startDraw() {
    if (App.activity && App.activity.name === 'draw') return;
    App.startActivity({
      name: 'draw',
      label: 'Your drawing',
      tab: 'draw',
      tick: () => { App.frame.set(this.pixels); },
    });
  },

  /* ───────────────────────── photos ───────────────────────── */
  /* One sample per light, straight from the picture.
   *
   *   front  the half of the tree facing you, corrected for the cone: a
   *          light's place across the picture is radius * sin(angle), not
   *          the angle itself, so a face still looks like a face.
   *   wrap   the picture goes the whole way round.
   */
  samplePhoto(turn) {
    const width = this.source.width;
    const height = this.source.height;
    const data = this.sourceCtx.getImageData(0, 0, width, height).data;
    const spin = ((turn === undefined ? this.photo.turn : turn) + this.photo.offset / 100)
      * Math.PI * 2;
    const front = this.photo.projection !== 'wrap';
    const out = this.photoPixels;
    out.fill(0);

    for (const row of Grid.rows) {
      for (const cell of row) {
        const i = cell.index;
        const angle = App.ang[i] + spin;
        let sx;
        let level = 1;
        if (front) {
          sx = (0.5 + 0.5 * App.rad[i] * Math.sin(angle)) * (width - 1);
          if (Math.cos(angle) <= 0) level = 0.22;
        } else {
          let fraction = (angle / (Math.PI * 2)) % 1;
          if (fraction < 0) fraction += 1;
          sx = fraction * (width - 1);
        }
        const sy = (1 - App.hgt[i]) * (height - 1);
        const at = ((Math.round(sy) * width) + Math.round(sx)) * 4;
        const to = i * 3;
        out[to] = data[at] * level;
        out[to + 1] = data[at + 1] * level;
        out[to + 2] = data[at + 2] * level;
      }
    }
    Grid.render(this.photoCanvas, out);
  },

  wirePhoto() {
    document.getElementById('photoFile').addEventListener('change', (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      const image = new Image();
      image.onload = () => {
        this.photo.image = image;
        this.renderSource();
        this.samplePhoto();
        this.startPhoto();
        App.toast('picture loaded');
      };
      image.onerror = () => App.toast('could not read that picture', true);
      image.src = URL.createObjectURL(file);
    });

    const keys = { photoBrightness: 'brightness', photoContrast: 'contrast',
                   photoSaturation: 'saturation', photoSpin: 'spin', photoOffset: 'offset' };
    Object.keys(keys).forEach((id) => {
      document.getElementById(id).addEventListener('input', (event) => {
        this.photo[keys[id]] = Number(event.target.value);
        if (keys[id] !== 'spin') this.renderSource();
        this.samplePhoto();
      });
    });

    document.getElementById('photoProjection').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-proj]');
      if (!button) return;
      this.photo.projection = button.dataset.proj;
      document.querySelectorAll('#photoProjection button').forEach((b) => {
        b.classList.toggle('on', b === button);
      });
      this.samplePhoto();
    });
    document.getElementById('photoFit').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-fit]');
      if (!button) return;
      this.photo.fit = button.dataset.fit;
      document.querySelectorAll('#photoFit button').forEach((b) => {
        b.classList.toggle('on', b === button);
      });
      this.renderSource();
      this.samplePhoto();
    });

    document.getElementById('photoSend').addEventListener('click', () => {
      if (!this.photo.image) { App.toast('choose a picture first', true); return; }
      this.startPhoto();
    });
    document.getElementById('photoStop').addEventListener('click', () => App.stopActivity());
  },

  /* The picture at full resolution, with the adjustments applied.  Only
   * ever sampled, never shown: the preview is the lights themselves. */
  renderSource() {
    const image = this.photo.image;
    if (!image) return;
    const ctx = this.sourceCtx;
    const { width, height } = this.source;
    const p = this.photo;

    ctx.filter = 'none';
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);
    ctx.filter = `brightness(${p.brightness}%) contrast(${p.contrast}%) saturate(${p.saturation}%)`;

    let dw = width, dh = height, dx = 0, dy = 0;
    if (p.fit !== 'stretch') {
      const scale = p.fit === 'cover'
        ? Math.max(width / image.width, height / image.height)
        : Math.min(width / image.width, height / image.height);
      dw = image.width * scale;
      dh = image.height * scale;
      dx = (width - dw) / 2;
      dy = (height - dh) / 2;
    }
    ctx.drawImage(image, dx, dy, dw, dh);
    ctx.filter = 'none';
  },

  startPhoto() {
    if (App.activity && App.activity.name === 'photo') return;
    let turn = this.photo.turn;
    let previous = performance.now();
    App.startActivity({
      name: 'photo',
      label: 'Your picture',
      tab: 'photo',
      tick: (now) => {
        const seconds = Math.min(0.2, (now - previous) / 1000);
        previous = now;
        if (this.photo.spin) {
          turn += (this.photo.spin / 100) * seconds * 0.4;
          this.photo.turn = turn;
          this.samplePhoto(turn);
        }
        App.frame.set(this.photoPixels);
      },
    });
  },
};
