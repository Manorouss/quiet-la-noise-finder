import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { verifyExternalPayloadFreeExport } from '../scripts/verify-external-payload-free-export.mjs';
import { verifyLocalProfileExport } from '../scripts/verify-local-profile-export.mjs';
import { publishVerifiedExport } from '../scripts/profile-export-io.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);

async function makeFrameworkFixture(root, html) {
  const buildId = 'fixture-build-id';
  await fs.mkdir(path.join(root, `_next/static/${buildId}`), { recursive: true });
  await fs.writeFile(path.join(root, 'index.html'), html);
  await fs.writeFile(path.join(root, 'robots.txt'), 'User-agent: *\nDisallow: /\n');
  await fs.writeFile(path.join(root, `_next/static/${buildId}/_buildManifest.js`), 'self.__BUILD_MANIFEST={}');
  await fs.writeFile(path.join(root, `_next/static/${buildId}/_ssgManifest.js`), 'self.__SSG_MANIFEST=new Set');
}

test('profile builders use a unique custom export directory and atomic out publication', async () => {
  const config = await fs.readFile(path.join(appRoot, 'next.config.mjs'), 'utf8');
  const io = await fs.readFile(path.join(appRoot, 'scripts/profile-export-io.mjs'), 'utf8');
  const local = await fs.readFile(path.join(appRoot, 'scripts/build-local-profile.mjs'), 'utf8');
  const external = await fs.readFile(path.join(appRoot, 'scripts/build-external-payload-free.mjs'), 'utf8');
  const preview = await fs.readFile(path.join(appRoot, 'scripts/build-private-preview.mjs'), 'utf8');
  assert.match(config, /isolatedExportDir[\s\S]*distDir: isolatedExportDir/);
  assert.match(io, /\.quiet-la-export-\$\{token\}-\$\{process\.pid\}-\$\{suffix\}/);
  assert.match(io, /await verify\(stageRoot\)[\s\S]*publishVerifiedExport/);
  assert.match(io, /rename\(targetOutRoot, backupRoot\)[\s\S]*rename\(stageRoot, targetOutRoot\)[\s\S]*verify\(targetOutRoot\)/);
  assert.match(io, /rename\(backupRoot, targetOutRoot\)/);
  assert.match(local, /excludeFromExport: \['_preview-data'\]/);
  assert.match(external, /excludeFromExport: \['_local-data', '_preview-data'\]/);
  assert.match(preview, /excludeFromExport: \['_local-data'\]/);
});

test('failed post-publication verification restores the previous exact out tree', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-atomic-export-'));
  try {
    const out = path.join(temp, 'out');
    const stage = path.join(temp, 'stage');
    await fs.mkdir(out);
    await fs.mkdir(stage);
    await fs.writeFile(path.join(out, 'old.txt'), 'old exact tree');
    await fs.writeFile(path.join(stage, 'new.txt'), 'new candidate');
    await assert.rejects(() => publishVerifiedExport(stage, async () => { throw new Error('post-publication verifier failed'); }, out), /post-publication verifier failed/);
    assert.equal(await fs.readFile(path.join(out, 'old.txt'), 'utf8'), 'old exact tree');
    assert.equal(await fs.lstat(path.join(out, 'new.txt')).then(() => true).catch(() => false), false);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('payload-free external export rejects both data roots, credentials, and symlinks', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-external-export-'));
  try {
    await makeFrameworkFixture(temp, '<meta name="robots" content="noindex,nofollow,noarchive"><p>Payload-free public shell</p><p>Scientific private preview is not published on this URL</p>');
    await verifyExternalPayloadFreeExport(temp);
    for (const root of ['_local-data', '_preview-data']) {
      await fs.mkdir(path.join(temp, root));
      await fs.writeFile(path.join(temp, root, 'payload.json'), '{}');
      await assert.rejects(() => verifyExternalPayloadFreeExport(temp), /scientific payload leaked|unexpected profile export/);
      await fs.rm(path.join(temp, root), { recursive: true });
    }
    await fs.mkdir(path.join(temp, '_next/static/chunks'), { recursive: true });
    const credential = path.join(temp, '_next/static/chunks/injected.js');
    const credentialName = ['VERCEL', 'TOKEN'].join('_');
    await fs.writeFile(credential, `const ${credentialName}="abcdefghijklmnopqrstuvwx";`);
    await assert.rejects(() => verifyExternalPayloadFreeExport(temp), /credential-like content/);
    await fs.rm(credential);
    const linked = path.join(temp, 'linked.js');
    await fs.symlink(path.join(temp, 'index.html'), linked);
    await assert.rejects(() => verifyExternalPayloadFreeExport(temp), /symlink exported entry/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('local export accepts only the exact local stage and rejects preview contamination', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-local-export-'));
  try {
    await makeFrameworkFixture(temp, '<meta name="robots" content="noindex,nofollow,noarchive"><p>Private model workspace</p><p>Active local layers</p>');
    await fs.cp(path.join(appRoot, 'public/_local-data'), path.join(temp, '_local-data'), { recursive: true, dereference: false });
    await verifyLocalProfileExport(temp);
    await fs.mkdir(path.join(temp, '_preview-data'));
    await fs.writeFile(path.join(temp, '_preview-data/preview-payload-manifest.json'), '{}');
    await assert.rejects(() => verifyLocalProfileExport(temp), /preview payload leaked|unexpected profile export/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('cross-profile sequence harness covers every required order', async () => {
  const source = await fs.readFile(path.join(appRoot, 'scripts/test-profile-build-sequences.mjs'), 'utf8');
  for (const sequence of ["['local', 'external']", "['local', 'private']", "['private', 'external']", "['external', 'local']"]) {
    assert.match(source, new RegExp(sequence.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
  assert.match(source, /assertFinalProfile\(second, result\.rows\)/);
});
