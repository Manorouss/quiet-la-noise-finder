import { promises as fs } from 'node:fs';
import path from 'node:path';
import {
  assertFrameworkOrAllowedPath,
  assertNoCredentialRows,
  compactRows,
  enumerateRegularTree,
  sha256,
} from './export-profile-policy.mjs';
import { verifyStagedRoot } from './verify-staged-local-data.mjs';
import { verifyDenseLocalData } from './stage-dense-local-data.mjs';
import { verifyContextLocalData } from './stage-context-local-data.mjs';
import { verifyMapRuntime } from './stage-map-runtime.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const defaultRoot = path.join(appRoot, 'out');
const sourceManifestPath = path.join(appRoot, 'src/data/local-source-manifest.json');

export async function verifyLocalProfileExport(root = defaultRoot) {
  await verifyMapRuntime(path.join(root, 'maplibre'));
  const localRoot = path.join(root, '_local-data');
  await verifyStagedRoot(localRoot, sourceManifestPath, { ignoreRootEntries: ['dense', 'context'] });
  const denseResult = await verifyDenseLocalData(path.join(localRoot, 'dense'));
  const contextResult = await verifyContextLocalData(path.join(localRoot, 'context'));
  const contextManifest = JSON.parse(await fs.readFile(path.join(appRoot, 'src/data/map-context-source-manifest.json'), 'utf8'));
  const sourceManifest = JSON.parse(await fs.readFile(sourceManifestPath, 'utf8'));
  const pilotRoot = path.join(localRoot, 'v3/pilot');
  const pilotManifestPath = path.join(pilotRoot, 'staged-manifest.json');
  const pilotManifest = JSON.parse(await fs.readFile(pilotManifestPath, 'utf8').catch(() => { throw new Error('local pilot stage is missing from the export'); }));
  if (pilotManifest.schema !== 'quiet_la_tarzana_pilot_local_stage_v1' || pilotManifest.model !== 'tarzana-pilot-r02-c03-freeway-v1' || !Array.isArray(pilotManifest.rows) || pilotManifest.rows.length !== 3) throw new Error('local pilot stage manifest is not the admitted package');
  for (const row of pilotManifest.rows) {
    const bytes = await fs.readFile(path.join(pilotRoot, row.target)).catch(() => { throw new Error(`local pilot asset missing: ${row.target}`); });
    if (bytes.length !== row.bytes || sha256(bytes) !== row.sha256) throw new Error(`local pilot asset hash/size drift: ${row.target}`);
  }
  const allowed = new Set(['_local-data/staged-manifest.json', '_local-data/dense/dense-stage-manifest.json', ...sourceManifest.rows.map((row) => `_local-data/${row.target}`), ...denseResult.rows.map((row) => `_local-data/dense/${row.path}`), ...contextManifest.rows.map((row) => `_local-data/context/${row.target}`), ...((pilotManifest.rows ?? []).map((row) => `_local-data/v3/pilot/${row.target}`)), '_local-data/v3/pilot/staged-manifest.json', 'pilot/index.html', 'pilot/index.txt']);
  const rows = await enumerateRegularTree(root);
  for (const row of rows) {
    if (row.path.startsWith('_preview-data/')) throw new Error(`preview payload leaked into local export: ${row.path}`);
    assertFrameworkOrAllowedPath(row.path, allowed);
  }
  assertNoCredentialRows(rows);
  const html = await fs.readFile(path.join(root, 'index.html'), 'utf8');
  if (!html.includes('data-quiet-workspace="dense-tarzana-v21"') || !html.includes('Opening Quiet LA')) throw new Error('local portal identity drift in export');
  if (!html.includes('noindex,nofollow,noarchive')) throw new Error('local export lacks noindex meta');
  const compact = compactRows(rows);
  const serialized = Buffer.from(`${JSON.stringify(compact)}\n`);
  return { profile: 'local_v3', files: compact.length, bytes: compact.reduce((sum, row) => sum + row.bytes, 0), contextFeatures: contextResult.features, treeSha256: sha256(serialized), rows: compact };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  verifyLocalProfileExport(process.argv[2] ? path.resolve(process.argv[2]) : defaultRoot)
    .then((result) => {
      const summary = { ...result };
      delete summary.rows;
      console.log(JSON.stringify({ status: 'LOCAL_PROFILE_EXPORT_VALID', ...summary }, null, 2));
    })
    .catch((error) => { console.error(`verify-local-profile-export: ${error.message}`); process.exitCode = 1; });
}
