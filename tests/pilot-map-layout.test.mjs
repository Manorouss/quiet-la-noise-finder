import assert from 'node:assert/strict';
import test from 'node:test';
import { isPointInPaddedMapViewport, pilotMapPadding } from '../src/lib/pilot-map-layout.js';

test('mobile bottom sheet shifts map focus above the actual panel height', () => {
  const host = { left: 0, top: 0, right: 390, bottom: 844, width: 390, height: 844 };
  const panel = { left: 10, top: 313, right: 380, bottom: 834, width: 370, height: 521 };
  assert.deepEqual(pilotMapPadding(host, panel), { top: 24, right: 24, bottom: 555, left: 24 });
});

test('desktop side panel shifts map focus left by the panel width', () => {
  const host = { left: 0, top: 0, right: 1400, bottom: 900, width: 1400, height: 900 };
  const panel = { left: 1014, top: 16, right: 1384, bottom: 884, width: 370, height: 868 };
  assert.deepEqual(pilotMapPadding(host, panel), { top: 24, right: 410, bottom: 24, left: 24 });
});

test('non-overlapping controls do not change the usable map viewport', () => {
  const host = { left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600 };
  const panel = { left: 820, top: 10, right: 1100, bottom: 500, width: 280, height: 490 };
  assert.deepEqual(pilotMapPadding(host, panel), { top: 24, right: 24, bottom: 24, left: 24 });
});

test('visibility bounds use CSS pixels rather than the high-DPI canvas backing size', () => {
  const padding = { top: 24, right: 24, bottom: 555, left: 24 };
  const cssSize = { width: 390, height: 844 };
  assert.equal(isPointInPaddedMapViewport({ x: 370, y: 180 }, cssSize, padding), false);
  assert.equal(isPointInPaddedMapViewport({ x: 350, y: 180 }, cssSize, padding), true);
  // A backing-pixel comparison at devicePixelRatio=2 would incorrectly call the same point visible.
  assert.equal(isPointInPaddedMapViewport({ x: 370, y: 180 }, { width: 780, height: 1688 }, padding), true);
});
