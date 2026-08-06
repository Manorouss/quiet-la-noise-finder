import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const port = 3199;
const server = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'dev', '-H', '127.0.0.1', '-p', String(port)], {
  cwd: appRoot,
  env: {
    ...process.env,
    NEXT_PUBLIC_QUIET_LA_DATA_PROFILE: 'external_payload_free_v1',
    NEXT_PUBLIC_QUIET_LA_DATA_ROOT: '',
    NEXT_PUBLIC_QUIET_LA_LOCAL_RECOVERY_COMMAND: '',
  },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let output = '';
server.stdout.on('data', (chunk) => { output += chunk.toString(); });
server.stderr.on('data', (chunk) => { output += chunk.toString(); });

async function waitForServer() {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try { if ((await fetch(`http://127.0.0.1:${port}`)).ok) return; } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`external profile server did not start: ${output}`);
}

let browser;
try {
  await waitForServer();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const payloadRequests = [];
  const pageErrors = [];
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.includes('/_local-data/') || pathname.includes('/_preview-data/')) payloadRequests.push(pathname);
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.goto(`http://127.0.0.1:${port}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.maplibregl-canvas');
  await page.getByText('Scientific private preview is not published on this URL', { exact: true }).first().waitFor({ state: 'visible' });
  const body = await page.locator('body').innerText();
  if (/stage:local|stage:private-preview|npm run|local filesystem|Map awaiting local v3 data/i.test(body)) throw new Error('developer recovery copy leaked into payload-free external UI');
  if (!body.includes('Payload-free public shell')) throw new Error('payload-free external badge is missing');
  if (payloadRequests.length) throw new Error(`payload-free external profile requested scientific data: ${[...new Set(payloadRequests)].join(', ')}`);

  await page.locator('.layer-instrument summary').click();
  const mapped = page.locator('.layer-rows label.layer-row input[type="checkbox"]');
  if (await mapped.count() !== 4) throw new Error(`external layer row count drift: ${await mapped.count()}`);
  for (let index = 0; index < await mapped.count(); index += 1) {
    const checkbox = mapped.nth(index);
    if (!await checkbox.isDisabled() || await checkbox.isChecked()) throw new Error(`external mapped layer ${index} is active/selectable`);
  }
  if (!(await page.getByTestId('rail-context-row').textContent())?.includes('Unavailable on this payload-free URL.')) throw new Error('external non-spatial context row is misleading');
  if (!(await page.getByRole('radio', { name: 'Evening' }).isDisabled())) throw new Error('external period control is active');
  await page.locator('.layer-instrument summary').click();
  if (!body.includes('0 mapped source families + 0 non-spatial context record · 0 receiver records')) throw new Error('external active data count is misleading');
  if (await page.locator('.inspect-card').count()) throw new Error('external shell has a misleading inspection selection');
  await page.getByRole('radio', { name: 'CONTEXT' }).click();
  await page.getByRole('radio', { name: 'ALL DATA' }).click();
  await page.getByRole('button', { name: 'Zoom in' }).click();
  await page.getByRole('button', { name: 'Zoom out' }).click();
  if (pageErrors.length) throw new Error(`external browser errors: ${pageErrors.join(' | ')}`);
  await page.screenshot({ path: path.join(appRoot, 'release/browser-external-payload-free.png'), fullPage: true });
  console.log(JSON.stringify({ status: 'EXTERNAL_PAYLOAD_FREE_BROWSER_PASS', payloadRequests: [], mappedLayersEnabled: 0, receiverRecords: 0, screenshot: 'release/browser-external-payload-free.png' }, null, 2));
} catch (error) {
  console.error(`browser-check-external: ${error.message}`);
  process.exitCode = 1;
} finally {
  await browser?.close();
  server.kill('SIGTERM');
  await fs.writeFile(path.join(appRoot, 'release/browser-check-external-last.log'), output);
}
