import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { verifyDenseLocalData } from '../scripts/stage-dense-local-data.mjs';
import { stageContextLocalData, verifyContextLocalData } from '../scripts/stage-context-local-data.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const denseRoot = path.join(appRoot, 'public/_local-data/dense');

test('dense local recovery stage matches both canonical package manifests', async () => {
  const result = await verifyDenseLocalData(denseRoot);
  assert.equal(result.files, 99);
  assert.equal(result.rows.filter((row) => row.path.startsWith('dense/shards/')).length, 30);
  assert.equal(result.rows.filter((row) => row.path.endsWith('.webp')).length, 18);
  assert.equal(result.rows.filter((row) => row.path.startsWith('scene3d/terrain/')).length, 30);
  assert.equal(result.rows.filter((row) => row.path.startsWith('scene3d/buildings/')).length, 15);
});

test('dense local verifier rejects missing, tampered, and symlinked payloads', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-dense-local-'));
  const copy = path.join(temp, 'dense');
  try {
    await fs.cp(denseRoot, copy, { recursive: true, dereference: false });
    const target = path.join(copy, 'dense/overview.json.gz');
    const original = await fs.readFile(target);
    await fs.appendFile(target, Buffer.from('\n tamper'));
    await assert.rejects(() => verifyDenseLocalData(copy), /hash\/size drift/);

    await fs.writeFile(target, original);
    await fs.rm(target);
    await assert.rejects(() => verifyDenseLocalData(copy), /file set/);

    await fs.writeFile(target, original);
    await fs.rm(target);
    await fs.symlink(path.join(appRoot, 'src/data/layer-contract.json'), target);
    await assert.rejects(() => verifyDenseLocalData(copy), /symlink/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('context layers are staged as hash-bound regular files with separate inventories', async () => {
  const result = await verifyContextLocalData(path.join(appRoot, 'public/_local-data/context'));
  assert.equal(result.files, 4);
  assert.equal(result.features, 525);
  await stageContextLocalData();
});
