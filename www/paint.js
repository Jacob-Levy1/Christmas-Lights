/* Finger painting and photos.
 *
 * Both work the same way: something is drawn on a small 2D canvas, and then
 * every LED samples the canvas at its own position on the tree.
 *
 *   wrap  — the canvas wraps right round the tree, left edge to right edge.
 *   front — the canvas is projected onto the half of the tree facing you,
 *           corrected for the cone, so a face still looks like a face.  The
 *           horizontal position of a pixel on screen is radius * sin(angle),
 *           not the angle itself, which is what makes it look right.
 */
const Paint = {
  draw: { projection: 'front', brush: 'color', size: 6, color: '#ff2d2d' },
  photo: {
    projection: 'front', fit: 'cover', image: null,
    brightness: 105, contrast: 135, saturation: 165, spin: 0, offset: 0, turn: 0,
  },

  init() {
    this.paintCanvas = document.getElementById('paint');
    this.paintCtx = this.paintCanvas.getContext('2d', { willReadFrequently: true });
    this.photoCanvas = document.getElementById('photo');
    this.photoCtx = this.photoCanvas.getContext('2d', { willReadFrequently: true });
    this.clearPaint();
    this.drawPhotoPlaceholder();

    this.wireDraw();
    this.wirePhoto();

    // Entering the Draw tab hands the tree straight to your canvas.
    document.getElementById('tabs').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-tab]');
      if (!button) return;
      if (button.dataset.tab === 'draw') this.startDraw();
    });
  },

  /* ───────────────────── shared sampling ───────────────────── */
  /* Paint one frame of the tree from a canvas. */
  project(ctx, width, height, projection, turn, backLevel) {
    const data = ctx.getImageData(0, 0, width, height).data;
    const frame = App.frame;
    const spin = (turn || 0) * Math.PI * 2;
    const back = backLevel === undefined ? 0.22 : backLevel;

    for (let i = 0; i < App.n; i++) {
      const angle = App.ang[i] + spin;
      let sx;
      let level = 1;

      if (projection === 'wrap') {
        let fraction = (angle / (Math.PI * 2)) % 1;
        if (fraction < 0) fraction += 1;
        sx = fraction * (width - 1);
      } else {
        const facing = Math.cos(angle);
        // Where this pixel appears horizontally when you stand in front.
        sx = (0.5 + 0.5 * App.rad[i] * Math.sin(angle)) * (width - 1);
        if (facing <= 0) level = back;
      }

      const sy = (1 - App.hgt[i]) * (height - 1);
      const at = ((Math.round(sy) * width) + Math.round(sx)) * 4;
      const out = i * 3;
      frame[out] = data[at] * level;
      frame[out + 1] = data[at + 1] * level;
      frame[out + 2] = data[at + 2] * level;
    }
    App.dirty = true;
  },

  /* ───────────────────────── drawing ───────────────────────── */
  wireDraw() {
    const canvas = this.paintCanvas;
    let drawing = false;
    let hue = 0;

    const position = (event) => {
      const box = canvas.getBoundingClientRect();
      return {
        x: ((event.clientX - box.left) / box.width) * canvas.width,
        y: ((event.clientY - box.top) / box.height) * canvas.height,
      };
    };

    const stroke = (event) => {
      const { x, y } = position(event);
      const ctx = this.paintCtx;
      if (this.draw.brush === 'erase') {
        ctx.fillStyle = '#000';
      } else if (this.draw.brush === 'rainbow') {
        hue = (hue + 0.02) % 1;
        const [r, g, b] = hsv(hue, 1, 1);
        ctx.fillStyle = `rgb(${r},${g},${b})`;
      } else {
        ctx.fillStyle = this.draw.color;
      }
      ctx.beginPath();
      ctx.arc(x, y, this.draw.size / 2, 0, Math.PI * 2);
      ctx.fill();
      // Wrap the brush round the seam so a stroke off one edge continues
      // on the other, like it does on the real tree.
      if (this.draw.projection === 'wrap') {
        const shift = x < canvas.width / 2 ? canvas.width : -canvas.width;
        ctx.beginPath();
        ctx.arc(x + shift, y, this.draw.size / 2, 0, Math.PI * 2);
        ctx.fill();
      }
      this.startDraw();
    };

    canvas.addEventListener('pointerdown', (event) => {
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
    });
    document.getElementById('paintClear').addEventListener('click', () => {
      this.clearPaint();
      this.startDraw();
    });
    document.getElementById('drawProjection').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-proj]');
      if (!button) return;
      this.draw.projection = button.dataset.proj;
      document.querySelectorAll('#drawProjection button').forEach((b) => {
        b.classList.toggle('on', b === button);
      });
      this.startDraw();
    });
  },

  syncBrushButtons() {
    document.getElementById('brushRainbow').classList.toggle('on', this.draw.brush === 'rainbow');
    document.getElementById('brushErase').classList.toggle('on', this.draw.brush === 'erase');
  },

  clearPaint() {
    const ctx = this.paintCtx;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, this.paintCanvas.width, this.paintCanvas.height);
  },

  startDraw() {
    if (App.activity && App.activity.name === 'draw') return;
    App.startActivity({
      name: 'draw',
      tab: 'draw',
      tick: () => {
        this.project(
          this.paintCtx,
          this.paintCanvas.width,
          this.paintCanvas.height,
          this.draw.projection,
          0,
        );
      },
    });
  },

  /* ───────────────────────── photos ───────────────────────── */
  wirePhoto() {
    document.getElementById('photoFile').addEventListener('change', (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      const image = new Image();
      image.onload = () => {
        this.photo.image = image;
        this.renderPhoto();
        this.startPhoto();
        App.toast('picture loaded');
      };
      image.onerror = () => App.toast('could not read that picture', true);
      image.src = URL.createObjectURL(file);
    });

    const sliders = ['photoBrightness', 'photoContrast', 'photoSaturation', 'photoSpin', 'photoOffset'];
    const keys = { photoBrightness: 'brightness', photoContrast: 'contrast', photoSaturation: 'saturation', photoSpin: 'spin', photoOffset: 'offset' };
    sliders.forEach((id) => {
      document.getElementById(id).addEventListener('input', (event) => {
        this.photo[keys[id]] = Number(event.target.value);
        if (keys[id] === 'offset') this.photo.turn = this.photo.offset / 100;
        this.renderPhoto();
      });
    });

    document.getElementById('photoProjection').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-proj]');
      if (!button) return;
      this.photo.projection = button.dataset.proj;
      document.querySelectorAll('#photoProjection button').forEach((b) => {
        b.classList.toggle('on', b === button);
      });
    });
    document.getElementById('photoFit').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-fit]');
      if (!button) return;
      this.photo.fit = button.dataset.fit;
      document.querySelectorAll('#photoFit button').forEach((b) => {
        b.classList.toggle('on', b === button);
      });
      this.renderPhoto();
    });

    document.getElementById('photoSend').addEventListener('click', () => {
      if (!this.photo.image) { App.toast('choose a picture first', true); return; }
      this.startPhoto();
    });
    document.getElementById('photoStop').addEventListener('click', () => App.stopActivity());
  },

  drawPhotoPlaceholder() {
    const ctx = this.photoCtx;
    const { width, height } = this.photoCanvas;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = '#3a4a68';
    ctx.font = '9px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('no picture yet', width / 2, height / 2);
  },

  renderPhoto() {
    const image = this.photo.image;
    if (!image) return;
    const ctx = this.photoCtx;
    const { width, height } = this.photoCanvas;
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
      tab: 'photo',
      tick: (now) => {
        const seconds = Math.min(0.2, (now - previous) / 1000);
        previous = now;
        turn += (this.photo.spin / 100) * seconds * 0.4;
        const base = this.photo.offset / 100;
        this.project(
          this.photoCtx,
          this.photoCanvas.width,
          this.photoCanvas.height,
          this.photo.projection,
          turn + base,
        );
      },
    });
  },
};
