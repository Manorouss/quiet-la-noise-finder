import { promises as fs } from 'node:fs';
import path from 'node:path';
import {
  assertFrameworkOrAllowedPath,
  assertNoCredentialRows,
  compactRows,
  enumerateRegularTree,
  sha256,
} from './export-profile-policy.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const defaultRoot = path.join(appRoot, 'out');

export async function verifyExternalPayloadFreeExport(root = defaultRoot) {
  const rows = await enumerateRegularTree(root);
  for (const row of rows) {
    if (row.path.startsWith('_local-data/') || row.path.startsWith('_preview-data/')) throw new Error(`scientific payload leaked into external export: ${row.path}`);
    assertFrameworkOrAllowedPath(row.path);
  }
  assertNoCredentialRows(rows);
  const html = await fs.readFile(path.join(root, 'index.html'), 'utf8');
  if (!html.includes('Scientific private preview is not published on this URL')) throw new Error('external shell policy status missing from export');
  if (!html.includes('Payload-free public shell')) throw new Error('external shell identity missing from export');
  if (!html.includes('noindex,nofollow,noarchive')) throw new Error('external export lacks noindex meta');
  if (/stage:local|stage:private-preview|npm run|Map awaiting local v3 data/i.test(html)) throw new Error('local developer recovery text leaked into external export');
  const robots = await fs.readFile(path.join(root, 'robots.txt'), 'utf8');
  if (!/^User-agent: \*\s+Disallow: \/\s*$/m.test(robots)) throw new Error('external robots.txt is not fail-closed');
  const compact = compactRows(rows);
  const serialized = Buffer.from(`${JSON.stringify(compact)}\n`);
  return { profile: 'external_payload_free_v1', files: compact.length, bytes: compact.reduce((sum, row) => sum + row.bytes, 0), treeSha256: sha256(serialized), rows: compact };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  verifyExternalPayloadFreeExport(process.argv[2] ? path.resolve(process.argv[2]) : defaultRoot)
    .then((result) => {
      const summary = { ...result };
      delete summary.rows;
      console.log(JSON.stringify({ status: 'EXTERNAL_PAYLOAD_FREE_EXPORT_VALID', ...summary }, null, 2));
    })
    .catch((error) => { console.error(`verify-external-payload-free-export: ${error.message}`); process.exitCode = 1; });
}
