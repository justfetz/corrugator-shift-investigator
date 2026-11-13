// Records docs/img/ask-and-answer.gif, the README's picture (issue #145): the local workbench answering one question
// on synthetic data, then the receipt beside the headline figure opening the exact SQL behind it.
//
//   python -m investigator.server --port 7860 &
//   node docs/img/record_ask_and_answer.cjs [http://127.0.0.1:7860]
//
// Needs Playwright (headless Chromium) and ffmpeg. Frames are screenshots at 1280 x 760, each held for its own time,
// and ffmpeg turns them into one GIF with a single shared palette and no dithering, so the text stays sharp.
// Not run in CI; re-record only when the page changes enough that the picture misleads.
const { chromium } = require('playwright');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');

const base = process.argv[2] || 'http://127.0.0.1:7860';
const out = path.join(__dirname, 'ask-and-answer.gif');
const day = '2026-09-09';   // Wednesday of the planted missed-splice week; shift 2 is the page's default
const question = 'Which downtime reason cost the most minutes this week?';

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ask-and-answer-'));
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 760 }, deviceScaleFactor: 1 });
  const frames = [];
  const shot = async ms => {
    const file = path.join(dir, `f${String(frames.length).padStart(3, '0')}.png`);
    await page.screenshot({ path: file });
    frames.push([path.basename(file), ms]);
  };
  await page.goto(base + '/', { waitUntil: 'networkidle' });
  await page.fill('#day', day);
  await page.dispatchEvent('#day', 'change');
  await page.evaluate(() => document.activeElement && document.activeElement.blur());   // no selected text in the first frame
  await page.waitForTimeout(1200);
  await shot(1400);
  await page.click('#question');
  for (let i = 0; i < question.length; i += 4) {
    await page.type('#question', question.slice(i, i + 4), { delay: 0 });
    await shot(90);
  }
  await shot(600);
  await page.keyboard.press('Enter');
  for (let i = 0; i < 4; i++) { await page.waitForTimeout(150); await shot(150); }
  await page.waitForTimeout(800);
  await shot(3200);
  // the receipt beside the headline figure opens the query box at that query, with its SQL
  const receipt = page.locator('#messages a.ref', { hasText: 'row 0' }).first();
  await receipt.hover(); await shot(500);
  await receipt.click();
  await page.waitForTimeout(500);
  await page.evaluate(() => [...document.querySelectorAll('#messages .queries, .queries')].pop().scrollIntoView({ block: 'start' }));
  await page.waitForTimeout(500);
  await shot(4500);
  await browser.close();
  const list = frames.map(([f, ms]) => `file '${f}'\nduration ${(ms / 1000).toFixed(3)}\n`).join('') + `file '${frames[frames.length - 1][0]}'\n`;
  fs.writeFileSync(path.join(dir, 'list.txt'), list);
  execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', 'list.txt', '-vf',
    'split[a][b];[a]palettegen=max_colors=192:stats_mode=full[p];[b][p]paletteuse=dither=none', '-vsync', 'vfr', out],
    { cwd: dir, stdio: 'inherit' });
  fs.rmSync(dir, { recursive: true, force: true });
  console.log(`${out}: ${frames.length} frames, ${(fs.statSync(out).size / 1024).toFixed(0)} KB`);
})().catch(error => { console.error(error); process.exit(1); });
