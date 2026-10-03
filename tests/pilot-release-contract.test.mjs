import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { expectedBuildingTileIds, mergePilotTiles, normalizePilotTile, normalizeSavedBuilding, savedBuildingRecord, tileIntersectsBounds } from '../src/lib/pilot-release.js';

const contract = JSON.parse(await readFile(new URL('../src/data/pilot-release-contract.json', import.meta.url), 'utf8'));

test('one combined study contract retains c03 legacy default and admits c02 expansion tile', () => {
  assert.equal(contract.study_id, 'tarzana-combined-road-study-r02-v1');
  assert.equal(contract.default_tile_id, 'r02-c03');
  const [c03, c02] = contract.tiles;
  assert.equal(c03.status, 'accepted_legacy_default');
  assert.equal(c02.status, 'accepted_expansion');
  assert.deepEqual(c02.masked_ids, []);
  assert.equal(c03.masked_ids.length, 2);
  assert.equal(contract.shared_assumptions.physics_contract_sha256.length, 64);
  assert.equal(contract.shared_assumptions.source_contract_sha256.length, 64);
  assert.equal(c02.provenance['cross_tile_receiver_coordinate_duplicates_at_1e-7_degrees'], 0);
  assert.equal(c02.provenance.cross_tile_duplicate_building_source_ids.length, 2);
  assert.equal(c02.assets.reduce((total, asset) => total + asset.bytes, 0), 5765028);
});

test('viewport intersection loads neighboring study tiles only when their extent is visible', () => {
  const c02 = contract.tiles.find((tile) => tile.tile_id === 'r02-c02');
  assert.equal(tileIntersectsBounds(c02.bbox_wgs84, { west: -118.58, south: 34.16, east: -118.57, north: 34.18 }), true);
  assert.equal(tileIntersectsBounds(c02.bbox_wgs84, { west: -118.56, south: 34.16, east: -118.55, north: 34.18 }), false);
});

test('stable-key merge collapses the same building and unions only its distinct tile samples', () => {
  const polygon = { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] };
  const receiver = (tile, id, buildingKey, values) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [id, 0] }, properties: { id, receiver_key: `${tile}:source-${id}`, building_key: buildingKey, masked: false, D: { laeq: values[0] }, E: { laeq: values[1] }, N: { laeq: values[2] } } });
  const building = (tile, id, key, receiverKey, value) => ({ type: 'Feature', geometry: polygon, properties: { building_pk: id, building_key: key, source_bld_id: key.slice(7), receiver_keys: [receiverKey], receiver_ids: [id], height_m: 8, periods: { D: { min: value, max: value, receiver_count: 1, unavailable_count: 0 }, E: { min: value, max: value, receiver_count: 1, unavailable_count: 0 }, N: { min: value, max: value, receiver_count: 1, unavailable_count: 0 } } } });
  const a = normalizePilotTile('r02-c03', { features: [receiver('r02-c03', 101, 'lariac:shared', [60, 55, 50])] }, { features: [building('r02-c03', 1, 'lariac:shared', 'r02-c03:source-101', 60)] });
  const b = normalizePilotTile('r02-c02', { features: [receiver('r02-c02', 202, 'lariac:shared', [62, 57, 52])] }, { features: [building('r02-c02', 2, 'lariac:shared', 'r02-c02:source-202', 62)] });
  const combined = mergePilotTiles([a, b]);
  assert.equal(combined.receivers.length, 2);
  assert.equal(combined.buildings.length, 1);
  assert.deepEqual(combined.buildings[0].properties.source_tile_ids, ['r02-c02', 'r02-c03']);
  assert.deepEqual(combined.buildings[0].properties.receiver_keys, ['r02-c02:source-202', 'r02-c03:source-101']);
  assert.equal(combined.buildings[0].properties.periods.D.min, 60);
  assert.equal(combined.buildings[0].properties.periods.D.max, 62);
});

test('seam buildings declare both tile owners so their range is never silently partial', () => {
  assert.deepEqual(expectedBuildingTileIds({ properties: { source_bld_id: '389949885603', source_tile_ids: ['r02-c03'] } }, contract.tiles, contract.default_tile_id), ['r02-c02', 'r02-c03']);
  assert.deepEqual(expectedBuildingTileIds({ properties: { source_bld_id: 'unshared', source_tile_ids: ['r02-c02'] } }, contract.tiles, contract.default_tile_id), ['r02-c02']);
});

test('saved buildings retain stable identity and loadable tile ownership across reloads', () => {
  const record = savedBuildingRecord({ properties: { building_pk: 2068, building_key: 'lariac:387292885106', source_bld_id: '387292885106', source_tile_ids: ['r02-c02'] } }, 'tarzana-pilot-r02-c03-freeway-v1');
  assert.deepEqual(record.sourceTileIds, ['r02-c02']);
  assert.equal(record.buildingKey, 'lariac:387292885106');
  assert.deepEqual(normalizeSavedBuilding(record, 'r02-c03', ['r02-c03', 'r02-c02']), record);
  const oldRecord = normalizeSavedBuilding({ buildingPk: 71728, sourceBldId: 'legacy-source', model: 'tarzana-pilot-r02-c03-freeway-v1' }, 'r02-c03', ['r02-c03', 'r02-c02']);
  assert.equal(oldRecord.buildingKey, 'lariac:legacy-source');
  assert.deepEqual(oldRecord.sourceTileIds, ['r02-c03']);
  assert.equal(normalizeSavedBuilding({ buildingPk: '2068' }, 'r02-c03', ['r02-c03', 'r02-c02']), null);
});

test('stable receiver keys cannot alias across tiles', () => {
  const a = normalizePilotTile('r02-c03', { features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: { id: 7 } }] }, { features: [] });
  const b = normalizePilotTile('r02-c02', { features: [{ type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: { id: 7 } }] }, { features: [] });
  assert.notEqual(a.receivers[0].properties.receiver_key, b.receivers[0].properties.receiver_key);
  assert.throws(() => mergePilotTiles([a, { ...b, receivers: [{ ...b.receivers[0], properties: { ...b.receivers[0].properties, receiver_key: a.receivers[0].properties.receiver_key } }] }]), /duplicate stable receiver key/);
});
