#!/usr/bin/env node
// End-to-end check of the county map (/map) in headless Chrome: search with suggestions, selection,
// periods, styles, 3D, aircraft layer, link state, zoom out, phone layout. Writes screenshots and a
// JSON report; exits 1 on any failed check or page error.
//
//   node scripts/county-map-e2e.mjs [base URL, default http://localhost:4174] [output dir]
//
// Uses the installed Google Chrome (channel "chrome") with software WebGL, so it runs while the
// screen is off. Needs the address services (LA County CAMS and parcels) and the R2 map data.
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const base = process.argv[2] || 'http://localhost:4174';
const out = path.resolve(process.argv[3] || 'e2e-out');
mkdirSync(out, { recursive: true });
const report = { base, checks: [], errors: [], failedRequests: [] };
const check = (name, ok, detail = '') => { report.checks.push({ name, ok: Boolean(ok), detail }); console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ` · ${detail}` : ''}`); };

const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });

async function open(viewport, hash, label) {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1, hasTouch: viewport.width < 720 });
  page.setDefaultTimeout(60000);
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    // Transient network resets to outside services are noted, not failures.
    if (/net::ERR_(CONNECTION_RESET|NETWORK_CHANGED|TIMED_OUT|CONNECTION_CLOSED)/.test(m.text())) report.network = [...(report.network ?? []), `${label}: ${m.text().slice(0, 160)}`];
    else report.errors.push(`${label}: ${m.text().slice(0, 240)}`);
  });
  page.on('pageerror', (e) => report.errors.push(`${label}: pageerror ${String(e).slice(0, 240)}`));
  page.on('response', (r) => { if (r.status() >= 400 && !/\.pbf|favicon/.test(r.url())) report.failedRequests.push(`${label}: ${r.status()} ${r.url().slice(0, 160)}`); });
  await page.goto(`${base}/map/${hash}`, { waitUntil: 'load' });
  await page.waitForFunction(() => window.__quietCountyMap?.loaded(), null, { timeout: 300000 });   // software WebGL on a busy Mac
  await idle(page);
  return page;
}
const idle = (page, ms = 15000) => page.evaluate((timeout) => new Promise((resolve) => {
  const map = window.__quietCountyMap;
  if (!map.isMoving() && map.loaded() && map.areTilesLoaded()) return setTimeout(resolve, 300);
  map.once('idle', () => setTimeout(resolve, 300));
  setTimeout(resolve, timeout);
}), ms);
// Share of warm noise colours (yellow to purple) in a map area screenshot: proves a raster style drew.
async function warmShare(page) {
  const png = await page.screenshot({ clip: { x: 340, y: 70, width: 600, height: 600 } });
  return page.evaluate(async (b64) => {
    const img = new Image(); img.src = `data:image/png;base64,${b64}`; await img.decode();
    const canvas = document.createElement('canvas'); canvas.width = img.width; canvas.height = img.height;
    const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0);
    const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let warm = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] > 200 && d[i + 1] < 200 && d[i + 2] < 150) warm += 1;
    return warm / (d.length / 4);
  }, png.toString('base64'));
}
const text = (page, selector) => page.evaluate((s) => document.querySelector(s)?.textContent?.trim() ?? null, selector);

async function searchAddress(page, typed, pick) {
  await page.fill('#place-search', '');
  await page.click('#place-search');
  await page.type('#place-search', typed, { delay: 20 });
  await page.waitForSelector('.search-suggestions li', { timeout: 10000 });
  const items = await page.$$eval('.search-suggestions li', (els) => els.map((e) => e.textContent));
  const index = Math.max(0, items.findIndex((t) => t.includes(pick)));
  await page.click(`.search-suggestions li >> nth=${index}`);
  await page.waitForSelector('.receiver-result', { timeout: 20000 });
  await page.waitForFunction(() => !document.querySelector('.selection-address.is-pending'), null, { timeout: 15000 });
  await page.evaluate(() => window.__quietCountyMap.triggerRepaint());
  await idle(page);
  return items;
}

// ---- desktop
{
  const page = await open({ width: 1280, height: 800 }, '#lat=34.17700&lng=-118.55500&z=16&period=D&style=field&mode=2d&base=light', 'desktop');
  check('desktop: field renders', await page.evaluate(() => window.__quietCountyMap.queryRenderedFeatures().length > 1000));
  const items = await searchAddress(page, '19305 Redwi', 'Tarzana');
  check('search: suggestions', items.length >= 2, items.join(' | '));
  const address = await text(page, '.selection-address');
  check('search: selects the house with its address', /19305 Redwing St/i.test(address ?? '') && !/^Near/.test(address ?? ''), address);
  check('search: level in words', Boolean(await text(page, '.level-words')), await text(page, '.level-words'));
  check('search: compares with mapped buildings', /than \d+% of mapped buildings/.test(await text(page, '.compare-line') ?? ''), await text(page, '.compare-line'));
  check('search: says which model computed it', /Computed with the (current|previous|upgraded|earlier) model/.test(await text(page, '.model-line') ?? ''), await text(page, '.model-line'));
  check('search: least exposed wall', /Least exposed wall/.test(await text(page, '.receiver-section') ?? ''));
  const nearby = await page.waitForSelector('.nearby-item', { timeout: 15000 }).then(() => text(page, '.nearby')).catch(() => null);
  check('search: nearby fire station', /Fire station \d+.*mi/.test(nearby ?? ''), nearby);
  await page.click('.save-place:has-text("Save to compare")');
  await page.waitForSelector('.saved-row', { timeout: 10000 }).catch(() => null);
  check('save to compare lists the house', /19305 Redwing/i.test(await text(page, '.saved-places') ?? ''), await text(page, '.saved-row'));
  await page.reload({ waitUntil: 'load' });
  await page.waitForFunction(() => window.__quietCountyMap?.loaded(), null, { timeout: 300000 });   // software WebGL on a busy Mac
  check('saved places survive a reload', /19305 Redwing/i.test(await text(page, '.saved-places') ?? ''));
  await page.click('.saved-go');
  await page.waitForFunction(() => /19305 Redwing/i.test(document.querySelector('.selection-address')?.textContent ?? ''), null, { timeout: 30000 }).catch(() => null);
  check('a saved place reopens', /19305 Redwing/i.test(await text(page, '.selection-address') ?? ''), await text(page, '.selection-address'));
  await idle(page);
  await page.screenshot({ path: `${out}/desktop_search.png` });

  // Click a building near the map centre.
  const clicked = await page.evaluate(() => {
    const map = window.__quietCountyMap;
    const f = map.queryRenderedFeatures({ layers: ['building-footprints'] }).find((x) => x.geometry.type === 'Polygon');
    if (!f) return null;
    const ring = f.geometry.coordinates[0];
    const c = ring.reduce((a, p) => [a[0] + p[0] / ring.length, a[1] + p[1] / ring.length], [0, 0]);
    const p = map.project(c);
    return { x: p.x, y: p.y };
  });
  if (clicked) {
    const box = await page.locator('.county-map canvas').boundingBox();
    await page.mouse.click(box.x + clicked.x, box.y + clicked.y);
    await page.waitForFunction(() => !document.querySelector('.selection-address.is-pending'), null, { timeout: 15000 });
  }
  check('click: building or wall point shows an address', Boolean(await text(page, '.selection-address')), await text(page, '.selection-address'));

  for (const [label, key] of [['Evening', 'e'], ['Night', 'n'], ['24 h', 'q']]) {
    await page.click(`.quick-controls button:has-text("${label}")`);
    await idle(page);
    const url = await page.evaluate(() => window.__quietCountyMap.getStyle().sources.field.url);
    check(`period ${label}: field_${key}`, url.endsWith(`field_${key}.pmtiles`), url.split('/').pop());
  }
  check('24 h legend says CNEL', /CNEL/.test(await text(page, '.county-legend span') ?? ''), await text(page, '.county-legend span'));
  await page.click('.quick-controls button:has-text("Day")');
  for (const style of ['Bands', 'Glow', 'Dots', 'Field']) {
    await page.click(`.guide-section button:has-text("${style}")`);
    await idle(page);
    await page.waitForTimeout(1500);
    const share = await warmShare(page);
    check(`style ${style} draws the noise`, share > (style === "Dots" ? 0.01 : 0.03), share.toFixed(3));  // dots are sparse at street level
    // Water (and the inland feather) go over the surface, not over the footprints; in Dots the water goes over the points and there is no feather.
    const order = await page.evaluate(() => window.__quietCountyMap.getStyle().layers.map((l) => l.id));
    const over = (a, b) => order.indexOf(a) > order.indexOf(b) && order.indexOf(b) >= 0;
    check(`style ${style}: water over the noise`, style === 'Dots'
      ? over('noise-water', 'receivers-dots') && over('noise-water-pier', 'noise-water') && !order.includes('coverage-feather')
      : over('coverage-feather', 'noise-field') && over('noise-water', 'coverage-feather') && over('noise-water-pier', 'noise-water') && over('building-footprints', 'noise-water-pier'));
  }
  check('styles switch without errors', report.errors.filter((e) => e.startsWith('desktop')).length === 0);

  // Software WebGL renders 3D slowly: switch at a lighter zoom and poll for the extruded buildings.
  await page.evaluate(() => window.__quietCountyMap.jumpTo({ zoom: 16 }));
  await idle(page);
  await page.click('.workspace-header button:has-text("3D")');
  // (In 3D the map keeps streaming distant terrain, so loaded() can stay false.)
  const has3d = await page.waitForFunction(() => window.__quietCountyMap?.getLayer('buildings-3d') && window.__quietCountyMap.queryRenderedFeatures({ layers: ['buildings-3d'] }).length > 0, null, { timeout: 120000, polling: 1000 }).then(() => true).catch(() => false);
  check('3D: extruded buildings', has3d);
  const cleared = await page.waitForFunction(() => !document.querySelector('.workspace-state'), null, { timeout: 60000, polling: 1000 }).then(() => true).catch(() => false);
  check('3D: loading notice clears', cleared);
  await page.screenshot({ path: `${out}/desktop_3d.png`, timeout: 120000 });
  await page.click('.workspace-header button:has-text("2D")', { timeout: 120000 });
  await page.waitForFunction(() => window.__quietCountyMap?.getPitch() === 0, null, { timeout: 45000, polling: 1000 }).catch(() => null);

  await page.evaluate(() => window.__quietCountyMap.jumpTo({ center: [-118.4899, 34.2098], zoom: 13.5 }));
  await idle(page);
  // Aircraft is on by default and part of the 24 h colors; switching it off gives the roads-only surface.
  const fieldUrl = () => page.evaluate(() => window.__quietCountyMap.getStyle().sources.field.url);
  await page.click('.quick-controls button:has-text("24 h")');
  await idle(page);
  check('aircraft on by default', await page.evaluate(() => document.querySelector('.context-airport-contours input')?.checked === true));
  check('aircraft contours drawn', await page.evaluate(() => window.__quietCountyMap.queryRenderedFeatures({ layers: ['context-airport-contours-line'] }).length > 0));
  check('24 h field includes aircraft', /_q\.pmtiles$/.test(await fieldUrl()), await fieldUrl());
  await page.screenshot({ path: `${out}/desktop_aircraft.png` });
  // Switches stay under the pointer: the row does not move when toggled (even though the period and panel change).
  // (Scrolled into view first: a click on a row below the fold scrolls it, which is not the row moving.)
  const switchTop = async (sel) => { await page.locator(sel).scrollIntoViewIfNeeded(); return page.evaluate((s) => document.querySelector(s).getBoundingClientRect().top, sel); };
  const before = await switchTop('.context-airport-contours .ml-switch');
  await page.click('.context-airport-contours .ml-main');
  await idle(page);
  const moved = Math.abs(await page.evaluate(() => document.querySelector('.context-airport-contours .ml-switch').getBoundingClientRect().top) - before);
  check('aircraft switch stays in place', moved < 1, `moved ${moved.toFixed(1)} px`);
  check('aircraft off: roads-only 24 h field', /_r\.pmtiles$/.test(await fieldUrl()), await fieldUrl());
  check('aircraft off: contours hidden', await page.evaluate(() => window.__quietCountyMap.getLayoutProperty('context-airport-contours-line', 'visibility') === 'none'));
  check('aircraft off: legend says roads only', /Roads only/.test(await text(page, '.county-legend') ?? ''));
  await page.screenshot({ path: `${out}/desktop_aircraft_off.png` });
  await page.click('.quick-controls button:has-text("Day")');
  await page.click('.context-airport-contours .ml-main');
  await idle(page);
  check('aircraft toggle switches to 24 h', /24 h/.test(await text(page, '.quick-controls [aria-checked="true"]') ?? ''));
  // Details are in a hover card beside the panel, not in the panel.
  await page.hover('.context-heliports .ml-main');
  const card = await page.waitForSelector('.context-heliports .ml-card.is-open', { timeout: 3000 }).then((el) => el.boundingBox()).catch(() => null);
  check('hover card opens beside the panel', card && card.x >= 328, JSON.stringify(card));
  check('trains listed as a noise source', /Trains\s*Included/.test(await text(page, '.ml-panel') ?? ''));
  // Heliports and fire stations draw as icons (Van Nuys Airport has both nearby).
  const heliTop = await switchTop('.context-heliports .ml-switch');
  await page.click('.context-heliports .ml-main');
  check('heliport switch stays in place', Math.abs(await page.evaluate(() => document.querySelector('.context-heliports .ml-switch').getBoundingClientRect().top) - heliTop) < 1);
  await page.click('.context-fire .ml-main');
  await idle(page);
  const icons = await page.evaluate(() => {
    const map = window.__quietCountyMap;
    return { heli: map.queryRenderedFeatures({ layers: ['context-heliports'] }).length, fire: map.queryRenderedFeatures({ layers: ['context-county-fire', 'context-city-fire'] }).length, images: ['ql-heliport', 'ql-fire'].every((id) => map.hasImage(id)) };
  });
  check('heliport and fire station icons', icons.heli > 0 && icons.fire > 0 && icons.images, JSON.stringify(icons));
  await page.screenshot({ path: `${out}/desktop_layers.png` });

  await page.evaluate(() => window.__quietCountyMap.jumpTo({ center: [-118.45, 34.17], zoom: 10.3 }));
  await idle(page);
  await page.screenshot({ path: `${out}/desktop_zoomed_out.png` });
  await page.close();
}

// ---- link state restore
{
  const page = await open({ width: 1280, height: 800 }, '#lat=34.20980&lng=-118.48990&z=14&period=Q&style=bands&mode=2d&base=dark&roads=1&context=airport-contours', 'link');
  const state = await page.evaluate(() => ({
    period: document.querySelector('.quick-controls [aria-checked="true"]')?.textContent,
    style: [...document.querySelectorAll('.guide-section [aria-checked="true"]')].map((b) => b.textContent),
    airport: document.querySelector('.context-airport-contours input')?.checked,
    roads: window.__quietCountyMap.getLayoutProperty('roads-modeled', 'visibility'),
    roadLabels: Boolean(window.__quietCountyMap.getLayer('roads-modeled-label')),
  }));
  check('road lines labelled with traffic', state.roadLabels);
  check('link restores period, style, base, roads, aircraft', state.period === '24 h' && state.style.includes('Bands') && state.style.includes('Dark') && state.airport && state.roads === 'visible', JSON.stringify(state));
  await page.screenshot({ path: `${out}/link_restore.png` });
  await page.close();
}

// ---- shared link with a selected place, and the site root
{
  const page = await open({ width: 1280, height: 800 }, '#lat=34.17993&lng=-118.55574&z=18&period=D&style=field&mode=2d&base=light&sel=-118.555738,34.179930', 'shared');
  await page.waitForSelector('.receiver-result', { timeout: 30000 }).catch(() => null);
  await page.waitForFunction(() => !document.querySelector('.selection-address.is-pending'), null, { timeout: 15000 }).catch(() => null);
  const shared = await text(page, '.selection-address') ?? '';
  check('shared link reopens the selected place with its own address', /Calvin/i.test(shared) && !/^Near/.test(shared), shared);
  await page.fill('#place-search', '100 N Garfield Ave, Pasadena');
  await page.press('#place-search', 'Enter');
  await page.waitForFunction(() => /not modeled|outside the area/i.test(document.querySelector('.receiver-section')?.textContent ?? ''), null, { timeout: 20000 }).catch(() => null);
  const outside = await text(page, '.receiver-section');
  check('address outside coverage says not modeled yet', /Not modeled yet/i.test(outside ?? ''), (outside ?? '').slice(0, 90));
  await page.goto(`${base}/`, { waitUntil: 'load' });
  await page.waitForURL(/\/map\/?/, { timeout: 15000 }).catch(() => null);
  check('site root opens the county map', /\/map\/?(#|$)/.test(page.url()), page.url());
  await page.close();
}

// ---- phone
{
  const page = await open({ width: 375, height: 812 }, '', 'phone');
  check('phone: search visible without opening Controls', await page.isVisible('#place-search'));
  await searchAddress(page, '6025 Calvin', 'Tarzana');
  const address = await text(page, '.selection-address');
  check('phone: search selects the house', /6025 Calvin/i.test(address ?? ''), address);
  const layout = await page.evaluate(() => {
    const legend = document.querySelector('.county-legend').getBoundingClientRect();
    const sheet = document.querySelector('.map-guide').getBoundingClientRect();
    const map = window.__quietCountyMap;
    const sel = map.queryRenderedFeatures({ layers: ['selected-building'] })[0];
    let houseY = null;
    if (sel) { const ring = sel.geometry.type === 'Polygon' ? sel.geometry.coordinates[0] : sel.geometry.coordinates; houseY = ring.reduce((a, p) => a + map.project(p).y, 0) / ring.length + 56; }
    return { legendBottom: legend.bottom, sheetTop: sheet.top, houseY };
  });
  check('phone: legend stays compact', layout.legendBottom < 260, JSON.stringify(layout));
  check('phone: searched house is above the sheet', layout.houseY !== null && layout.houseY < layout.sheetTop, JSON.stringify(layout));
  await page.screenshot({ path: `${out}/phone_search.png` });
  await page.click('.sheet-toggle');
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${out}/phone_controls.png` });
  await page.close();
}

await browser.close();
report.ok = report.checks.every((c) => c.ok) && report.errors.length === 0;
writeFileSync(`${out}/report.json`, JSON.stringify(report, null, 1));
if (report.errors.length) console.log('page errors:\n  ' + report.errors.join('\n  '));
if (report.failedRequests.length) console.log('failed requests:\n  ' + report.failedRequests.join('\n  '));
console.log(report.ok ? 'E2E PASSED' : 'E2E FAILED');
process.exitCode = report.ok ? 0 : 1;
