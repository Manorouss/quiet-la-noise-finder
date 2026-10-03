import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { parsePilotViewHash } from '../src/lib/pilot-view-state.js';

const model = 'tarzana-pilot-r02-c03-freeway-v1';
const periods = ['D', 'E', 'N'];

test('same-document view hashes restore period, selections, display mode, and exact camera', async () => {
  const view = parsePilotViewHash(`#model=${model}&period=N&building=71728&receiver=198418&mode=3d&lng=-118.566123&lat=34.170321&z=18.25`, model, periods);
  assert.deepEqual(view, {
    period: 'N', buildingPk: 71728, receiverId: 198418, mode3d: true,
    camera: { lng: -118.566123, lat: 34.170321, zoom: 18.25 }, modelMismatch: false,
  });
  const portal = await readFile(new URL('../src/components/PilotPortal.tsx', import.meta.url), 'utf8');
  assert.match(portal, /window\.addEventListener\('hashchange', applyHash\)/);
  assert.match(portal, /window\.removeEventListener\('hashchange', applyHash\)/);
});

test('a subsequent hash without selection or camera clears prior view state', () => {
  assert.deepEqual(parsePilotViewHash(`#model=${model}&period=E`, model, periods), {
    period: 'E', buildingPk: null, receiverId: null, mode3d: false, camera: null, modelMismatch: false,
  });
});

test('invalid period and partial or non-finite cameras normalize to the default view', () => {
  assert.deepEqual(parsePilotViewHash('#period=invalid&building=NaN&receiver=2&lng=-118&lat=34&z=Infinity', model, periods), {
    period: 'D', buildingPk: null, receiverId: 2, mode3d: false, camera: null, modelMismatch: false,
  });
});

test('blank, out-of-range, and out-of-map-limit camera values are rejected', () => {
  for (const hash of [
    '#lng=&lat=34&z=14',
    '#lng=-118&lat=&z=14',
    '#lng=-118&lat=34&z=',
    '#lng=-118&lat=1000&z=14',
    '#lng=181&lat=34&z=14',
    '#lng=-118&lat=34&z=10.99',
    '#lng=-118&lat=34&z=19.01',
  ]) {
    assert.equal(parsePilotViewHash(hash, model, periods).camera, null, hash);
  }
  assert.deepEqual(parsePilotViewHash('#lng=-180&lat=90&z=11', model, periods).camera, {
    lng: -180, lat: 90, zoom: 11,
  });
});
