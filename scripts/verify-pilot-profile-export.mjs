import { promises as fs } from 'node:fs';
import path from 'node:path';
import {
  assertFrameworkOrAllowedPath,
  assertNoCredentialRows,
  compactRows,
  enumerateRegularTree,
  sha256,
} from './export-profile-policy.mjs';
import { verifyMapRuntime } from './stage-map-runtime.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const defaultRoot = path.join(appRoot, 'out');
const expectedPilotRows = Object.freeze([
  { source: 'benchmark.geojson', target: 'benchmark.geojson', sha256: '16df6aaf99abaf0a791e5de95a49a5eda5760851994fd4636465086cffd59c93', bytes: 2754559 },
  { source: 'buildings.geojson', target: 'buildings.geojson', sha256: 'b1f25db888ebb749751fc9b4beb2a2ab93bb6be28e23e273243e03d1ef937bd5', bytes: 102267 },
  { source: 'build-manifest.json', target: 'build-manifest.json', sha256: 'd733b7e11af379b2a31a1cda7559481ef5d7a175464c5d3b92c5c273c2c15769', bytes: 496 },
]);

async function verifyPilotStage(root) {
  const pilotRoot = path.join(root, '_local-data/v3/pilot');
  const manifestPath = path.join(pilotRoot, 'staged-manifest.json');
  const manifest = JSON.parse(await fs.readFile(manifestPath).catch(() => { throw new Error('pilot stage is missing from the export'); }));
  if (manifest.schema !== 'quiet_la_tarzana_pilot_local_stage_v1' || manifest.model !== 'tarzana-pilot-r02-c03-freeway-v1' || JSON.stringify(manifest.rows) !== JSON.stringify(expectedPilotRows)) throw new Error('pilot stage manifest is not the admitted exact package');
  for (const row of expectedPilotRows) {
    const bytes = await fs.readFile(path.join(pilotRoot, row.target)).catch(() => { throw new Error(`pilot asset missing: ${row.target}`); });
    if (bytes.length !== row.bytes || sha256(bytes) !== row.sha256) throw new Error(`pilot asset hash/size drift: ${row.target}`);
    if (row.target === 'build-manifest.json') {
      const publicManifest = JSON.parse(bytes.toString('utf8'));
      const forbiddenPathKeys = ['source_csv', 'source_receivers', 'source_buildings'];
      if (forbiddenPathKeys.some((key) => Object.hasOwn(publicManifest, key)) || /\/(?:Users|private)\//.test(bytes.toString('utf8'))) throw new Error('private filesystem path leaked through pilot manifest');
      if (publicManifest.receiver_count !== 6421 || publicManifest.numeric_rows !== 19257 || publicManifest.building_count !== 80 || publicManifest.facade_receiver_count !== 1573 || JSON.stringify(publicManifest.masked_ids) !== '[85715,88627]') throw new Error('public pilot manifest counts/masks drift');
    }
  }
  return { files: expectedPilotRows.length + 1, bytes: expectedPilotRows.reduce((sum, row) => sum + row.bytes, Buffer.byteLength(JSON.stringify(manifest, null, 2) + '\n')), rows: expectedPilotRows };
}

export async function verifyPilotProfileExport(root = defaultRoot) {
  const mapRuntime = await verifyMapRuntime(path.join(root, 'maplibre'));
  const pilot = await verifyPilotStage(root);
  const allowed = new Set([
    '_local-data/v3/pilot/staged-manifest.json',
    ...expectedPilotRows.map((row) => `_local-data/v3/pilot/${row.target}`),
    'pilot/index.html',
    'pilot/index.txt',
  ]);
  const rows = await enumerateRegularTree(root);
  for (const row of rows) {
    if (row.path.startsWith('_local-data/') && !row.path.startsWith('_local-data/v3/pilot/')) throw new Error(`non-pilot local payload leaked into pilot export: ${row.path}`);
    if (row.path.startsWith('_preview-data/')) throw new Error(`preview payload leaked into pilot export: ${row.path}`);
    assertFrameworkOrAllowedPath(row.path, allowed);
  }
  assertNoCredentialRows(rows);
  for (const route of ['index.html', 'pilot/index.html']) {
    const html = await fs.readFile(path.join(root, route), 'utf8');
    if (!html.includes('Tarzana combined-road pilot') || !html.includes('80 modeled buildings')) throw new Error(`pilot identity/disclosure missing from ${route}`);
    if (!html.includes('noindex,nofollow,noarchive')) throw new Error(`pilot export lacks noindex meta in ${route}`);
  }
  const compact = compactRows(rows);
  const serialized = Buffer.from(`${JSON.stringify(compact)}\n`);
  return { profile: 'pilot_v1', files: compact.length, bytes: compact.reduce((sum, row) => sum + row.bytes, 0), treeSha256: sha256(serialized), pilot, mapRuntime, rows: compact };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  verifyPilotProfileExport(process.argv[2] ? path.resolve(process.argv[2]) : defaultRoot)
    .then((result) => { const summary = { ...result }; delete summary.rows; console.log(JSON.stringify({ status: 'PILOT_PROFILE_EXPORT_VALID', ...summary }, null, 2)); })
    .catch((error) => { console.error(`verify-pilot-profile-export: ${error.message}`); process.exitCode = 1; });
}
