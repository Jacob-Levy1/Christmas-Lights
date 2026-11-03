/* The Patterns tab (what the tree does on its own) and the Admin tab
 * (tree shape, per-loop front calibration, hardware and wifi). */

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

/* Parse a raw level listing.
 *
 * Accepts the shorthand people actually type:
 *
 *   0 20l, 1 17l, 2 15l      level number then LED count
 *   0: 20                    one per line, with a colon or equals
 *   20, 17, 15               just the counts, bottom level first
 *   20 17 15                 the same, spaces instead of commas
 *   level 0 20               spelled out
 *   lead-in 12               pixels before the first loop
 *   # anything after a hash is a comment
 *
 * Levels are numbered from 0 at the bottom of the tree.  Returns the counts
 * bottom level first, plus any lead-in, plus anything it could not read.
 */
function parseLevels(text) {
  const errors = [];
  const indexed = new Map();
  const bare = [];
  let offset = null;
  let sawIndexed = false;

  const entries = String(text || '')
    .replace(/#[^\n]*/g, '')
    .split(/[\n,;]+/)
    .map((s) => s.trim())
    .filter((s) => s.length);

  const UNIT = /(?:leds?|lights?|pixels?|px|l)\b/gi;

  for (const entry of entries) {
    // lead-in 12 / offset: 12 / skip 12
    let m = entry.match(/^(?:lead[-\s]?in|lead|offset|skip|start)\s*[:=]?\s*(\d+)/i);
    if (m) {
      if (offset !== null) errors.push('the lead-in is given more than once');
      offset = Number(m[1]);
      continue;
    }

    // 0: 20   0 = 20   level 0: 20
    m = entry.match(/^(?:level|lvl|loop)?\s*(\d+)\s*[:=]\s*(\d+)\s*(?:leds?|lights?|pixels?|px|l)?\s*$/i);
    // level 0 20   lvl 0 20l
    if (!m) m = entry.match(/^(?:level|lvl|loop)\s*(\d+)\s+(\d+)\s*(?:leds?|lights?|pixels?|px|l)?\s*$/i);
    // 0 20l  -- the unit on the second number is what marks it as the count
    if (!m) m = entry.match(/^(\d+)\s+(\d+)\s*(?:leds?|lights?|pixels?|px|l)\s*$/i);
    if (m) {
      const index = Number(m[1]);
      sawIndexed = true;
      if (indexed.has(index)) errors.push('level ' + index + ' is given more than once');
      indexed.set(index, Number(m[2]));
      continue;
    }

    // otherwise: plain counts, in order
    const cleaned = entry.replace(UNIT, ' ').trim();
    if (!/^[\d\s]+$/.test(cleaned)) {
      errors.push('could not read "' + entry + '"');
      continue;
    }
    for (const n of cleaned.match(/\d+/g)) bare.push(Number(n));
  }

  let levels;
  if (sawIndexed) {
    const highest = Math.max(...indexed.keys());
    const size = Math.max(highest + 1, indexed.size + bare.length);
    levels = new Array(size).fill(null);
    for (const [index, count] of indexed) {
      if (index < size) levels[index] = count;
    }
    let next = 0;
    for (const count of bare) {
      while (next < size && levels[next] !== null) next += 1;
      if (next < size) levels[next] = count;
    }
    const missing = [];
    levels.forEach((count, index) => {
      if (count === null) missing.push(index);
    });
    for (const index of missing) errors.push('nothing given for level ' + index);
    levels = levels.map((c) => (c === null ? 0 : c));
    var reported = missing;
  } else {
    levels = bare;
  }

  const alreadyReported = typeof reported === 'undefined' ? [] : reported;
  levels.forEach((count, index) => {
    if (count < 1 && alreadyReported.indexOf(index) === -1) {
      errors.push('level ' + index + ' needs at least one pixel');
    }
  });

  return { levels, offset, errors };
}

/* Sliders in Admin do not apply as you drag them.  Nudging one by accident
 * should not quietly rebuild the tree's geometry, so the new value is shown
 * with an OK / Cancel bar and only applied when you say so. */
function deferredSlider(id, format, buildPatch, onApplied) {
  const input = document.getElementById(id);
  const label = input.closest('label') || input;

  const bar = document.createElement('div');
  bar.className = 'confirm';
  bar.hidden = true;
  const text = document.createElement('span');
  const ok = document.createElement('button');
  ok.className = 'ok';
  ok.textContent = 'OK';
  const cancel = document.createElement('button');
  cancel.textContent = 'Cancel';
  bar.append(text, ok, cancel);
  label.after(bar);

  const control = {
    applied: Number(input.value),
    pending: false,
    set(value) {
      control.applied = Number(value);
      // A background refresh must not wipe a value you are still deciding
      // about: leave the slider and the OK bar alone while one is waiting.
      if (control.pending) return;
      input.value = value;
      control.show(Number(value));
      bar.hidden = true;
    },
    show(value) {
      const out = input.parentElement.querySelector('output');
      if (out) out.textContent = format(value);
    },
  };

  input.addEventListener('input', () => {
    const value = Number(input.value);
    control.show(value);
    if (value === control.applied) {
      control.pending = false;
      bar.hidden = true;
      return;
    }
    control.pending = true;
    text.textContent = 'Apply ' + format(value) + '?';
    bar.hidden = false;
  });

  ok.addEventListener('click', async () => {
    const value = Number(input.value);
    control.pending = false;
    bar.hidden = true;
    try {
      await App.pushConfig(buildPatch(value));
      control.applied = value;
      if (onApplied) onApplied(value);
      App.toast('applied');
    } catch (err) {
      App.toast('could not apply that', true);
      input.value = control.applied;
      control.show(control.applied);
    }
  });

  cancel.addEventListener('click', () => {
    control.pending = false;
    input.value = control.applied;
    control.show(control.applied);
    bar.hidden = true;
  });

  return control;
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

    // --- tree shape (all confirmed before they apply) ----------------
    this.sliders = {
      turns: deferredSlider('turns', (v) => v + ' rotations',
        (v) => ({ turns: v }), () => this.syncFromState()),
      topRatio: deferredSlider('topRatio', (v) => v + '%',
        (v) => ({ top_ratio: v / 100 }), () => this.syncFromState()),
      rotation: deferredSlider('rotation', (v) => v + '%',
        (v) => ({ rotation: v / 100 })),
      brightness: deferredSlider('brightness', (v) => v + '%',
        (v) => ({ brightness: v / 100 })),
      fps: deferredSlider('fps', (v) => v + ' fps', (v) => ({ fps: v })),
      cols: deferredSlider('cols', (v) => v + ' wide',
        (v) => ({ cols: v }), () => this.syncFromState()),
    };

    // --- per-loop pixel counts --------------------------------------
    const moveLoopPixel = async (delta) => {
      // Level counts run bottom first, which is not the same as strip order
      // when the strip starts at the top, so work in levels throughout.
      const counts = Array.from(App.geo.level_counts || App.geo.counts);
      const here = this.levelOf(this.loop);
      const neighbour = here + 1 < counts.length ? here + 1 : here - 1;
      if (neighbour < 0) { App.toast('only one level to share between', true); return; }
      if (counts[here] + delta < 1 || counts[neighbour] - delta < 1) {
        App.toast('a level cannot go below one pixel', true);
        return;
      }
      counts[here] += delta;
      counts[neighbour] -= delta;
      try {
        await App.pushConfig({ loop_counts: counts });
        this.syncFromState();
      } catch (err) {
        App.toast('could not change the level lengths', true);
      }
    };
    document.getElementById('loopMinus').addEventListener('click', () => moveLoopPixel(-1));
    document.getElementById('loopPlus').addEventListener('click', () => moveLoopPixel(1));
    document.getElementById('loopAuto').addEventListener('click', async () => {
      await App.pushConfig({ loop_counts: [] });
      this.syncFromState();
      App.toast('loop lengths back to automatic');
    });

    // --- raw setup ---------------------------------------------------
    const rawText = document.getElementById('rawText');
    const rawLevels = document.getElementById('rawLevels');
    rawText.addEventListener('input', () => this.checkRaw());
    rawLevels.addEventListener('input', () => {
      const wanted = Number(rawLevels.value);
      if (!(wanted >= 1)) return;
      // Resize whatever is in the box to that many levels, so you can type
      // the height first and then fill the levels in, without applying
      // anything until you press Apply.
      const current = parseLevels(rawText.value).levels.filter((c) => c > 0);
      const levels = [];
      for (let k = 0; k < wanted; k++) {
        levels.push(current[k] !== undefined ? current[k]
          : current.length ? current[current.length - 1] : 1);
      }
      rawText.value = this.formatLevels(levels);
      this.checkRaw();
    });

    document.getElementById('rawLoad').addEventListener('click', () => {
      this.loadRaw();
      App.toast('loaded what the tree is set to now');
    });

    document.getElementById('rawEven').addEventListener('click', () => {
      const wanted = Number(rawLevels.value) || App.geo.turns;
      const usable = App.n - (App.geo.offset || 0);
      const each = Math.floor(usable / wanted);
      const levels = new Array(wanted).fill(each);
      let spare = usable - each * wanted;
      for (let k = 0; spare > 0; k++, spare--) levels[k] += 1;
      rawText.value = this.formatLevels(levels);
      this.checkRaw();
    });

    document.getElementById('rawApply').addEventListener('click', () => this.applyRaw());

    const startOffset = document.getElementById('startOffset');
    startOffset.addEventListener('change', async () => {
      try {
        await App.pushConfig({ start_offset: Number(startOffset.value) });
        this.syncFromState();
        App.toast('lead-in set');
      } catch (err) {
        App.toast('could not set the lead-in', true);
      }
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

    // --- games / wifi ------------------------------------------------
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

    if (this.sliders) {
      this.sliders.turns.set(App.geo.turns);
      this.sliders.topRatio.set(Math.round(cfg.top_ratio * 100));
      this.sliders.rotation.set(Math.round(cfg.rotation * 100));
      this.sliders.brightness.set(Math.round(cfg.brightness * 100));
      this.sliders.fps.set(cfg.fps);
      this.sliders.cols.set(cfg.cols);
    }
    document.getElementById('startOffset').value = cfg.start_offset || 0;

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

    // Only refill the raw box when it is empty or already matches, so a
    // background refresh never eats what you are part way through typing.
    const rawText = document.getElementById('rawText');
    if (document.activeElement !== rawText) {
      const live = this.formatLevels(App.geo.level_counts || App.geo.counts);
      const shown = parseLevels(rawText.value).levels.join(',');
      if (!rawText.value.trim() || shown === (App.geo.level_counts || []).join(',')) {
        this.loadRaw();
      } else if (shown !== live) {
        this.checkRaw();
      }
    }
  },

  /* Which level (0 = bottom of the tree) a strip loop sits at. */
  levelOf(loop) {
    const heights = App.geo.heights;
    return Array.from({ length: heights.length }, (_, k) => k)
      .sort((a, b) => heights[a] - heights[b])
      .indexOf(loop);
  },

  /* Write a list of level counts out in the form the box accepts. */
  formatLevels(levels) {
    return levels.map((count, index) => index + ': ' + count).join('\n');
  },

  /* Fill the box from what the tree is actually set to. */
  loadRaw() {
    const levels = App.geo.level_counts || App.geo.counts;
    const offset = App.geo.offset || 0;
    const lines = [];
    if (offset) lines.push('lead-in ' + offset);
    lines.push(this.formatLevels(levels));
    document.getElementById('rawText').value = lines.join('\n');
    document.getElementById('rawLevels').value = levels.length;
    this.checkRaw();
  },

  /* Read the box and say what it would do, without doing it. */
  checkRaw() {
    const status = document.getElementById('rawStatus');
    const text = document.getElementById('rawText').value;
    const parsed = parseLevels(text);
    const offset = parsed.offset === null ? (App.geo.offset || 0) : parsed.offset;
    const lines = [];
    let ok = true;

    if (parsed.errors.length) {
      ok = false;
      lines.push(...parsed.errors);
    }
    if (!parsed.levels.length) {
      ok = false;
      if (!parsed.errors.length) lines.push('nothing to apply yet');
    } else {
      if (parsed.levels.length > 64) {
        ok = false;
        lines.push('64 levels is the most the board will take');
      }
      const total = parsed.levels.reduce((a, b) => a + b, 0);
      const needed = total + offset;
      if (needed > App.n) {
        ok = false;
        lines.push(`that needs ${needed} pixels but the strip has ${App.n}`);
      } else {
        const spare = App.n - needed;
        lines.push(`${parsed.levels.length} levels, ${total} pixels`
          + (offset ? `, ${offset} lead-in` : '')
          + (spare ? `, ${spare} spare at the end (left dark)` : ', the whole strip'));
      }
    }

    // Keep the height field in step with what is actually in the box.
    const levelsField = document.getElementById('rawLevels');
    if (parsed.levels.length && document.activeElement !== levelsField) {
      levelsField.value = parsed.levels.length;
    }

    status.textContent = lines.join('\n');
    status.className = 'rawstatus ' + (ok ? 'good' : 'bad');
    document.getElementById('rawApply').disabled = !ok;
    return ok ? { levels: parsed.levels, offset } : null;
  },

  async applyRaw() {
    const parsed = this.checkRaw();
    if (!parsed) { App.toast('fix the raw setup first', true); return; }
    try {
      await App.pushConfig({
        turns: parsed.levels.length,
        loop_counts: parsed.levels,
        start_offset: parsed.offset,
      });
      this.syncFromState();
      const geo = App.geo;
      const applied = (geo.level_counts || []).join(',');
      if (applied !== parsed.levels.join(',')) {
        App.toast('the board did not take those lengths — check the total', true);
      } else {
        App.toast(geo.turns + ' levels applied');
      }
    } catch (err) {
      App.toast('could not apply the raw setup', true);
    }
  },

  renderLoopMap() {
    const geo = App.geo;
    const order = Array.from({ length: geo.turns }, (_, k) => k)
      .sort((a, b) => geo.heights[a] - geo.heights[b]);
    const biggest = Math.max(...geo.counts);
    document.getElementById('loopMap').innerHTML = order
      .map((loop) => {
        const height = Math.max(4, Math.round((geo.counts[loop] / biggest) * 100));
        const here = loop === this.loop ? ' class="at"' : '';
        return `<span${here} data-loop="${loop}" style="height:${height}%"` +
          ` title="loop ${loop + 1}: ${geo.counts[loop]} pixels"></span>`;
      })
      .join('');
    document.querySelectorAll('#loopMap span').forEach((bar) => {
      bar.addEventListener('click', () => this.gotoLoop(Number(bar.dataset.loop)));
    });

    const count = geo.counts[this.loop];
    const level = this.levelOf(this.loop);
    document.getElementById('loopTuneLabel').textContent =
      `Level ${level} of ${geo.turns - 1} — ${count} pixel${count === 1 ? '' : 's'}`;
    document.getElementById('loopTuneMode').textContent = geo.auto_counts
      ? 'lengths from the cone shape'
      : 'lengths set by hand';
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
    this.renderLoopMap();
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
    const offset = App.geo.offset || 0;
    const parts = [
      state.config.num_pixels + ' pixels'
        + (offset ? ' (' + offset + ' lead-in, ' + App.geo.usable + ' on the tree)' : '')
        + ' in ' + App.geo.turns + ' loops (' + App.geo.counts.join(', ') + ')',
      'lights updating at ' + (state.fps_actual || 0) + ' fps',
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
