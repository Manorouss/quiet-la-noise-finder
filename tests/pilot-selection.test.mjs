import assert from 'node:assert/strict';
import test from 'node:test';
import { resolvePilotSelection } from '../src/lib/pilot-selection.js';

const buildings = new Map([[71728, {}], [90001, {}]]);
const receivers = new Map([
  [198418, { properties: { id: 198418, building_pk: 71728 } }],
  [200001, { properties: { id: 200001, building_pk: null } }],
]);

test('receiver selection overrides a mismatched building link', () => {
  assert.deepEqual(resolvePilotSelection(198418, 90001, receivers, buildings), {
    receiverId: 198418,
    buildingPk: 71728,
  });
});

test('open-space receiver selection clears building association', () => {
  assert.deepEqual(resolvePilotSelection(200001, 71728, receivers, buildings), {
    receiverId: 200001,
    buildingPk: null,
  });
});

test('standalone receiver selection remains visible without a building', () => {
  const selection = resolvePilotSelection(200001, null, receivers, buildings);
  assert.equal(selection.receiverId, 200001);
  assert.equal(selection.buildingPk, null);
});

test('invalid receiver is cleared while a valid building selection survives', () => {
  assert.deepEqual(resolvePilotSelection(999999, 71728, receivers, buildings), {
    receiverId: null,
    buildingPk: 71728,
  });
});
