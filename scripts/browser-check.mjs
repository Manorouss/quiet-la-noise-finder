import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { chromium, webkit } from '@playwright/test';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const port = 3197;
const engineName = process.env.QUIET_LA_BROWSER_ENGINE === 'webkit' ? 'webkit' : 'chromium';
const browserType = engineName === 'webkit' ? webkit : chromium;
const server = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'dev', '-H', '127.0.0.1', '-p', String(port)], {
  cwd: appRoot,
  env: {
    ...process.env,
    NEXT_PUBLIC_QUIET_LA_DATA_PROFILE: 'local_v3',
    NEXT_PUBLIC_QUIET_LA_DATA_ROOT: '/_local-data/v3',
    NEXT_PUBLIC_QUIET_LA_LOCAL_RECOVERY_COMMAND: 'npm run stage:local',
  },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let output = '';
let browser;
server.stdout.on('data', (chunk) => { output += chunk.toString(); });
server.stderr.on('data', (chunk) => { output += chunk.toString(); });
async function waitForServer() { for (let i = 0; i < 80; i += 1) { try { const response = await fetch(`http://127.0.0.1:${port}`); if (response.ok) return; } catch {} await new Promise((resolve) => setTimeout(resolve, 250)); } throw new Error(`server did not start: ${output}`); }
async function waitForPage(page, label, predicate, arg) {
  try { await page.waitForFunction(predicate, arg, { timeout: 10000 }); }
  catch (error) { throw new Error(`${label}: ${error.message}`); }
}
async function checkMobileLayout(page, width, pageErrors, consoleErrors, screenshotPath) {
  page.on('pageerror', (error) => pageErrors.push(`mobile-${width}: ${error.message}`));
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(`mobile-${width}: ${message.text()}`); });
  await page.goto(`http://127.0.0.1:${port}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.brand-instrument');
  await waitForPage(page, `mobile-${width} local ready state`, () => document.body.textContent?.includes('Active local layers'));
  const geometry = await page.evaluate(() => {
    const rect = (selector) => {
      const node = document.querySelector(selector);
      const box = node?.getBoundingClientRect();
      return box ? { x: box.x, y: box.y, width: box.width, height: box.height, right: box.right, bottom: box.bottom } : null;
    };
    const intersects = (a, b) => Boolean(a && b && a.x < b.right && a.right > b.x && a.y < b.bottom && a.bottom > b.y);
    const fit = rect('.preset-row .text-button');
    const period = rect('.period-instrument');
    const attribution = rect('.map-attribution');
    const layer = rect('.layer-instrument');
    const disclosure = rect('.disclosure-rail');
    const fitTarget = fit ? document.elementFromPoint(fit.x + fit.width / 2, fit.y + fit.height / 2) : null;
    const attributionLinkNode = document.querySelector('.map-attribution a');
    const attributionLinkBox = attributionLinkNode?.getBoundingClientRect();
    const attributionLink = attributionLinkBox ? { x: attributionLinkBox.x, y: attributionLinkBox.y, width: attributionLinkBox.width, height: attributionLinkBox.height, right: attributionLinkBox.right, bottom: attributionLinkBox.bottom } : null;
    const attributionLinkTarget = attributionLink ? document.elementFromPoint(attributionLink.x + attributionLink.width / 2, attributionLink.y + attributionLink.height / 2) : null;
    return {
      fit, period, attribution, attributionLink, layer, disclosure,
      fitIntersectsPeriod: intersects(fit, period),
      attributionIntersectsLayer: intersects(attribution, layer),
      attributionIntersectsDisclosure: intersects(attribution, disclosure),
      fitHit: Boolean(fitTarget?.closest('.text-button')),
      attributionLinkHit: Boolean(attributionLinkTarget?.closest('.map-attribution a')),
      fitTargetTag: fitTarget?.tagName ?? '',
      attributionTargetTag: attributionLinkTarget?.tagName ?? '',
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1 || document.body.scrollWidth > window.innerWidth + 1,
      centerInMap: Boolean(document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2)?.closest('.map')),
    };
  });
  if (!geometry.fit || !geometry.period || !geometry.attribution || !geometry.layer || !geometry.disclosure) throw new Error(`mobile-${width} layout controls are missing`);
  if (geometry.fit.height < 44 || geometry.fit.width < 44) throw new Error(`mobile-${width} Fit visible target is smaller than 44px`);
  if (geometry.fitIntersectsPeriod) throw new Error(`mobile-${width} Fit visible overlaps Scenario period`);
  if (!geometry.fitHit) throw new Error(`mobile-${width} Fit visible center hit target is not the button`);
  if (geometry.attributionIntersectsLayer || geometry.attributionIntersectsDisclosure) throw new Error(`mobile-${width} attribution overlaps a lower control bar`);
  if (!geometry.attributionLink || !geometry.attributionLinkHit) throw new Error(`mobile-${width} OpenStreetMap link center hit target is not its link`);
  if (geometry.horizontalOverflow) throw new Error(`mobile-${width} layout has horizontal overflow`);
  if (!geometry.centerInMap) throw new Error(`mobile-${width} viewport center is obstructed by a control`);
  await page.locator('.layer-instrument summary').click();
  if (!(await page.locator('.layer-instrument__panel').isVisible())) throw new Error(`mobile-${width} Noise layers sheet did not open`);
  await page.locator('.layer-instrument summary').click();
  if (await page.locator('.layer-instrument').getAttribute('open') !== null) throw new Error(`mobile-${width} Noise layers sheet did not close`);
  await page.locator('.mobile-limits-button').click();
  await page.locator('#limits-dialog').waitFor({ state: 'visible' });
  await page.getByRole('button', { name: 'Return to the map' }).click();
  if (await page.locator('#limits-dialog').isVisible()) throw new Error(`mobile-${width} Model limits dialog did not close`);
  if (screenshotPath) await page.screenshot({ path: screenshotPath, fullPage: true });
  return geometry;
}

try {
  await waitForServer();
  browser = await browserType.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const pageErrors = [];
  const consoleErrors = [];
  const payloadRequests = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('request', (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.includes('/_local-data/') || pathname.includes('/_preview-data/')) payloadRequests.push(pathname);
  });
  await page.goto(`http://127.0.0.1:${port}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.maplibregl-canvas');
  await waitForPage(page, 'local ready state', () => document.body.textContent?.includes('Active local layers'));
  await waitForPage(page, 'MapLibre style/data ready', () => Boolean(window.__quietMap?.isStyleLoaded() && window.__quietMap?.getLayer('quiet-tarzana-points')));
  if ((await page.locator('body').innerText()).includes('Loading the accepted v3 display layers')) throw new Error('local v3 remained in a loading state');
  const robots = await page.locator('meta[name="robots"]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('content')));
  if (!robots.includes('noindex,nofollow,noarchive')) throw new Error(`noindex contract missing: ${robots.join(',')}`);
  await page.getByRole('radio', { name: 'MODELED' }).click();
  await page.getByRole('radio', { name: 'CONTEXT' }).click();
  await page.getByRole('radio', { name: 'ALL DATA' }).click();
  await page.getByRole('radio', { name: 'Scenario period' }).count();
  await page.locator('.layer-instrument summary').click();
  const tarzanaRow = page.locator('label.layer-row').filter({ hasText: 'Tarzana mixed-road scenario' });
  await tarzanaRow.click();
  await waitForPage(page, 'Tarzana layer off', () => window.__quietMap?.getLayoutProperty('quiet-tarzana-points', 'visibility') === 'none');
  await tarzanaRow.click();
  await waitForPage(page, 'Tarzana layer on', () => window.__quietMap?.getLayoutProperty('quiet-tarzana-points', 'visibility') === 'visible');
  await page.getByRole('radio', { name: 'Bands' }).click();
  await waitForPage(page, 'bands display style', () => Number(window.__quietMap?.getPaintProperty('quiet-tarzana-points', 'circle-radius')) > 5);
  await page.selectOption('#region-preset', 'tarzana');
  await page.getByRole('button', { name: 'Fit visible' }).click();
  await page.getByRole('button', { name: /Zoom in/ }).click();
  await page.getByRole('button', { name: /Zoom out/ }).click();
  const mapCanvas = page.locator('.maplibregl-canvas');
  await mapCanvas.focus();
  const centerBeforeKeyboard = await page.evaluate(() => window.__quietMap?.getCenter().lat ?? 0);
  await page.keyboard.press('ArrowUp');
  await waitForPage(page, 'keyboard pan', (before) => Math.abs((window.__quietMap?.getCenter().lat ?? before) - before) > 0.00001, centerBeforeKeyboard);
  const zoomBeforeWheel = await page.evaluate(() => window.__quietMap?.getZoom() ?? 0);
  await page.mouse.move(700, 500);
  await page.mouse.wheel(0, -260);
  await waitForPage(page, 'wheel zoom', (before) => (window.__quietMap?.getZoom() ?? before) > before, zoomBeforeWheel);
  const attribution = await page.locator('.map-attribution').textContent();
  if (!attribution?.includes('OpenStreetMap contributors')) throw new Error('OpenStreetMap attribution is missing');
  const attributionLink = page.locator('.map-attribution a');
  if ((await attributionLink.textContent())?.trim() !== '© OpenStreetMap contributors') throw new Error('OpenStreetMap link text is not scoped to the contributor attribution');
  if (await attributionLink.getAttribute('href') !== 'https://www.openstreetmap.org/copyright') throw new Error('OpenStreetMap attribution is not linked to the copyright page');
  const railRow = page.getByTestId('rail-context-row');
  if (await railRow.count() !== 1) throw new Error('Metro non-spatial context record is missing');
  if (await railRow.locator('input').count() !== 0) throw new Error('Metro metadata row became a toggle');
  if (!(await railRow.textContent())?.includes('6 active route metadata records; geometry and rail acoustics are not admitted.')) throw new Error('Metro context disclosure is incorrect');
  const railBox = await railRow.boundingBox();
  const railCopyBox = await railRow.locator('.layer-row-copy').boundingBox();
  if (!railBox || !railCopyBox || railBox.width < 220 || railCopyBox.width < 150 || railBox.height < 43) throw new Error('Metro context disclosure is not readable at desktop width');
  if (await page.locator('.legend-instrument').isVisible()) throw new Error('legend overlaps the open Noise layers panel');
  const disabled = await page.locator('.layer-row--disabled input').isDisabled();
  if (!disabled) throw new Error('compatible combination control is not disabled');
  await page.evaluate(() => {
    const map = window.__quietMap;
    if (!map) throw new Error('MapLibre instance was not exposed for the browser assertion');
    return new Promise((resolve) => {
      map.once('moveend', resolve);
      map.fitBounds([[-118.591, 34.1668], [-118.577, 34.174]], { padding: 120, duration: 0, maxZoom: 15 });
    });
  });
  await page.waitForTimeout(250);
  await page.getByRole('radio', { name: 'Day' }).click();
  await waitForPage(page, 'scenario period day selection', () => document.querySelector('.period.is-active')?.textContent?.includes('D') === true);
  const dayValue = await page.evaluate(() => Number(window.__quietMap?.queryRenderedFeatures({ layers: ['quiet-tarzana-points'] })[0]?.properties?.value));
  if (!Number.isFinite(dayValue)) throw new Error('Tarzana day value is not rendered after fit');
  await page.getByRole('radio', { name: 'Evening' }).click();
  await waitForPage(page, 'scenario period update', (before) => document.querySelector('.period.is-active')?.textContent?.includes('E') === true && Number(window.__quietMap?.queryRenderedFeatures({ layers: ['quiet-tarzana-points'] })[0]?.properties?.value) !== before, dayValue);
  const canvas = page.locator('.maplibregl-canvas');
  const box = await canvas.boundingBox();
  if (!box) throw new Error('Map canvas has no measurable bounds');
  const projected = await page.evaluate(() => {
    const map = window.__quietMap;
    if (!map) throw new Error('MapLibre instance was not exposed for the browser assertion');
    const point = map.project([-118.5891656, 34.1671006]);
    return { x: point.x, y: point.y };
  });
  await page.mouse.click(box.x + projected.x, box.y + projected.y);
  await page.locator('.inspect-card').waitFor({ state: 'visible' });
  const inspectionCount = await page.locator('.inspection-record').count();
  if (inspectionCount < 1 || inspectionCount > 4) throw new Error(`inspection returned ${inspectionCount} records; expected 1..4`);
  const families = await page.locator('.inspection-record').evaluateAll((nodes) => nodes.map((node) => {
    const labels = [...node.querySelectorAll('dt')];
    const index = labels.findIndex((label) => label.textContent?.trim() === 'Source family');
    return index >= 0 ? labels[index].parentElement?.querySelector('dd')?.textContent?.trim() ?? '' : '';
  }));
  if (new Set(families).size !== families.length || families.some((family) => !family)) throw new Error(`inspection source families are not unique: ${families.join(', ')}`);
  await page.locator('.layer-instrument summary').click();
  if (await page.locator('.layer-instrument').getAttribute('open') !== null) throw new Error('desktop Noise layers panel did not close before screenshot');
  await page.screenshot({ path: path.join(appRoot, `release/browser-desktop-${engineName}.png`), fullPage: true });
  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const mobileGeometry = await checkMobileLayout(mobile, 390, pageErrors, consoleErrors, path.join(appRoot, `release/browser-mobile-${engineName}.png`));
  const compact = await browser.newPage({ viewport: { width: 320, height: 844 } });
  const compactGeometry = await checkMobileLayout(compact, 320, pageErrors, consoleErrors, path.join(appRoot, `release/browser-mobile-${engineName}-320.png`));
  const expectedPayloadRequests = [
    '/_local-data/v3/context/airport-contours.json.gz',
    '/_local-data/v3/context/rail.json',
    '/_local-data/v3/context/source341-mask.json',
    '/_local-data/v3/four-region-relative.json.gz',
    '/_local-data/v3/tarzana-scenario.json',
  ].sort();
  const actualPayloadRequests = [...new Set(payloadRequests)].sort();
  if (JSON.stringify(actualPayloadRequests) !== JSON.stringify(expectedPayloadRequests)) throw new Error(`local v3 payload request set drift: ${actualPayloadRequests.join(', ')}`);
  if (pageErrors.length) throw new Error(`${engineName} browser page errors: ${pageErrors.join(' | ')}`);
  if (consoleErrors.length) throw new Error(`${engineName} browser console errors: ${consoleErrors.join(' | ')}`);
  await browser.close();
  browser = undefined;
  console.log(JSON.stringify({ status: 'BROWSER_INTERACTION_PASS', engine: engineName, actions: ['view-filters', 'period-switch', 'layer-toggle', 'display-style', 'region-fit-moveend', 'wheel-zoom', 'keyboard-pan', 'zoom-controls', 'disabled-combination', 'separate-inspection-1-to-4', 'rail-readable-nontoggle', 'legend-nonoverlap', 'osm-attribution-link', 'noindex', 'mobile-geometry-hit-targets-390-and-320', 'mobile-sheet-and-limits'], payloadRequests: actualPayloadRequests, mobileGeometry: { width390: mobileGeometry, width320: compactGeometry }, screenshots: [`release/browser-desktop-${engineName}.png`, `release/browser-mobile-${engineName}.png`, `release/browser-mobile-${engineName}-320.png`], }, null, 2));
} catch (error) {
  console.error(`browser-check: ${error.message}`);
  process.exitCode = 1;
} finally {
  await browser?.close().catch(() => {});
  server.kill('SIGTERM');
  await fs.writeFile(path.join(appRoot, `release/browser-check-${engineName}-last.log`), output);
}
