/* Regression test: does what the page sends actually reach the strip?
 *
 *     python3 tools/build.py
 *     python3 tools/simulator.py 8099 &
 *     NODE_PATH=/path/to/playwright node tools/test_strip.js
 *
 * The tree preview draws App.frame in the browser, so it looks right no
 * matter what the board does.  That hid a real fault: the game grid was
 * built from every pixel on the strip, and pixels that are not on a loop --
 * the lead-in, and the spare tail past the last level -- default to loop 0,
 * so they piled into the bottom row.  On a 600-LED strip whose levels only
 * covered 52 pixels, the bottom row held 568 pixels of which the board
 * blanked 548 in push().  The page looked fine; the tree did not.
 *
 * These checks compare the two sides directly, in the geometries where they
 * can disagree: a lead-in, a spare tail, or both.  The existing suites all
 * passed because they only ever ran with the whole strip in use.
 */
const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:8099';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
function check(n, ok, d) { if (!ok) fails++; console.log((ok ? 'PASS ' : 'FAIL ') + n + (d ? '  [' + d + ']' : '')); }

const GEOMETRIES = [
  ['whole strip',            { num_pixels: 300, turns: 12, loop_counts: [], start_offset: 0 }],
  ['spare tail',             { num_pixels: 600, turns: 3, loop_counts: [20, 17, 15], start_offset: 0 }],
  ['lead-in',                { num_pixels: 300, turns: 12, loop_counts: [], start_offset: 120 }],
  ['lead-in and spare tail', { num_pixels: 600, turns: 4, loop_counts: [30, 25, 20, 15], start_offset: 40 }],
  ['tiny tree, long strip',  { num_pixels: 1000, turns: 2, loop_counts: [10, 8], start_offset: 5 }],
];

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 400, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(BASE + '/', { waitUntil: 'load' });
  await sleep(900);
  // Admin settings are behind a login. Override with TREE_EMAIL / TREE_PASSWORD
  // if you have changed them on your board.
  const CREDENTIALS = {
    email: process.env.TREE_EMAIL || 'jacobelilevy@gmail.com',
    password: process.env.TREE_PASSWORD || '',
  };
  if (!CREDENTIALS.password) {
    console.log('Set TREE_PASSWORD to your admin password before running this.');
    process.exit(2);
  }
  const signedIn = await page.evaluate(async (creds) => {
    const res = await fetch('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(creds),
    });
    if (res.ok) await App.refresh();
    return res.ok;
  }, CREDENTIALS);
  if (!signedIn) { console.log('Could not sign in; check TREE_PASSWORD.'); process.exit(2); }

  for (const [label, patch] of GEOMETRIES) {
    console.log('\n-- ' + label + ' --');
    await page.evaluate(async (p) => { await App.pushConfig(p); await App.refresh(); }, patch);
    await sleep(600);

    const geo = await page.evaluate(() => ({ n: App.n, offset: App.offset, used: App.used,
                                             rows: App.rows, cols: App.cols }));

    // 1. the game grid must only ever hold pixels that are on the tree
    const cells = await page.evaluate(() => {
      const seen = new Set();
      let empty = 0;
      for (const row of App.cells) for (const cell of row) {
        if (!cell.length) empty += 1;
        for (const i of cell) seen.add(i);
      }
      const outside = [...seen].filter((i) => i < App.offset || i >= App.used);
      return { covered: seen.size, outside, empty };
    });
    check('no cell holds a pixel the board will blank', cells.outside.length === 0,
      cells.outside.length + ' of ' + cells.covered + ' outside [' + geo.offset + ',' + geo.used + ')');
    check('and every cell still has a pixel', cells.empty === 0, cells.empty + ' empty');
    check('the grid covers exactly the tree', cells.covered === geo.used - geo.offset,
      cells.covered + ' of ' + (geo.used - geo.offset));

    // 2. lighting every cell: the strip must show what the page sent
    const lit = await page.evaluate(async () => {
      App.live = true;                       // drive the strip directly
      App.clear();
      for (let r = 0; r < App.rows; r++) {
        for (let c = 0; c < App.cols; c++) App.setCell(r, c, 200, 200, 200);
      }
      let mine = 0;
      for (let i = 0; i < App.n; i++) if (App.frame[i * 3] > 0) mine++;
      await App.send();
      await new Promise((r) => setTimeout(r, 300));
      const d = await (await fetch('/api/sim/frame')).json();
      let board = 0, blanked = 0;
      for (let i = 0; i < App.n; i++) {
        const on = parseInt(d.hex.substr(i * 6, 2), 16) > 0;
        if (on) board++;
        if (App.frame[i * 3] > 0 && !on) blanked++;
      }
      App.live = false;
      return { mine, board, blanked };
    });
    check('every pixel the page lights reaches the strip', lit.blanked === 0,
      'page lit ' + lit.mine + ', strip showed ' + lit.board + ', ' + lit.blanked + ' blanked');

    // 3. and the page is told when most of the strip is not on the tree
    const note = await page.textContent('#stageNote');
    const darkPixels = geo.n - (geo.used - geo.offset);
    if (darkPixels > 0) {
      check('the page says how much of the strip is unused',
        note.includes(String(darkPixels)) && /dark/.test(note), note.slice(0, 80));
    } else {
      check('no warning when the whole strip is in use', !/stay dark/.test(note), note.slice(0, 60));
    }
  }

  // 4. each game, on a geometry with a tail, must not light blanked pixels
  console.log('\n-- every game, on a strip with a spare tail --');
  await page.evaluate(async () => {
    await App.pushConfig({ num_pixels: 600, turns: 4, loop_counts: [30, 25, 20, 15], start_offset: 40 });
    await App.refresh();
  });
  await sleep(600);
  await page.click('#tabs button[data-tab="play"]');
  for (const game of ['snake', 'tetris', 'dodger', 'simon']) {
    await page.click(`#gamePicker button[data-game="${game}"]`);
    await page.click('#gameStart');
    await sleep(900);
    // Watch which pixels the game actually writes, rather than comparing
    // two frames taken a moment apart: a running game moves between the
    // board's snapshot and ours, which would make this flap.
    const strayed = await page.evaluate(async () => {
      const original = App.setPixel;
      let stray = 0;
      let writes = 0;
      App.setPixel = function (i, r, g, b) {
        writes += 1;
        if (i < App.offset || i >= App.used) stray += 1;
        return original.call(this, i, r, g, b);
      };
      await new Promise((r) => setTimeout(r, 1000));
      App.setPixel = original;
      return { stray, writes };
    });
    check(game + ' only ever writes pixels that are on the tree',
      strayed.stray === 0 && strayed.writes > 0,
      strayed.writes + ' writes, ' + strayed.stray + ' outside the tree');
    await page.click('#gameStop');
    await sleep(250);
  }

  // ---- drawing and photos are one square per light ----
  console.log('\n-- the drawing grid --');
  await page.evaluate(async () => {
    await App.pushConfig({ num_pixels: 300, turns: 12, loop_counts: [], start_offset: 0 });
    await App.refresh();
  });
  await sleep(500);
  await page.click('#tabs button[data-tab="draw"]');
  await sleep(500);

  const grid = await page.evaluate(() => {
    const seen = new Set();
    let cells = 0;
    for (const row of Grid.rows) for (const cell of row) { cells += 1; seen.add(cell.index); }
    const outside = [...seen].filter((i) => i < App.offset || i >= App.used);
    const rowWidths = Grid.rows.map((r) => r.length);
    const tapering = rowWidths.every((w, k) => k === 0 || w <= rowWidths[k - 1]);
    return { cells, distinct: seen.size, outside: outside.length, tapering,
             rows: Grid.rows.length, loops: App.rows, onTree: App.used - App.offset };
  });
  check('one square per light, no light twice', grid.cells === grid.distinct,
    grid.cells + ' squares, ' + grid.distinct + ' lights');
  check('and every light on the tree has one', grid.distinct === grid.onTree,
    grid.distinct + ' of ' + grid.onTree);
  check('no square stands for a light off the tree', grid.outside === 0, grid.outside);
  check('a row per loop, narrowing towards the top', grid.rows === grid.loops && grid.tapering,
    grid.rows + ' rows');

  // The point of the whole thing: one touch, one light.  Zoomed in, most of
  // the tree is off screen, so each target is scrolled into view first --
  // which is what a person does too.
  for (const [row, col] of [[0, 0], [3, 5], [6, 11], [11, 4]]) {
    const aim = await page.evaluate(([row, col]) => {
      if (!Grid.rows[row] || col >= Grid.rows[row].length) return null;
      Paint.draw.size = 1;
      Paint.pixels.fill(0);
      const wrap = document.getElementById('paintWrap');
      const canvas = document.getElementById('paint');
      const cell = Grid.cellSize(canvas);
      const ratio = canvas.getBoundingClientRect().width / canvas.width;
      // centre of that square, in the canvas's own CSS pixels
      const cx = (Grid.rowLeft(row, cell, canvas) + (col + 0.5) * cell) * ratio;
      const cy = (canvas.height - (row + 0.5) * cell) * ratio;
      wrap.scrollLeft = cx - wrap.clientWidth / 2;
      wrap.scrollTop = cy - wrap.clientHeight / 2;
      const box = canvas.getBoundingClientRect();
      return { x: box.left + cx, y: box.top + cy, want: Grid.rows[row][col].index };
    }, [row, col]);
    if (!aim) continue;
    await sleep(120);
    await page.mouse.move(aim.x, aim.y);
    await page.mouse.down();
    await page.mouse.up();
    await sleep(140);
    const result = await page.evaluate(() => {
      const on = [];
      for (let i = 0; i < App.n; i++) {
        if (Paint.pixels[i * 3] + Paint.pixels[i * 3 + 1] + Paint.pixels[i * 3 + 2] > 0) on.push(i);
      }
      return on;
    });
    check('one touch lights exactly one light (' + aim.want + ')',
      result.length === 1 && result[0] === aim.want,
      result.length + ' lit: ' + result.slice(0, 5));
  }

  // ---- the squares have to be big enough to actually hit ----
  const view = await page.evaluate(() => {
    const wrap = document.getElementById('paintWrap');
    const canvas = document.getElementById('paint');
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    return {
      square: Math.round(Grid.cellSize(canvas) / ratio),
      window: wrap.clientWidth,
      scrollWidth: wrap.scrollWidth,
      scrollLeft: wrap.scrollLeft,
      readout: document.getElementById('zoomOut').textContent,
    };
  });
  check('opens with squares big enough for a finger', view.square >= 20,
    view.square + ' px a square');
  check('and says how big they are', /px a light/.test(view.readout), view.readout);
  check('zoomed in, the view starts on the front of the tree',
    Math.abs((view.scrollLeft + view.window / 2) - view.scrollWidth / 2) < 16,
    'centre at ' + Math.round(view.scrollLeft + view.window / 2) + ' of ' + view.scrollWidth);

  // a touch has to land on the right light at any zoom, not just the default
  for (const zoom of [100, 250, 600]) {
    await page.evaluate((z) => {
      const slider = document.getElementById('zoomLevel');
      slider.value = z;
      slider.dispatchEvent(new Event('input', { bubbles: true }));
      Paint.draw.size = 1;
      Paint.pixels.fill(0);
    }, zoom);
    await sleep(300);
    // whatever square is under the middle of the visible window
    const aim = await page.evaluate(() => {
      const wrap = document.getElementById('paintWrap');
      const box = wrap.getBoundingClientRect();
      const x = box.left + box.width / 2;
      const y = box.top + Math.min(box.height, wrap.scrollHeight) / 2;
      const spot = Grid.cellAt(document.getElementById('paint'), x, y);
      return { x, y, want: Grid.rows[spot.row][spot.col].index };
    });
    await page.mouse.move(aim.x, aim.y);
    await page.mouse.down();
    await page.mouse.up();
    await sleep(200);
    const hit = await page.evaluate(() => {
      const on = [];
      for (let i = 0; i < App.n; i++) {
        if (Paint.pixels[i * 3] + Paint.pixels[i * 3 + 1] + Paint.pixels[i * 3 + 2] > 0) on.push(i);
      }
      return on;
    });
    check('a touch at ' + zoom + '% lights the one square under it',
      hit.length === 1 && hit[0] === aim.want, hit.length + ' lit, wanted ' + aim.want);
  }

  // the whole tree still fits when asked for
  await page.click('#zoomFit');
  await sleep(300);
  const whole = await page.evaluate(() => {
    const wrap = document.getElementById('paintWrap');
    return { fits: wrap.scrollWidth <= wrap.clientWidth + 2,
             square: Math.round(Grid.cellSize(document.getElementById('paint'))
                                / Math.min(2, window.devicePixelRatio || 1)) };
  });
  check('"Whole tree" fits it all on screen', whole.fits, whole.square + ' px a square');

  // Move mode has to hand the touch back, or the box cannot be scrolled
  await page.click('#paintMode button[data-mode="move"]');
  await sleep(200);
  const moving = await page.evaluate(() => ({
    touch: getComputedStyle(document.getElementById('paint')).touchAction,
    marked: document.getElementById('paintWrap').classList.contains('move'),
  }));
  check('Move hands the touch back to the browser so it can scroll',
    moving.marked && moving.touch !== 'none', JSON.stringify(moving));

  const movedPixels = await page.evaluate(async () => {
    const before = Paint.pixels.slice();
    const wrap = document.getElementById('paintWrap');
    const box = wrap.getBoundingClientRect();
    return { x: box.left + box.width / 2, y: box.top + box.height / 2,
             sum: before.reduce((a, b) => a + b, 0) };
  });
  await page.mouse.move(movedPixels.x, movedPixels.y);
  await page.mouse.down();
  await page.mouse.move(movedPixels.x - 40, movedPixels.y);
  await page.mouse.up();
  await sleep(200);
  check('and dragging in Move mode does not draw',
    await page.evaluate((was) => Paint.pixels.reduce((a, b) => a + b, 0) === was,
      movedPixels.sum), '');
  await page.click('#paintMode button[data-mode="draw"]');
  await sleep(200);

  // put a stroke back for the checks below
  await page.evaluate(() => {
    Paint.pixels.fill(0);
    for (const cell of Grid.rows[2]) {
      const at = cell.index * 3;
      Paint.pixels[at] = 200; Paint.pixels[at + 1] = 40; Paint.pixels[at + 2] = 90;
    }
    Grid.render(document.getElementById('paint'), Paint.pixels);
    Paint.startDraw();
    App.dirty = true;
  });
  await sleep(500);

  // what is drawn is what is sent
  await sleep(500);
  const drawn = await page.evaluate(async () => {
    const d = await (await fetch('/api/sim/frame')).json();
    let differs = 0;
    for (let i = 0; i < App.n * 3; i++) {
      if (parseInt(d.hex.substr(i * 2, 2), 16) !== Paint.pixels[i]) differs++;
    }
    return differs;
  });
  check('the strip shows the drawing byte for byte', drawn === 0, drawn + ' bytes differ');

  // ---- the photo preview is the lights themselves ----
  console.log('\n-- the photo grid --');
  await page.click('#tabs button[data-tab="photo"]');
  await sleep(400);
  await page.evaluate(async () => {
    const c = document.createElement('canvas');
    c.width = 120; c.height = 160;
    const x = c.getContext('2d');
    const g = x.createLinearGradient(0, 0, 0, 160);
    g.addColorStop(0, '#ffd24a'); g.addColorStop(1, '#2b5cff');
    x.fillStyle = g; x.fillRect(0, 0, 120, 160);
    x.fillStyle = '#fff'; x.beginPath(); x.arc(60, 60, 25, 0, 7); x.fill();
    const img = new Image();
    await new Promise((r) => { img.onload = r; img.src = c.toDataURL(); });
    Paint.photo.image = img;
    Paint.renderSource();
    Paint.samplePhoto();
    Paint.startPhoto();
  });
  await sleep(900);
  const photo = await page.evaluate(async () => {
    const d = await (await fetch('/api/sim/frame')).json();
    let differs = 0, lit = 0;
    for (let i = 0; i < App.n; i++) {
      for (let k = 0; k < 3; k++) {
        if (parseInt(d.hex.substr(i * 6 + k * 2, 2), 16) !== Paint.photoPixels[i * 3 + k]) differs++;
      }
      if (Paint.photoPixels[i * 3] + Paint.photoPixels[i * 3 + 1] + Paint.photoPixels[i * 3 + 2] > 0) lit++;
    }
    return { differs, lit, samples: Paint.photoPixels.length / 3 };
  });
  check('the picture is reduced to one colour per light', photo.lit > 0,
    photo.lit + ' of ' + photo.samples + ' lit');
  check('and the strip shows exactly that', photo.differs === 0, photo.differs + ' bytes differ');
  await page.click('#photoStop');
  await sleep(300);

  check('no javascript errors', errors.length === 0, errors.slice(0, 3).join(' | '));
  await browser.close();
  console.log(fails ? '\n' + fails + ' FAILURES' : '\nall strip checks passed');
  process.exit(fails ? 1 : 0);
})();
