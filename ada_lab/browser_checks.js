/* Trusted real-browser checks. The app runs in a different restricted process. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const config = JSON.parse(fs.readFileSync('browser.json', 'utf8'));
const temporary = path.join(process.cwd(), '.runtime', 'tmp');
fs.mkdirSync(temporary, { recursive: true });
process.env.TEMP = process.env.TMP = process.env.TMPDIR = temporary;
delete process.env.SSLKEYLOGFILE;
const { chromium } = require(config.library);
setTimeout(() => process.exit(124), 95000).unref();

const input = readline.createInterface({ input: process.stdin });
let pending = [], chain = Promise.resolve();
input.on('line', line => {
  const waiter = pending.shift();
  if (!waiter) return;
  try { waiter.resolve(JSON.parse(line)); } catch (error) { waiter.reject(error); }
});
input.on('close', () => { for (const waiter of pending) waiter.reject(Error('Controller disconnected')); });
function request(method, target, body = Buffer.alloc(0), headers = {}) {
  const exchange = () => new Promise((resolve, reject) => {
    pending.push({ resolve, reject });
    process.stdout.write(JSON.stringify({ kind: 'request', request: {
      method, path: target, body: Buffer.from(body).toString('base64'), headers
    } }) + '\n');
  });
  const result = chain.then(exchange);
  chain = result.catch(() => {});
  return result;
}
async function records() {
  const response = await request('GET', '/api/repairs');
  if (response.status !== 200) throw Error('Could not read backend state');
  return JSON.parse(Buffer.from(response.body, 'base64').toString('utf8'));
}
function requireThat(condition, message) { if (!condition) throw Error(message); }
async function until(predicate, message) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  throw Error(message);
}
const result = { criteria: {} };
async function check(name, action) {
  const start = Date.now();
  try { await action(); result.criteria[name] = { status: 'PASS', detail: 'Observed required browser behavior' }; }
  catch (error) { result.criteria[name] = { status: 'FAIL', detail: String(error) }; }
  result.criteria[name].wall_seconds = (Date.now() - start) / 1000;
  fs.writeFileSync('progress.json', JSON.stringify(result));
}

(async () => {
  let browser;
  try {
    browser = await chromium.launch({ executablePath: config.executable, headless: true,
      chromiumSandbox: false, timeout: 12000, env: { ...process.env },
      args: ['--disable-gpu', '--disable-crash-reporter', '--disable-breakpad', '--noerrdialogs', '--disable-features=RendererCodeIntegrity'] });
    result.browser_version = browser.version();
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: 'block', acceptDownloads: false });
    await context.route('**/*', async route => {
      try {
      const parsed = new URL(route.request().url());
      if (parsed.origin !== 'http://repair.local') return route.abort();
      const response = await request(route.request().method(), parsed.pathname + parsed.search,
        route.request().postDataBuffer() || Buffer.alloc(0), route.request().headers());
      const headers = Object.fromEntries(Object.entries(response.headers).filter(([key]) => !['content-length', 'transfer-encoding', 'connection'].includes(key.toLowerCase())));
      await route.fulfill({ status: response.status, headers, body: Buffer.from(response.body, 'base64') });
      } catch (error) {
        if (!String(error).includes('already handled') && !String(error).includes('Target page, context or browser has been closed'))
          result.error = 'Transport error: ' + String(error);
      }
    });
    const page = await context.newPage();
    page.setDefaultTimeout(5000);
    const field = name => page.getByTestId(name);
    const rows = () => field('repair-row');
    const goto = () => page.goto('http://repair.local/', { waitUntil: 'domcontentloaded' });
    async function fill(data) {
      for (const [name, value] of Object.entries(data)) await field(name).fill(value);
    }
    await check('ui_crud', async () => {
      await goto();
      await fill({ customer: 'Browser <b>Customer</b>', device: 'Laptop', issue: 'Keyboard failure' });
      await field('priority').selectOption('high'); await field('save').click();
      const row = rows().filter({ hasText: 'Browser <b>Customer</b>' });
      await until(async () => await row.count() === 1, 'Browser create did not appear');
      requireThat(await row.locator('b').count() === 0, 'User content must be displayed as text');
      await row.getByTestId('edit').click();
      await fill({ customer: 'Browser Edited', device: 'Tablet', issue: 'Screen replaced' });
      await field('status').selectOption('done'); await field('priority').selectOption('low'); await field('save').click();
      await until(async () => await rows().filter({ hasText: 'Browser Edited' }).count() === 1, 'Browser edit did not appear');
      const saved = (await records()).filter(record => record.customer === 'Browser Edited');
      requireThat(saved.length === 1 && saved[0].status === 'done' && saved[0].priority === 'low' && saved[0].device === 'Tablet' && saved[0].issue === 'Screen replaced', 'All UI edits must persist on the backend');
      await page.reload();
      await until(async () => await rows().filter({ hasText: 'Browser Edited' }).count() === 1, 'Reload lost browser record');
    });
    await check('ui_filter', async () => {
      await goto(); await field('search').fill('BROWSER');
      await field('status-filter').selectOption('done'); await field('priority-filter').selectOption('low');
      await until(async () => await rows().count() === 1 && (await rows().innerText()).includes('Browser Edited'), 'Combined UI filters did not intersect');
      await field('status-filter').selectOption('new');
      await until(async () => await rows().count() === 0, 'UI filter should return no rows');
    });
    await check('ui_error', async () => {
      await goto(); await fill({ customer: 'Keep my input', device: 'Phone', issue: 'Network failure' });
      const fail = route => ['POST', 'PUT'].includes(route.request().method()) ? route.abort('connectionfailed') : route.fallback();
      await page.route('**/api/repairs*', fail); await field('save').click();
      await until(async () => await field('error').isVisible() && (await field('error').innerText()).length > 0, 'Failed save needs visible error');
      requireThat(await field('customer').inputValue() === 'Keep my input' && await field('issue').inputValue() === 'Network failure', 'Failed save lost input');
      await page.unroute('**/api/repairs*', fail); await field('save').click();
      await until(async () => await rows().filter({ hasText: 'Keep my input' }).count() === 1, 'Save retry did not succeed');
    });
    await check('responsive', async () => {
      await page.setViewportSize({ width: 390, height: 844 }); await goto();
      requireThat(await field('customer').isVisible(), 'Mobile form must be visible');
      requireThat(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Horizontal overflow at 390px');
      for (const name of ['customer', 'device', 'issue', 'status', 'priority', 'search', 'status-filter', 'priority-filter'])
        requireThat(await field(name).evaluate(element => !!(element.labels?.length || element.getAttribute('aria-label') || element.getAttribute('aria-labelledby'))), 'Missing label: ' + name);
      await page.screenshot({ path: path.join(process.cwd(), 'mobile.png'), fullPage: true });
    });
    await check('ui_states', async () => {
      const held = [], hold = route => { held.push(route); };
      await page.route('**/api/repairs*', hold); await goto();
      await until(async () => await field('list-state').getAttribute('data-state') === 'loading', 'Loading state missing');
      for (const route of held) await route.fallback();
      await page.unroute('**/api/repairs*', hold);
      await field('search').fill('unmatched-813568');
      await until(async () => await rows().count() === 0 && await field('list-state').getAttribute('data-state') === 'empty', 'Empty state missing');
      const failure = route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"Unavailable"}' });
      await page.route('**/api/repairs*', failure); await page.reload({ waitUntil: 'domcontentloaded' });
      await until(async () => await field('list-state').getAttribute('data-state') === 'error' && (await field('list-state').innerText()).length > 0, 'List-load error state missing');
      await page.unroute('**/api/repairs*', failure);
    });
    await check('ui_validation', async () => {
      await goto(); const before = await records();
      await fill({ customer: '   ', device: 'Keep device', issue: 'Keep issue' }); await field('save').click();
      await until(async () => await field('error').isVisible() && (await field('error').innerText()).length > 0, 'Invalid save needs visible error');
      requireThat(await field('device').inputValue() === 'Keep device', 'Invalid save lost other inputs');
      requireThat(JSON.stringify(await records()) === JSON.stringify(before), 'Invalid UI save mutated records');
    });
    await context.close();
  } catch (error) { result.error = String(error); }
  finally { if (browser) await browser.close(); }
  process.stdout.write(JSON.stringify({ kind: 'result', result }) + '\n');
  input.close();
})().catch(error => { process.stdout.write(JSON.stringify({ kind: 'result', result: { error: String(error) } }) + '\n'); input.close(); process.exitCode = 1; });
