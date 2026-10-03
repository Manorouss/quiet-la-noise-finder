import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { gunzipSync } from 'node:zlib';
import { chromium, webkit } from '@playwright/test';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const port = 3197;
const engine = process.env.QUIET_LA_BROWSER_ENGINE === 'webkit' ? 'webkit' : 'chromium';
const base = process.env.QUIET_LA_BROWSER_URL || `http://127.0.0.1:${port}`;
const server = process.env.QUIET_LA_BROWSER_URL ? null : spawn(process.execPath, ['scripts/run-local-next.mjs', 'dev', '-H', '127.0.0.1', '-p', String(port)], { cwd: appRoot, stdio: ['ignore', 'pipe', 'pipe'] });
let serverOutput = '';
server?.stdout.on('data', (chunk) => { serverOutput += chunk; });
server?.stderr.on('data', (chunk) => { serverOutput += chunk; });
let browser;
let activePage;
const failures = [];
const requests = [];
async function wait(page, label, predicate, arg) {
  try { await page.waitForFunction(predicate, arg, { timeout: 20000 }); }
  catch (error) { throw new Error(`${label}: ${error.message}`); }
}
async function loaded(page) {
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: 'Explore Tarzana' }).waitFor();
  await wait(page, 'dense field ready', () => Boolean(window.__quietMap?.getLayer('dense-field') && window.__quietMap?.isSourceLoaded('dense-field')));
  await wait(page, 'initial camera settled', () => !window.__quietMap.isMoving());
}
try {
  for (let n = 0; n < 100; n++) {
    try { if ((await fetch(base, { signal: AbortSignal.timeout(5000) })).ok) break; } catch {}
    if (n === 99) throw new Error(`Local server did not start: ${serverOutput}`);
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  browser = await (engine === 'webkit' ? webkit : chromium).launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  activePage = page;
  page.on('pageerror', (error) => failures.push(error.message));
  page.on('response', (response) => { if (response.url().includes('/_local-data/') && response.status() >= 400) failures.push(`Scientific asset ${response.status()}: ${response.url()}`); });
  page.on('request', (request) => { const pathname = new URL(request.url()).pathname; if (pathname.includes('_local-data') || pathname.includes('_preview-data')) requests.push(pathname); });
  await loaded(page);
  if (!(await page.locator('meta[name="robots"]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('content')))).includes('noindex,nofollow,noarchive')) throw new Error('noindex contract drift');
  for (const style of ['field', 'bands', 'glow', 'dots']) {
    await page.getByRole('radio', { name: style[0].toUpperCase() + style.slice(1), exact: true }).click();
    await wait(page, `${style} exclusive rendering`, (active) => ['field', 'bands', 'glow', 'dots'].every((id) => window.__quietMap.getLayoutProperty(`dense-${id}`, 'visibility') === (id === active ? 'visible' : 'none')), style);
  }
  await page.getByRole('radio', { name: 'Field', exact: true }).click();
  await page.locator('.advanced-controls > summary').click();
  const contextCounts = { 'LA County fire stations': 185, 'City fire stations': 106, Heliports: 199, 'Airport noise contours': 35 };
  for (const [label, count] of Object.entries(contextCounts)) {
    await page.getByRole('checkbox', { name: label, exact: true }).check();
    const source = { 'LA County fire stations': 'context-county-fire', 'City fire stations': 'context-city-fire', Heliports: 'context-heliports', 'Airport noise contours': 'context-airport-contours' }[label];
    await wait(page, `${label} context geometry`, async ({ source: sourceId, count: expected }) => (await window.__quietMap.getSource(sourceId).getData()).features.length === expected, { source, count });
  }
  await page.getByRole('button', { name: 'Fit visible context', exact: true }).click();
  await wait(page, 'context camera settled', () => !window.__quietMap.isMoving());
  const contextPoint = await page.evaluate(() => { const feature = window.__quietMap.querySourceFeatures('context-county-fire')[0]; const coords = feature?.geometry?.coordinates; const p = window.__quietMap.project(coords); const rect = window.__quietMap.getCanvas().getBoundingClientRect(); return { x: rect.x + p.x, y: rect.y + p.y }; });
  await page.mouse.click(contextPoint.x, contextPoint.y);
  await page.getByText('LA County fire station', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Close inspection', exact: true }).click();
  const airportPoint = await page.evaluate(() => { const features = window.__quietMap.querySourceFeatures('context-airport-contours'); const first = features[0]; const points = []; const collect = (value) => { if (Array.isArray(value)) { if (value.length === 2 && value.every((part) => typeof part === 'number')) points.push(value); else value.forEach(collect); } }; collect(first?.geometry?.coordinates); const vertexCount = features.reduce((count, feature) => { const all = []; const collectAll = (value) => { if (Array.isArray(value)) { if (value.length === 2 && value.every((part) => typeof part === 'number')) all.push(value); else value.forEach(collectAll); } }; collectAll(feature.geometry?.coordinates); return count + all.length; }, 0); const center = points.reduce((sum, point) => [sum[0] + point[0], sum[1] + point[1]], [0, 0]).map((value) => value / Math.max(1, points.length)); const p = window.__quietMap.project(center); const rect = window.__quietMap.getCanvas().getBoundingClientRect(); return { x: rect.x + p.x, y: rect.y + p.y, vertexCount }; });
  if (airportPoint.vertexCount < 100) throw new Error(`Airport contour fixture unexpectedly sparse: ${airportPoint.vertexCount}`);
  await page.mouse.click(airportPoint.x, airportPoint.y);
  await page.getByText('Airport noise contour location', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Close inspection', exact: true }).click();
  const manifest = JSON.parse(await fs.readFile(path.join(appRoot, 'public/_local-data/dense/dense/manifest.json'), 'utf8'));
  const shard = manifest.shards.find((item) => item.id === 'r02-c02') || manifest.shards[0];
  const shardData = JSON.parse(gunzipSync(await fs.readFile(path.join(appRoot, 'public/_local-data/dense/dense', shard.path))));
  const receiver = shardData.records[Math.floor(shardData.records.length / 2)];
  await page.locator('#place-search').fill(`${receiver[2]}, ${receiver[1]}`);
  await page.getByRole('button', { name: 'Find place', exact: true }).click();
  await wait(page, 'search camera target', (row) => !window.__quietMap.isMoving() && Math.abs(window.__quietMap.getCenter().lng - row[1]) < 0.00001, receiver);
  // Click the actual canvas at the source record coordinate, including the sidebar offset.
  const point = await page.evaluate((row) => { const map = window.__quietMap; const p = map.project([row[1], row[2]]); const rect = map.getCanvas().getBoundingClientRect(); return { x: rect.x + p.x, y: rect.y + p.y }; }, receiver);
  await page.mouse.click(point.x, point.y);
  await page.getByText(`Receiver ${receiver[0]} · 0 m from selection`, { exact: true }).waitFor();
  const assertValue = async (index) => { const actual = await page.locator('.receiver-result strong').textContent(); if (actual !== receiver[index].toFixed(1)) throw new Error(`Receiver value mismatch: expected ${receiver[index]}, got ${actual}`); };
  await assertValue(7);
  await page.getByRole('radio', { name: 'Night', exact: true }).click();
  await assertValue(9);
  await page.getByRole('radio', { name: '23 sources', exact: true }).click();
  await assertValue(6);
  await page.getByRole('button', { name: 'Save receiver', exact: true }).click();
  await page.locator('.saved-places summary').click();
  if (await page.locator('.comparison-row strong').textContent() !== receiver[6].toFixed(1)) throw new Error('Saved comparison period/scenario is stale');
  await page.getByRole('radio', { name: 'Day', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  if (await page.getByRole('radio', { name: 'Evening', exact: true }).getAttribute('aria-checked') !== 'true') throw new Error('Period keyboard navigation failed');
  await page.getByRole('radio', { name: '585 sources', exact: true }).click();
  await assertValue(8);
  await page.getByRole('checkbox', { name: 'Show modeled road sources' }).check();
  await wait(page, '585 road source geometry', async () => (await window.__quietMap.getSource('modeled-sources').getData()).features.length === 585);
  await page.getByRole('radio', { name: '23 sources', exact: true }).click();
  await wait(page, '23 road source geometry', async () => (await window.__quietMap.getSource('modeled-sources').getData()).features.length === 23);
  await page.getByRole('radio', { name: '585 sources', exact: true }).click();
  await wait(page, '585 road source geometry restored', async () => (await window.__quietMap.getSource('modeled-sources').getData()).features.length === 585);
  const before3d = await page.evaluate(() => ({ center: window.__quietMap.getCenter().toArray(), zoom: window.__quietMap.getZoom() }));
  await page.getByRole('radio', { name: '3D', exact: true }).click();
  await wait(page, 'real 3D terrain and buildings', () => Boolean(window.__quietMap.getTerrain() && window.__quietMap.getPitch() > 20 && window.__quietMap.getLayoutProperty('buildings-3d', 'visibility') === 'visible' && window.__quietMap.querySourceFeatures('buildings-3d').length > 0));
  await wait(page, 'surface has no dots or columns in 3D', () => ['dense-dots', 'dense-columns'].every((id) => window.__quietMap.getLayoutProperty(id, 'visibility') === 'none'));
  const after3d = await page.evaluate(() => ({ center: window.__quietMap.getCenter().toArray(), zoom: window.__quietMap.getZoom() }));
  if (Math.abs(before3d.center[0] - after3d.center[0]) > 0.0001 || Math.abs(before3d.center[1] - after3d.center[1]) > 0.0001 || Math.abs(before3d.zoom - after3d.zoom) > 0.05) throw new Error('3D mode reset the camera');
  await page.screenshot({ path: path.join(appRoot, `release/recovery-3d-${engine}.png`) });
  await page.getByRole('radio', { name: '2D', exact: true }).click();
  await wait(page, '2D restored', () => !window.__quietMap.getTerrain() && window.__quietMap.getPitch() < 0.1);
  await page.getByRole('button', { name: 'Fit study area' }).click();
  await wait(page, 'fit command settles', () => !window.__quietMap.isMoving() && window.__quietMap.getZoom() < 14.5);
  const cameraBeforeStyle = await page.evaluate(() => window.__quietMap.getCenter().toArray());
  await page.getByRole('radio', { name: 'Bands', exact: true }).click();
  const cameraAfterStyle = await page.evaluate(() => window.__quietMap.getCenter().toArray());
  if (cameraBeforeStyle.some((value, index) => Math.abs(value - cameraAfterStyle[index]) > 0.0000001)) throw new Error('Style change moved camera');
  await page.screenshot({ path: path.join(appRoot, `release/recovery-desktop-${engine}.png`) });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('.saved-places summary').waitFor();
  await wait(page, 'context visibility restored', () => ['context-county-fire', 'context-city-fire', 'context-heliports', 'context-airport-contours'].every((id) => window.__quietMap.getLayoutProperty(id, 'visibility') === 'visible'));
  await page.locator('.saved-places summary').click();
  if (await page.locator('.comparison-row').count() !== 1) throw new Error('Saved receiver did not survive reload');
  if (await page.getByRole('radio', { name: 'Bands', exact: true }).getAttribute('aria-checked') !== 'true') throw new Error('View state did not survive reload');
  await page.locator('#place-search').fill('34.6, -117.8');
  await page.getByRole('button', { name: 'Find place', exact: true }).click();
  await wait(page, 'outside coverage orientation', () => !window.__quietMap.isMoving() && window.__quietMap.getCenter().lng > -118);
  const emptyPoint = await page.evaluate(() => { const rect = window.__quietMap.getCanvas().getBoundingClientRect(); return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 }; });
  await page.mouse.click(emptyPoint.x, emptyPoint.y);
  await page.getByText('No nearby modeled receiver', { exact: true }).waitFor();
  const mobileGeometry = [];
  for (const width of [390, 320]) {
    const mobile = await browser.newPage({ viewport: { width, height: 844 } });
    mobile.on('pageerror', (error) => failures.push(`mobile${width}: ${error.message}`));
    await loaded(mobile);
    const geometry = await mobile.evaluate(() => {
      const panel = document.querySelector('.map-guide').getBoundingClientRect();
      const legend = document.querySelector('.workspace-legend').getBoundingClientRect();
      return { viewport: innerHeight, panelHeight: panel.height, legendBottom: legend.bottom, panelTop: panel.top, horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1, mapCenterAccessible: Boolean(document.elementFromPoint(innerWidth / 2, innerHeight / 2)?.closest('.map')) };
    });
    if (geometry.horizontalOverflow || !geometry.mapCenterAccessible || geometry.panelHeight > 220 || geometry.legendBottom > geometry.panelTop) throw new Error(`Mobile layout obstruction: ${JSON.stringify(geometry)}`);
    await mobile.getByRole('button', { name: 'Controls', exact: true }).click();
    await mobile.getByRole('radio', { name: 'Glow', exact: true }).click();
    await mobile.getByRole('button', { name: 'Less', exact: true }).click();
    await mobile.screenshot({ path: path.join(appRoot, `release/recovery-mobile-${engine}-${width}.png`) });
    mobileGeometry.push({ width, ...geometry });
    await mobile.close();
  }
  // Basemap requests stay unresolved; science must load on its own.
  const stalled = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await stalled.route('https://tile.openstreetmap.org/**', () => {});
  await loaded(stalled);
  await stalled.getByRole('radio', { name: 'Glow', exact: true }).click();
  await wait(stalled, 'treatment works while basemap pending', () => window.__quietMap.getLayoutProperty('dense-glow', 'visibility') === 'visible' && window.__quietMap.getLayoutProperty('dense-field', 'visibility') === 'none');
  await stalled.close();
  if (requests.some((url) => url.includes('four-region-relative') || url.includes('/_preview-data/'))) throw new Error('New map requested a withheld/cross-profile payload');
  if (failures.length) throw new Error(failures.join(' | '));
  console.log(JSON.stringify({ status: 'DENSE_RECOVERY_BROWSER_PASS', engine, checks: ['exclusive-raster-treatments', 'context-layer-toggle-and-fit', 'context-inspection-is-separate', 'exact-surface-picking', 'period-and-scenario-sync', 'scenario-source-geometry', 'saved-comparison', 'keyboard-periods', 'real-terrain-and-building-3d', 'camera-preservation', 'one-shot-fit', 'reload-state', 'empty-coverage', 'mobile-390-320', 'stalled-basemap', 'legacy-quarantine'], mobileGeometry, scientificRequestCount: new Set(requests).size }, null, 2));
} catch (error) { console.error(`browser-check: ${error.stack || error.message}`); console.error(JSON.stringify({ pageErrors: failures, body: await activePage?.locator('body').innerText().catch(() => '') })); await activePage?.screenshot({path: path.join(appRoot, `release/recovery-failure-${engine}.png`)}).catch(() => {}); process.exitCode = 1; }
finally { await browser?.close().catch(() => {}); server?.kill('SIGTERM'); await fs.writeFile(path.join(appRoot, `release/browser-check-${engine}-last.log`), serverOutput); }
