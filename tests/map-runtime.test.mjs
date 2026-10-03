import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { stageMapRuntime, verifyMapRuntime } from '../scripts/stage-map-runtime.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const runtimeNames = ['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs'];

async function makeAppFixture() {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-map-runtime-'));
  const dist = path.join(temp, 'node_modules/maplibre-gl/dist');
  await fs.mkdir(dist, { recursive: true });
  for (const name of runtimeNames) await fs.copyFile(path.join(appRoot, 'node_modules/maplibre-gl/dist', name), path.join(dist, name));
  await fs.mkdir(path.join(temp, 'public'), { recursive: true });
  return temp;
}

test('MapLibre runtime stages and verifies the exact installed sibling pair', async () => {
  const temp = await makeAppFixture();
  try {
    const result = await stageMapRuntime(temp);
    assert.equal(result.files, 2);
    assert.deepEqual((await fs.readdir(path.join(temp, 'public/maplibre'))).sort(), runtimeNames.sort());
    assert.deepEqual((await verifyMapRuntime(path.join(temp, 'public/maplibre'), temp)).files, 2);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('MapLibre runtime verifier rejects missing, extra, and tampered pair members', async () => {
  const temp = await makeAppFixture();
  try {
    await stageMapRuntime(temp);
    const runtime = path.join(temp, 'public/maplibre');
    await fs.rm(path.join(runtime, 'maplibre-gl-shared.mjs'));
    await assert.rejects(() => verifyMapRuntime(runtime, temp), /file set differs/);
    await stageMapRuntime(temp);
    await fs.appendFile(path.join(runtime, 'maplibre-gl-worker.mjs'), '\n// tampered\n');
    await assert.rejects(() => verifyMapRuntime(runtime, temp), /bytes differ/);
    await stageMapRuntime(temp);
    await fs.writeFile(path.join(runtime, 'unexpected.mjs'), '');
    await assert.rejects(() => verifyMapRuntime(runtime, temp), /file set differs/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('MapLibre runtime staging rejects symlinked runtime paths and missing installed modules', async () => {
  const temp = await makeAppFixture();
  try {
    await fs.symlink(path.join(temp, 'node_modules/maplibre-gl/dist'), path.join(temp, 'public/maplibre'));
    await assert.rejects(() => stageMapRuntime(temp), /symlink/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }

  const missing = await makeAppFixture();
  try {
    await fs.rm(path.join(missing, 'node_modules/maplibre-gl/dist/maplibre-gl-shared.mjs'));
    await assert.rejects(() => stageMapRuntime(missing), /regular non-symlink file/);
  } finally {
    await fs.rm(missing, { recursive: true, force: true });
  }
});
