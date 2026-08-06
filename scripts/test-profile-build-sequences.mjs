import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { verifyExternalPayloadFreeExport } from './verify-external-payload-free-export.mjs';
import { verifyLocalProfileExport } from './verify-local-profile-export.mjs';
import { verifyPrivatePreviewExport } from './verify-private-preview-export.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const outRoot = path.join(appRoot, 'out');
const scripts = {
  local: 'scripts/build-local-profile.mjs',
  external: 'scripts/build-external-payload-free.mjs',
  private: 'scripts/build-private-preview.mjs',
};
const verifiers = {
  local: verifyLocalProfileExport,
  external: verifyExternalPayloadFreeExport,
  private: verifyPrivatePreviewExport,
};

async function run(script) {
  let output = '';
  const code = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], { cwd: appRoot, env: process.env, stdio: ['ignore', 'pipe', 'pipe'] });
    child.stdout.on('data', (chunk) => { output += chunk.toString(); });
    child.stderr.on('data', (chunk) => { output += chunk.toString(); });
    child.once('error', reject);
    child.once('close', resolve);
  });
  if (code !== 0) throw new Error(`${script} exited ${code}: ${output.slice(-4000)}`);
}

function assertFinalProfile(profile, rows) {
  const local = rows.filter((row) => row.path.startsWith('_local-data/'));
  const preview = rows.filter((row) => row.path.startsWith('_preview-data/'));
  if (profile === 'local' && (local.length === 0 || preview.length !== 0)) throw new Error('local final export tree is cross-profile contaminated');
  if (profile === 'private' && (preview.length === 0 || local.length !== 0)) throw new Error('private final export tree is cross-profile contaminated');
  if (profile === 'external' && (preview.length !== 0 || local.length !== 0)) throw new Error('external final export tree is cross-profile contaminated');
}

const sequences = [
  ['local', 'external'],
  ['local', 'private'],
  ['private', 'external'],
  ['external', 'local'],
];
const results = [];
await run('scripts/build-clean-manifest.mjs');
await run('scripts/stage-local-data.mjs');
await run('scripts/stage-private-preview.mjs');
for (const [first, second] of sequences) {
  await run(scripts[first]);
  await run(scripts[second]);
  const result = await verifiers[second](outRoot);
  assertFinalProfile(second, result.rows);
  results.push({ sequence: `${first}->${second}`, finalProfile: result.profile, files: result.files, bytes: result.bytes, treeSha256: result.treeSha256 ?? result.manifestSha256 });
}
const report = { schema: 'quiet_la_cross_profile_export_sequence_test_v1', status: 'PASS', sequences: results };
await fs.mkdir(path.join(appRoot, 'release'), { recursive: true });
await fs.writeFile(path.join(appRoot, 'release/PROFILE_SEQUENCE_TEST.json'), `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify(report, null, 2));
