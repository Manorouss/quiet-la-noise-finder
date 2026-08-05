import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const port = 3197;
const server = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'dev', '-H', '127.0.0.1', '-p', String(port)], { cwd: appRoot, stdio: ['ignore', 'pipe', 'pipe'] });
let output = '';
server.stdout.on('data', (chunk) => { output += chunk.toString(); });
server.stderr.on('data', (chunk) => { output += chunk.toString(); });
async function waitForServer() { for (let i = 0; i < 80; i += 1) { try { const response = await fetch(`http://127.0.0.1:${port}`); if (response.ok) return; } catch {} await new Promise((resolve) => setTimeout(resolve, 250)); } throw new Error(`server did not start: ${output}`); }

try {
  await waitForServer();
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(`http://127.0.0.1:${port}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.maplibregl-canvas');
  await page.waitForFunction(() => document.body.textContent?.includes('Active local layers'));
  const robots = await page.locator('meta[name="robots"]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('content')));
  if (!robots.includes('noindex,nofollow,noarchive')) throw new Error(`noindex contract missing: ${robots.join(',')}`);
  await page.getByRole('radio', { name: 'MODELED' }).click();
  await page.getByRole('radio', { name: 'CONTEXT' }).click();
  await page.getByRole('radio', { name: 'ALL DATA' }).click();
  await page.getByRole('radio', { name: 'Scenario period' }).count();
  await page.getByRole('radio', { name: 'Evening' }).click();
  await page.locator('.layer-instrument summary').click();
  await page.getByRole('checkbox', { name: 'Toggle Tarzana mixed-road scenario' }).uncheck();
  await page.getByRole('checkbox', { name: 'Toggle Tarzana mixed-road scenario' }).check();
  await page.selectOption('#region-preset', 'tarzana');
  await page.getByRole('button', { name: 'Fit visible' }).click();
  await page.getByRole('button', { name: /Zoom in/ }).click();
  await page.getByRole('button', { name: /Zoom out/ }).click();
  const attribution = await page.locator('.map-attribution').textContent();
  if (!attribution?.includes('OpenStreetMap contributors')) throw new Error('OpenStreetMap attribution is missing');
  if (await page.locator('.map-attribution a').getAttribute('href') !== 'https://www.openstreetmap.org/copyright') throw new Error('OpenStreetMap attribution is not linked to the copyright page');
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
  await page.screenshot({ path: path.join(appRoot, 'release/browser-desktop.png'), fullPage: true });
  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await mobile.goto(`http://127.0.0.1:${port}`, { waitUntil: 'networkidle' });
  await mobile.waitForSelector('.brand-instrument');
  if (!(await mobile.locator('.disclosure-rail').isVisible())) throw new Error('mobile disclosure rail is not visible');
  if (await mobile.locator('.layer-instrument').getAttribute('open') !== null) throw new Error('mobile Noise layers is open on initial load');
  const centerTarget = await mobile.evaluate(() => {
    const target = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2);
    return { tag: target?.tagName ?? '', className: typeof target?.className === 'string' ? target.className : '', inMap: Boolean(target?.closest('.map')) };
  });
  if (!centerTarget.inMap && !centerTarget.className.includes('maplibregl-canvas')) throw new Error(`mobile viewport center is obstructed by ${centerTarget.tag} ${centerTarget.className}`);
  await mobile.locator('.layer-instrument summary').click();
  if (!(await mobile.locator('.layer-instrument__panel').isVisible())) throw new Error('mobile Noise layers sheet did not open');
  await mobile.locator('.layer-instrument summary').click();
  if (await mobile.locator('.layer-instrument').getAttribute('open') !== null) throw new Error('mobile Noise layers sheet did not close');
  await mobile.locator('.mobile-limits-button').click();
  await mobile.locator('#limits-dialog').waitFor({ state: 'visible' });
  await mobile.getByRole('button', { name: 'Return to the map' }).click();
  if (await mobile.locator('#limits-dialog').isVisible()) throw new Error('mobile Model limits dialog did not close');
  await mobile.screenshot({ path: path.join(appRoot, 'release/browser-mobile.png'), fullPage: true });
  await browser.close();
  console.log(JSON.stringify({ status: 'BROWSER_INTERACTION_PASS', actions: ['view-filters', 'period-switch', 'layer-toggle', 'region-fit-moveend', 'zoom', 'disabled-combination', 'separate-inspection-1-to-4', 'rail-readable-nontoggle', 'legend-nonoverlap', 'osm-attribution-link', 'noindex', 'mobile-sheet-and-limits'], screenshots: ['release/browser-desktop.png', 'release/browser-mobile.png'] }, null, 2));
} catch (error) {
  console.error(`browser-check: ${error.message}`);
  process.exitCode = 1;
} finally {
  server.kill('SIGTERM');
  await fs.writeFile(path.join(appRoot, 'release/browser-check-last.log'), output);
}
