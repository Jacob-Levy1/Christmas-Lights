/* The Patterns tab (what the tree does on its own) and the Admin tab
 * (tree shape, per-loop front calibration, hardware and wifi). */

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

const Idle = {
  ready: false,

  init() {
    this.ready = true;

    document.getElementById('patternGrid').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-pattern]');
      if (!button) return;
      if (App.activity) App.stopActivity(false);
      App.live = false;
      try {
        await App.pushConfig({ idle_pattern: button.dataset.pattern });
        await App.post('/api/mode', { mode: 'idle' });
        this.syncFromState();
        App.clear();
      } catch (err) {
        App.toast('could not change the pattern', true);
      }
    });

    const push = debounce((patch) => {
      App.pushConfig(patch).catch(() => App.toast('could not save that', true));
    }, 220);

    const bind = (id, key, scale, output, format) => {
      const input = document.getElementById(id);
      input.addEventListener('input', () => {
        const value = Number(input.value) * scale;
        if (output) document.getElementById(output).textContent = format(value);
        push({ [key]: value });
      });
    };

    bind('idleSpeed', 'idle_speed', 0.01, 'idleSpeedOut', (v) => v.toFixed(2) + '×');
    bind('idleBrightness', 'idle_brightness', 0.01, 'idleBrightnessOut', (v) => Math.round(v * 100) + '%');
    bind('idleCycleSecs', 'idle_cycle_secs', 1, 'idleCycleSecsOut', (v) => v + 's');

    document.getElementById('idlePalette').addEventListener('change', (event) => {
      push({ idle_palette: event.target.value });
    });
    document.getElementById('idleCycle').addEventListener('change', (event) => {
      push({ idle_cycle: event.target.checked });
    });

    document.getElementById('idleApply').addEventListener('click', async () => {
      if (App.activity) App.stopActivity(false);
      App.live = false;
      App.clear();
      await App.post('/api/mode', { mode: 'idle' });
      await App.refresh();
      App.toast('idle show running on the tree');
    });

    document.getElementById('allOff').addEventListener('click', async () => {
      if (App.activity) App.stopActivity(false);
      App.live = false;
      App.clear();
      await App.post('/api/mode', { mode: 'off' });
      App.toast('lights off');
    });
  },

  syncFromState() {
    if (!this.ready) this.init();
    const state = App.state;
    if (!state) return;
    const cfg = state.config;

    const grid = document.getElementById('patternGrid');
    if (grid.childElementCount !== state.patterns.length) {
      grid.innerHTML = state.patterns
        .map((p) => `<button data-pattern="${p.id}">${p.label}</button>`)
        .join('');
    }
    grid.querySelectorAll('button').forEach((b) => {
      b.classList.toggle('on', b.dataset.pattern === cfg.idle_pattern);
    });

    const palette = document.getElementById('idlePalette');
    if (palette.childElementCount !== state.palettes.length) {
      palette.innerHTML = state.palettes.map((p) => `<option value="${p}">${p}</option>`).join('');
    }
    palette.value = cfg.idle_palette;

    set('idleSpeed', Math.round(cfg.idle_speed * 100), 'idleSpeedOut', cfg.idle_speed.toFixed(2) + '×');
    set('idleBrightness', Math.round(cfg.idle_brightness * 100), 'idleBrightnessOut', Math.round(cfg.idle_brightness * 100) + '%');
    set('idleCycleSecs', cfg.idle_cycle_secs, 'idleCycleSecsOut', cfg.idle_cycle_secs + 's');
    document.getElementById('idleCycle').checked = !!cfg.idle_cycle;
  },
};

const Admin = {
  ready: false,
  loop: 0,

  init() {
    this.ready = true;
    Idle.init();

    const shape = debounce((patch) => {
      App.pushConfig(patch)
        .then(() => this.renderLoopMap())
        .catch(() => App.toast('could not apply that', true));
    }, 260);

    // --- tree shape -------------------------------------------------
    const turns = document.getElementById('turns');
    turns.addEventListener('input', () => {
      document.getElementById('turnsOut').textContent = turns.value;
      shape({ turns: Number(turns.value) });
    });

    const topRatio = document.getElementById('topRatio');
    topRatio.addEventListener('input', () => {
      document.getElementById('topRatioOut').textContent = topRatio.value + '%';
      shape({ top_ratio: Number(topRatio.value) / 100 });
    });

    const rotation = document.getElementById('rotation');
    rotation.addEventListener('input', () => {
      document.getElementById('rotationOut').textContent = rotation.value + '%';
      shape({ rotation: Number(rotation.value) / 100 });
    });

    document.getElementById('startAtBottom').addEventListener('change', (event) => {
      shape({ start_at_bottom: event.target.checked });
    });
    document.getElementById('clockwise').addEventListener('change', (event) => {
      shape({ clockwise: event.target.checked });
    });

    // --- calibration ------------------------------------------------
    const slider = document.getElementById('calSlider');
    const sendCalibration = debounce((index) => {
      App.post('/api/test', { kind: 'calibrate', loop: this.loop, index })
        .catch(() => {});
    }, 60);
    slider.addEventListener('input', () => sendCalibration(Number(slider.value)));

    document.getElementById('calSave').addEventListener('click', async () => {
      try {
        const result = await App.post('/api/test', {
          kind: 'calibrate',
          loop: this.loop,
          index: Number(slider.value),
          save: true,
        });
        App.state.config.front = result.front;
        App.state.config.calibrated = result.calibrated;
        App.setGeometry(await App.get('/api/geometry'));
        this.renderCalibration();
        App.toast(result.persisted ? 'loop ' + (this.loop + 1) + ' saved' : 'saved for now (USB drive is mounted)');
        this.gotoLoop(this.loop + 1);
      } catch (err) {
        App.toast('could not save the front for that loop', true);
      }
    });

    document.getElementById('calPrev').addEventListener('click', () => this.gotoLoop(this.loop - 1));
    document.getElementById('calNext').addEventListener('click', () => this.gotoLoop(this.loop + 1));

    document.getElementById('calInterpolate').addEventListener('click', async () => {
      await App.pushConfig({ interpolate_front: true });
      this.renderCalibration();
      await App.post('/api/test', { kind: 'front_line' });
      App.toast('filled in the loops you did not set');
    });

    document.getElementById('calReset').addEventListener('click', async () => {
      const turnsNow = App.geo.turns;
      await App.pushConfig({
        front: new Array(turnsNow).fill(0),
        calibrated: new Array(turnsNow).fill(false),
      });
      this.renderCalibration();
      App.toast('calibration cleared');
    });

    document.querySelectorAll('[data-test]').forEach((button) => {
      button.addEventListener('click', () => {
        if (App.activity) App.stopActivity(false);
        App.live = false;
        App.post('/api/test', { kind: button.dataset.test, loop: this.loop }).catch(() => {});
      });
    });

    // --- hardware / games / wifi ------------------------------------
    const show = (id, out, format) => {
      const input = document.getElementById(id);
      input.addEventListener('input', () => {
        document.getElementById(out).textContent = format(Number(input.value));
      });
      return input;
    };
    show('brightness', 'brightnessOut', (v) => v + '%');
    show('fps', 'fpsOut', (v) => v + ' fps');

    const cols = show('cols', 'colsOut', (v) => v + ' wide');
    cols.addEventListener('change', () => shape({ cols: Number(cols.value) }));

    document.getElementById('snakeWrap').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-wrap]');
      if (!button) return;
      document.querySelectorAll('#snakeWrap button').forEach((b) => b.classList.toggle('on', b === button));
      shape({ snake_wrap: button.dataset.wrap });
    });

    document.getElementById('wifiMode').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-wifi]');
      if (!button) return;
      document.querySelectorAll('#wifiMode button').forEach((b) => b.classList.toggle('on', b === button));
      const mode = button.dataset.wifi;
      document.getElementById('apFields').hidden = mode !== 'ap';
      document.getElementById('staFields').hidden = mode !== 'sta';
    });

    const buttonsEnabled = document.getElementById('buttonsEnabled');
    buttonsEnabled.addEventListener('change', () => {
      document.getElementById('buttonFields').hidden = !buttonsEnabled.checked;
    });

    document.getElementById('adminSave').addEventListener('click', () => this.save());
    document.getElementById('adminRestart').addEventListener('click', async () => {
      App.toast('restarting — reconnect in a few seconds');
      App.post('/api/restart', {}).catch(() => {});
    });

    this.syncFromState();
  },

  async save() {
    const wifiMode = document.querySelector('#wifiMode button.on').dataset.wifi;
    const patch = {
      num_pixels: Number(document.getElementById('numPixels').value),
      pin: document.getElementById('pin').value.trim() || 'GP0',
      order: document.getElementById('order').value,
      brightness: Number(document.getElementById('brightness').value) / 100,
      fps: Number(document.getElementById('fps').value),
      cols: Number(document.getElementById('cols').value),
      wifi_mode: wifiMode,
      ap_ssid: document.getElementById('apSsid').value,
      ap_password: document.getElementById('apPassword').value,
      sta_ssid: document.getElementById('staSsid').value,
      sta_password: document.getElementById('staPassword').value,
      hostname: document.getElementById('hostname').value,
      buttons_enabled: document.getElementById('buttonsEnabled').checked,
      button_up: document.getElementById('buttonUp').value.trim(),
      button_down: document.getElementById('buttonDown').value.trim(),
      button_left: document.getElementById('buttonLeft').value.trim(),
      button_right: document.getElementById('buttonRight').value.trim(),
      button_select: document.getElementById('buttonSelect').value.trim(),
    };
    try {
      const result = await App.pushConfig(patch);
      let message = result.persisted ? 'settings saved' : 'applied, but could not write to flash';
      if (result.restart_needed) message += ' — restart the tree to use the new wifi/pin';
      App.toast(message, !result.persisted);
      this.renderInfo(result);
    } catch (err) {
      App.toast('could not save the settings', true);
    }
  },

  syncFromState() {
    if (!this.ready) { this.init(); return; }
    const state = App.state;
    if (!state) return;
    const cfg = state.config;

    set('turns', App.geo.turns, 'turnsOut', App.geo.turns);
    set('topRatio', Math.round(cfg.top_ratio * 100), 'topRatioOut', Math.round(cfg.top_ratio * 100) + '%');
    set('rotation', Math.round(cfg.rotation * 100), 'rotationOut', Math.round(cfg.rotation * 100) + '%');
    set('brightness', Math.round(cfg.brightness * 100), 'brightnessOut', Math.round(cfg.brightness * 100) + '%');
    set('fps', cfg.fps, 'fpsOut', cfg.fps + ' fps');
    set('cols', cfg.cols, 'colsOut', cfg.cols + ' wide');

    document.getElementById('startAtBottom').checked = !!cfg.start_at_bottom;
    document.getElementById('clockwise').checked = !!cfg.clockwise;
    document.getElementById('numPixels').value = cfg.num_pixels;
    document.getElementById('pin').value = cfg.pin;
    document.getElementById('order').value = cfg.order;
    document.getElementById('apSsid').value = cfg.ap_ssid;
    document.getElementById('apPassword').value = cfg.ap_password;
    document.getElementById('staSsid').value = cfg.sta_ssid;
    document.getElementById('staPassword').value = cfg.sta_password;
    document.getElementById('hostname').value = cfg.hostname;
    document.getElementById('buttonsEnabled').checked = !!cfg.buttons_enabled;
    document.getElementById('buttonFields').hidden = !cfg.buttons_enabled;
    document.getElementById('buttonUp').value = cfg.button_up || '';
    document.getElementById('buttonDown').value = cfg.button_down || '';
    document.getElementById('buttonLeft').value = cfg.button_left || '';
    document.getElementById('buttonRight').value = cfg.button_right || '';
    document.getElementById('buttonSelect').value = cfg.button_select || '';

    const buttons = state.buttons || {};
    const status = document.getElementById('buttonStatus');
    if (buttons.warnings && buttons.warnings.length) {
      status.textContent = 'Problem: ' + buttons.warnings.join('; ');
      status.style.color = 'var(--red)';
    } else if (buttons.active) {
      status.textContent = 'Wired up: ' + (buttons.wired || []).join(', ');
      status.style.color = 'var(--green)';
    } else {
      status.textContent = cfg.buttons_enabled ? 'No pins claimed yet.' : '';
      status.style.color = '';
    }

    document.querySelectorAll('#snakeWrap button').forEach((b) => {
      b.classList.toggle('on', b.dataset.wrap === cfg.snake_wrap);
    });
    document.querySelectorAll('#wifiMode button').forEach((b) => {
      b.classList.toggle('on', b.dataset.wifi === cfg.wifi_mode);
    });
    document.getElementById('apFields').hidden = cfg.wifi_mode !== 'ap';
    document.getElementById('staFields').hidden = cfg.wifi_mode !== 'sta';

    this.renderLoopMap();
    this.renderCalibration();
    this.renderInfo(state);
  },

  renderLoopMap() {
    const geo = App.geo;
    const order = Array.from({ length: geo.turns }, (_, k) => k)
      .sort((a, b) => geo.heights[a] - geo.heights[b]);
    const biggest = Math.max(...geo.counts);
    document.getElementById('loopMap').innerHTML = order
      .map((loop) => {
        const height = Math.max(4, Math.round((geo.counts[loop] / biggest) * 100));
        return `<span style="height:${height}%" title="loop ${loop + 1}: ${geo.counts[loop]} pixels"></span>`;
      })
      .join('');
  },

  renderCalibration() {
    const geo = App.geo;
    if (this.loop >= geo.turns) this.loop = geo.turns - 1;
    if (this.loop < 0) this.loop = 0;

    const count = geo.counts[this.loop];
    const slider = document.getElementById('calSlider');
    slider.max = Math.max(0, count - 1);
    slider.value = geo.front_index[this.loop];

    const height = Math.round(geo.heights[this.loop] * 100);
    document.getElementById('calLabel').textContent =
      `Loop ${this.loop + 1} of ${geo.turns} — ${count} pixels, ${height}% up the tree`;

    document.getElementById('calDots').innerHTML = geo.calibrated
      .map((done, k) => {
        const classes = [done ? 'done' : '', k === this.loop ? 'at' : ''].join(' ').trim();
        return `<i class="${classes}" data-loop="${k}">${k + 1}</i>`;
      })
      .join('');
    document.querySelectorAll('#calDots i').forEach((dot) => {
      dot.addEventListener('click', () => this.gotoLoop(Number(dot.dataset.loop)));
    });
  },

  gotoLoop(index) {
    const geo = App.geo;
    this.loop = ((index % geo.turns) + geo.turns) % geo.turns;
    this.renderCalibration();
    if (App.activity) App.stopActivity(false);
    App.live = false;
    App.post('/api/test', {
      kind: 'calibrate',
      loop: this.loop,
      index: Number(document.getElementById('calSlider').value),
    }).catch(() => {});
  },

  renderInfo(state) {
    const smallest = Math.min(...App.geo.counts);
    const parts = [
      state.config.num_pixels + ' pixels in ' + App.geo.turns + ' loops (' + App.geo.counts.join(', ') + ')',
      state.config.cols > smallest
        ? 'the smallest loop has ' + smallest + ' pixels, so the top of the game grid ('
          + state.config.cols + ' columns) shares pixels between columns'
        : 'every game column has its own pixel on every loop',
      'free memory ' + Math.round((state.free_memory || 0) / 1024) + ' kB',
      'up ' + Math.round((state.uptime || 0) / 60) + ' min',
      state.persisted ? 'settings saved to flash' : 'cannot write to flash while the USB drive is mounted',
    ];
    document.getElementById('adminInfo').textContent = parts.join(' · ');
  },
};

function set(id, value, outId, outText) {
  const element = document.getElementById(id);
  if (element) element.value = value;
  if (outId) {
    const out = document.getElementById(outId);
    if (out) out.textContent = outText;
  }
}
