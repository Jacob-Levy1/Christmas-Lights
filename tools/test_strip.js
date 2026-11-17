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

  await page.evaluate(async () => {
    await App.pushConfig({ num_pixels: 300, turns: 12, loop_counts: [], start_offset: 0 });
  });
  check('no javascript errors', errors.length === 0, errors.slice(0, 3).join(' | '));
  await browser.close();
  console.log(fails ? '\n' + fails + ' FAILURES' : '\nall strip checks passed');
  process.exit(fails ? 1 : 0);
})();
