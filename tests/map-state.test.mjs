import assert from 'node:assert/strict';
import test from 'node:test';
import { fitPadding, inspectionForView, layerDisplayAllowed, receiverValue } from '../src/lib/map-state.js';
import { initialLayerToggles, resolveRuntimeProfile } from '../src/lib/runtime-profile.js';

const records = [
  { id: 'tarzana-59955', family: 'Tarzana mixed-road scenario', period: 'D', value: 105.2 },
  { id: 'airport-1', family: 'Airport planning contours', value: null },
  { id: 'tarzana:1', family: 'Four-region freeway model', value: 48.9 },
];
const receivers = new Map([['tarzana-59955', { d: 105.2, e: 103.0, n: 98.2 }]]);
const toggles = { tarzana_mixed_road_scenario: true, four_region_freeway_relative: true, airport_planning_contours: true };

test('invalidated legacy family stays withheld even when toggles request it', () => {
  assert.equal(layerDisplayAllowed('four_region_freeway_relative'), false);
  assert.equal(initialLayerToggles(resolveRuntimeProfile('local_v3'), Object.keys(toggles)).four_region_freeway_relative, false);
  assert.deepEqual(inspectionForView(records, receivers, 'D', 'all', toggles).map(r => r.id), ['tarzana-59955', 'airport-1']);
});

test('inspection tracks periods at the same receiver and excludes hidden families', () => {
  const night = inspectionForView(records, receivers, 'N', 'modeled', toggles);
  assert.deepEqual(night, [{ ...records[0], period: 'N', value: 98.2 }]);
  assert.deepEqual(inspectionForView(records, receivers, 'N', 'context', toggles), [records[1]]);
  assert.deepEqual(inspectionForView(records, receivers, 'D', 'modeled', { ...toggles, tarzana_mixed_road_scenario: false }), []);
  assert.deepEqual(inspectionForView(records, new Map(), 'D', 'modeled', toggles), []);
});

test('missing, non-finite, and exact sentinel values never become low results', () => {
  for (const value of [null, undefined, '', '50', NaN, Infinity, -Infinity, -99]) assert.equal(receiverValue(value), null);
  assert.equal(receiverValue(0), 0);
  assert.equal(receiverValue(-100), -100); // Do not broaden the exact -99 sentinel rule.
});

test('mobile fit leaves usable horizontal and vertical space', () => {
  for (const [width, height] of [[320, 568], [390, 844], [640, 800], [1440, 1000]]) {
    const padding = fitPadding(width, height);
    assert.ok(width - padding.left - padding.right >= 200);
    assert.ok(height - padding.top - padding.bottom >= 150);
  }
});
