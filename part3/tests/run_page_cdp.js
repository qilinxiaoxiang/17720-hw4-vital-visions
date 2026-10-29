// Opens a page in headless Chrome over the DevTools protocol (pipe transport,
// no extra packages), waits until document.body.dataset.selftest is set, and
// prints {selftest, error, pre, consoleErrors} as JSON.
// Usage: node run_page_cdp.js <url> [timeoutMs] [driverJs]
// (--dump-dom cannot be used for the live test: it does not drive
// requestAnimationFrame at a real frame rate.)
'use strict';
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const url = process.argv[2];
const timeoutMs = Number(process.argv[3] || 30000);
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'hilight-cdp-'));

const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-pipe', `--user-data-dir=${profile}`, 'about:blank'],
  { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });
const toChrome = chrome.stdio[3];
const fromChrome = chrome.stdio[4];

let nextId = 1;
const pending = new Map();
const consoleErrors = [];
let buf = '';
fromChrome.on('data', (d) => {
  buf += d.toString('utf8');
  let i;
  while ((i = buf.indexOf('\0')) >= 0) {
    const msg = JSON.parse(buf.slice(0, i));
    buf = buf.slice(i + 1);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error))); else resolve(msg.result);
    } else if (msg.method === 'Runtime.exceptionThrown') {
      consoleErrors.push(msg.params.exceptionDetails.text + ' ' +
        ((msg.params.exceptionDetails.exception || {}).description || ''));
    } else if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      consoleErrors.push(msg.params.args.map((a) => a.value || a.description).join(' '));
    }
  }
});

function send(method, params, sessionId) {
  const id = nextId++;
  const m = { id, method, params: params || {} };
  if (sessionId) m.sessionId = sessionId;
  toChrome.write(JSON.stringify(m) + '\0');
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

function finish(out, code) {
  process.stdout.write(JSON.stringify(out));
  send('Browser.close').catch(() => {});
  setTimeout(() => {
    try { chrome.kill('SIGKILL'); } catch (e) { /* already gone */ }
    fs.rmSync(profile, { recursive: true, force: true });
    process.exit(code);
  }, 300);
}

(async () => {
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  await send('Runtime.enable', {}, sessionId);
  await send('Page.enable', {}, sessionId);
  await send('Page.navigate', { url }, sessionId);
  const t0 = Date.now();
  const ev = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true }, sessionId)).result.value;
  const driver = process.argv[4];
  if (driver) {
    // optional page-side script that clicks/presses keys like a user would
    while (await ev('document.readyState') !== 'complete') await new Promise((r) => setTimeout(r, 100));
    await ev(driver);
  }
  while (Date.now() - t0 < timeoutMs) {
    const st = await ev('document.body && document.body.dataset.selftest || ""');
    if (st) {
      finish({ selftest: st, error: await ev('document.body.dataset.error || null'),
        pre: await ev('document.getElementById("selftest").textContent'), consoleErrors }, 0);
      return;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  finish({ selftest: null, error: await ev('document.body && document.body.dataset.error || null'),
    pre: null, consoleErrors, timeout: true }, 0);
})().catch((e) => finish({ fatal: String(e), consoleErrors }, 1));
