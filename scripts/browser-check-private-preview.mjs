import { createServer } from 'node:http';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const root = path.join(appRoot, 'out');
const port = 3198;
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.gz': 'application/gzip', '.txt': 'text/plain; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png' };

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url ?? '/', `http://127.0.0.1:${port}`);
    const decoded = decodeURIComponent(url.pathname);
    let target = path.join(root, decoded);
    const stat = await fs.stat(target).catch(() => null);
    if (stat?.isDirectory()) target = path.join(target, 'index.html');
    if (!path.resolve(target).startsWith(`${path.resolve(root)}${path.sep}`)) throw new Error('path escape');
    const bytes = await fs.readFile(target);
    response.writeHead(200, { 'content-type': mime[path.extname(target)] ?? 'application/octet-stream', 'x-robots-tag': 'noindex, nofollow, noarchive' });
    response.end(bytes);
  } catch {
    response.writeHead(404, { 'content-type': 'text/plain' });
    response.end('not found');
  }
});
let browser;

try {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(10000);
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  const dataRequests = [];
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.includes('/_preview-data/') || pathname.includes('/_local-data/')) dataRequests.push(pathname);
  });
  await page.goto(`http://127.0.0.1:${port}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.maplibregl-canvas');
  await page.waitForFunction(() => document.body.textContent?.includes('Active private preview layers'));
  if ((await page.locator('body').innerText()).trim().length < 100) throw new Error('private preview rendered an empty shell');
  if (await page.locator('[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay').count()) throw new Error('framework error overlay is visible');
  const robots = await page.locator('meta[name="robots"]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('content')));
  if (!robots.includes('noindex,nofollow,noarchive')) throw new Error(`private preview noindex meta drift: ${robots.join(',')}`);

  await page.locator('.layer-instrument summary').click();
  const fourRegion = page.getByRole('checkbox', { name: 'Toggle Four-region freeway model' });
  const tarzana = page.getByRole('checkbox', { name: 'Toggle Tarzana mixed-road scenario' });
  if (!(await fourRegion.isDisabled()) || !(await tarzana.isDisabled())) throw new Error('rights-blocked modeled payload became selectable');
  if (!(await page.getByText('Not uploaded · source/output rights are not externally admitted.').first().isVisible())) throw new Error('modeled derivative rights disclosure missing');
  const airport = page.getByRole('checkbox', { name: 'Toggle Airport planning contours' });
  if (!(await airport.isDisabled())) throw new Error('unresolved airport payload became selectable');
  const source341 = page.getByRole('checkbox', { name: 'Toggle Source-341 incomplete mask' });
  if (!(await source341.isDisabled())) throw new Error('private Source-341 evidence became selectable');
  const rail = page.getByTestId('rail-context-row');
  if (!(await rail.textContent())?.includes('Not uploaded · Metro provider terms unresolved.')) throw new Error('Metro exclusion disclosure missing');
  if (!(await page.getByText('Not uploaded · official-record redistribution terms unresolved.').isVisible())) throw new Error('airport exclusion disclosure missing');
  if (!(await page.getByText(/Not uploaded · private review evidence excluded/).isVisible())) throw new Error('Source-341 exclusion disclosure missing');
  const combination = page.locator('.layer-row--disabled').filter({ hasText: 'Compatible acoustic sum — not admitted' }).locator('input');
  if (!(await combination.isDisabled())) throw new Error('acoustic combination became selectable');
  await page.locator('.layer-instrument summary').click();
  if (!(await page.getByRole('radio', { name: 'Evening' }).isDisabled())) throw new Error('rights-blocked Tarzana period control became interactive');
  if (!(await page.locator('.layer-style-options button').filter({ hasText: 'Glow' }).isDisabled())) throw new Error('empty preview display treatment became interactive');

  await page.getByRole('radio', { name: 'CONTEXT' }).click();
  if (!(await page.getByText('Preview payloads unavailable', { exact: true }).isVisible())) throw new Error('empty preview legend is missing');
  if (!(await page.locator('.legend-instrument').getByText(/Source-341 remains incomplete — not computable/).isVisible())) throw new Error('Source-341 incomplete warning is missing');
  await page.getByRole('radio', { name: 'ALL DATA' }).click();
  await page.selectOption('#region-preset', 'tarzana');
  await page.getByRole('button', { name: 'Fit visible' }).click();
  await page.getByRole('button', { name: 'Zoom in' }).click();
  await page.getByRole('button', { name: 'Zoom out' }).click();
  await page.waitForFunction(() => {
    const map = window.__quietMap;
    return Boolean(map?.isStyleLoaded() && map.getLayer('quiet-four-points') && map.getLayer('quiet-tarzana-points') && map.getSource('quiet-airports') && map.getSource('quiet-receivers'));
  });
  const excludedSources = await page.evaluate(() => ({ airportFeatures: window.__quietMap?.querySourceFeatures('quiet-airports').length ?? -1, source341Present: Boolean(window.__quietMap?.getSource('quiet-mask')) }));
  if (excludedSources.airportFeatures !== 0 || excludedSources.source341Present) throw new Error(`excluded map sources became active: ${JSON.stringify(excludedSources)}`);
  if (!(await page.getByText('Scientific private preview is not yet admitted', { exact: true }).isVisible())) throw new Error('empty-shell map status disclosure is missing');
  const renderedModeled = await page.evaluate(() => window.__quietMap?.queryRenderedFeatures({ layers: ['quiet-four-points', 'quiet-tarzana-points'] }).length ?? 0);
  if (renderedModeled !== 0) throw new Error(`rights-blocked private preview rendered ${renderedModeled} modeled features`);

  const uniqueRequests = [...new Set(dataRequests)].sort();
  const expected = [];
  if (JSON.stringify(uniqueRequests) !== JSON.stringify(expected)) throw new Error(`private preview requested an unallowlisted payload: ${uniqueRequests.join(', ')}`);
  const visibleCopy = await page.locator('body').innerText();
  if (/stage:local|npm run|\/_local-data\//i.test(visibleCopy)) throw new Error('local developer recovery text leaked into private preview');
  if (pageErrors.length) throw new Error(`browser page errors: ${pageErrors.join(' | ')}`);
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(appRoot, 'release/browser-private-preview.png'), fullPage: true });
  await browser.close();
  browser = undefined;
  console.log(JSON.stringify({ status: 'PRIVATE_PREVIEW_BROWSER_PASS', payloadRequests: uniqueRequests, renderedModeled, checks: ['portal-shell', 'interactive-empty-map', 'zero-scientific-payload-requests', 'noindex', 'rights-and-terms-blocked-layers-disabled', 'source341-evidence-excluded', 'combination-disabled'] }, null, 2));
} catch (error) {
  console.error(`browser-check-private-preview: ${error.message}`);
  process.exitCode = 1;
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
