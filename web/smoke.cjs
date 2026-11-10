// Smoke test of the built front end in headless Chromium:
//   python -m hoopsim web --no-open --port 8123 &
//   node web/smoke.cjs http://localhost:8123/ /tmp/shots
// Starts a new game, sims a few days, opens every screen and the player card,
// and fails on any console error. Needs Node's `playwright` package.
const { chromium } = require('playwright');
const SHOTS = (process.argv[3] || '.') + '/';
const URL = process.argv[2] || 'http://localhost:8123/';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1366, height: 860 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') errors.push(m.type() + ': ' + m.text()); });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  await page.goto(URL);
  await page.waitForSelector('.start, .app');
  await page.getByRole('button', { name: 'Create league' }).click();
  await page.getByText('Choose your team').waitFor();
  await page.screenshot({ path: SHOTS + '01-choose.png' });
  await page.getByRole('button', { name: 'Manage' }).first().click();
  await page.getByText('Inbox').first().waitFor();
  for (let i = 0; i < 3; i++) { await page.getByRole('button', { name: 'Next day' }).click(); await page.waitForTimeout(400); }
  await page.getByRole('button', { name: 'Week' }).click(); await page.waitForTimeout(1500);
  await page.screenshot({ path: SHOTS + '02-home.png' });
  for (const s of ['squad', 'tactics', 'transfers', 'league', 'offseason']) {
    await page.goto(URL + '#/' + s); await page.waitForTimeout(900);
    await page.screenshot({ path: SHOTS + `03-${s}.png`, fullPage: false });
  }
  await page.goto(URL + '#/squad'); await page.waitForTimeout(600);
  await page.locator('table .link').first().click(); await page.waitForTimeout(800);
  await page.screenshot({ path: SHOTS + '04-card.png' });
  await page.keyboard.press('Escape');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto(URL + '#/league'); await page.waitForTimeout(900);
  await page.screenshot({ path: SHOTS + '10-league-dark.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto(URL + '#/home'); await page.waitForTimeout(900);
  await page.screenshot({ path: SHOTS + '11-phone-home.png' });
  await page.goto(URL + '#/tactics'); await page.waitForTimeout(900);
  await page.screenshot({ path: SHOTS + '12-phone-tactics.png' });
  console.log(errors.join('\n') || 'no console errors');
  await browser.close();
  if (errors.length) process.exit(2);
})().catch(e => { console.error(e); process.exit(1); });
