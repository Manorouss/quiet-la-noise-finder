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

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const defaultRoot = path.join(appRoot, 'out');
const sourceManifestPath = path.join(appRoot, 'src/data/local-source-manifest.json');

export async function verifyLocalProfileExport(root = defaultRoot) {
  const localRoot = path.join(root, '_local-data');
  await verifyStagedRoot(localRoot, sourceManifestPath);
  const sourceManifest = JSON.parse(await fs.readFile(sourceManifestPath, 'utf8'));
  const allowed = new Set(['_local-data/staged-manifest.json', ...sourceManifest.rows.map((row) => `_local-data/${row.target}`)]);
  const rows = await enumerateRegularTree(root);
  for (const row of rows) {
    if (row.path.startsWith('_preview-data/')) throw new Error(`preview payload leaked into local export: ${row.path}`);
    assertFrameworkOrAllowedPath(row.path, allowed);
  }
  assertNoCredentialRows(rows);
  const html = await fs.readFile(path.join(root, 'index.html'), 'utf8');
  if (!html.includes('Private model workspace') || !html.includes('Active local layers')) throw new Error('local portal identity drift in export');
  if (!html.includes('noindex,nofollow,noarchive')) throw new Error('local export lacks noindex meta');
  const compact = compactRows(rows);
  const serialized = Buffer.from(`${JSON.stringify(compact)}\n`);
  return { profile: 'local_v3', files: compact.length, bytes: compact.reduce((sum, row) => sum + row.bytes, 0), treeSha256: sha256(serialized), rows: compact };
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
