import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { initialLayerToggles, resolveRuntimeProfile, scientificAssetUrl } from '../src/lib/runtime-profile.js';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const layerIds = ['four', 'tarzana', 'airport', 'source341'];

test('unset and unknown profiles fail closed to the payload-free external shell', () => {
  for (const value of [undefined, '', 'production', 'local_typo', '../_local-data']) {
    const profile = resolveRuntimeProfile(value);
    assert.equal(profile.id, 'external_payload_free_v1');
    assert.equal(profile.dataRoot, null);
    assert.equal(profile.scientificPayloadsAvailable, false);
    assert.equal(profile.developerRecoveryAllowed, false);
    assert.deepEqual(initialLayerToggles(profile, layerIds), { four: false, tarzana: false, airport: false, source341: false });
    assert.throws(() => scientificAssetUrl(profile, 'four-region-relative.json.gz'), /scientific payload requests are disabled/);
  }
});

test('local and private-preview roots cannot cross or escape their profile', () => {
  const local = resolveRuntimeProfile('local_v3');
  const preview = resolveRuntimeProfile('private_preview_v1');
  assert.equal(scientificAssetUrl(local, 'four-region-relative.json.gz'), '/_local-data/v3/four-region-relative.json.gz');
  assert.equal(scientificAssetUrl(preview, 'admitted/example.json'), '/_preview-data/v3/admitted/example.json');
  assert.deepEqual(initialLayerToggles(local, layerIds), { four: true, tarzana: true, airport: true, source341: true });
  assert.deepEqual(initialLayerToggles(preview, layerIds), { four: false, tarzana: false, airport: false, source341: false });
  for (const unsafe of ['/absolute.json', '../escape.json', 'https://example.com/value.json']) {
    assert.throws(() => scientificAssetUrl(local, unsafe), /not a safe same-origin relative path/);
    assert.throws(() => scientificAssetUrl(preview, unsafe), /not a safe same-origin relative path/);
  }
});

test('production UI source has no embedded local recovery command and Vercel routing is profile-explicit', async () => {
  const page = await fs.readFile(path.join(appRoot, 'src/app/page.tsx'), 'utf8');
  assert.doesNotMatch(page, /npm run stage:local|Map awaiting local v3 data/);
  assert.match(page, /Scientific private preview is not published on this URL/);
  assert.match(page, /initialLayerToggles\(runtimeProfile/);
  assert.match(page, /loadState === 'error' && localProfile/);
  assert.match(page, /runtimeProfile\.developerRecoveryAllowed && localRecoveryCommand/);
  assert.match(page, /NEXT_PUBLIC_QUIET_LA_LOCAL_RECOVERY_COMMAND/);
  const localRunner = await fs.readFile(path.join(appRoot, 'scripts/run-local-next.mjs'), 'utf8');
  assert.match(localRunner, /NEXT_PUBLIC_QUIET_LA_DATA_PROFILE: 'local_v3'/);
  assert.match(localRunner, /NEXT_PUBLIC_QUIET_LA_LOCAL_RECOVERY_COMMAND: 'npm run stage:local'/);
  const externalBuilder = await fs.readFile(path.join(appRoot, 'scripts/build-external-payload-free.mjs'), 'utf8');
  assert.match(externalBuilder, /NEXT_PUBLIC_QUIET_LA_DATA_PROFILE: 'external_payload_free_v1'/);
  assert.match(externalBuilder, /payloadDirectories: 0/);
  const vercel = JSON.parse(await fs.readFile(path.join(appRoot, 'vercel.json'), 'utf8'));
  assert.equal(vercel.buildCommand, 'npm run build:vercel-profile');
  const router = await fs.readFile(path.join(appRoot, 'scripts/build-vercel-profile.mjs'), 'utf8');
  assert.match(router, /environment === 'production'.*build:external/s);
  assert.match(router, /environment === 'preview'.*branch === 'private-preview'.*build:private-preview/s);
  const packageJson = JSON.parse(await fs.readFile(path.join(appRoot, 'package.json'), 'utf8'));
  assert.equal(packageJson.scripts['verify:external'], 'node scripts/verify-external-payload-free-export.mjs');
  assert.equal(packageJson.scripts['verify:external-source'], 'node scripts/verify-external-tree.mjs');
  assert.notEqual(packageJson.scripts['build:external'], packageJson.scripts['verify:external-source']);
});

test('external profile policy copy is clean and does not claim active scientific state', async () => {
  const page = await fs.readFile(path.join(appRoot, 'src/app/page.tsx'), 'utf8');
  assert.match(page, /Payload-free external shell/);
  assert.match(page, /No scientific or context layer is active/);
  assert.match(page, /payloadUnavailable && unavailableLayers/);
  const map = await fs.readFile(path.join(appRoot, 'src/components/MapCanvas.tsx'), 'utf8');
  assert.match(map, /runtimeMode === 'external_payload_free'/);
  assert.doesNotMatch(map, /Private preview shell · scientific payloads pending rights|Map awaiting accepted v3 data/);
});
