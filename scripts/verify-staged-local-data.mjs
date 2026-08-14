import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

export function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }

const HASH = /^[0-9a-f]{64}$/;

export function validateSourceManifest(sourceManifest) {
  if (!sourceManifest || sourceManifest.schema !== 'quiet_la_web_local_source_manifest_v1' || sourceManifest.candidate !== 'unified_visual_preview_local_v3') throw new Error('unexpected local source manifest schema/candidate');
  if (typeof sourceManifest.sourceRoot !== 'string' || sourceManifest.sourceRoot.startsWith('/') || sourceManifest.sourceRoot.includes('..') || !sourceManifest.sourceRoot.endsWith('/unified-visual-preview-local-v3')) throw new Error('local source root is not the accepted v3 namespace');
  if (!Array.isArray(sourceManifest.rows) || sourceManifest.rows.length !== 10) throw new Error('local source manifest must contain the exact ten v3 rows');
  const sources = new Set();
  const targets = new Set();
  for (const row of sourceManifest.rows) {
    if (!row || typeof row !== 'object' || typeof row.source !== 'string' || typeof row.target !== 'string' || row.source.startsWith('/') || row.target.startsWith('/') || row.source.includes('..') || row.target.includes('..') || row.localOnly !== true || !Number.isSafeInteger(row.bytes) || row.bytes <= 0 || typeof row.sha256 !== 'string' || !HASH.test(row.sha256)) throw new Error('local source manifest row is malformed or widened');
    if (sources.has(row.source) || targets.has(row.target)) throw new Error('local source manifest rows must be unique');
    sources.add(row.source);
    targets.add(row.target);
  }
  return sourceManifest;
}

async function lstatOrNull(p) { return fs.lstat(p).catch(() => null); }
async function assertNoSymlinkChain(p) {
  let cursor = path.parse(p).root;
  for (const component of path.relative(cursor, p).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, component);
    const stat = await lstatOrNull(cursor);
    if (stat?.isSymbolicLink()) throw new Error(`symlink path component: ${cursor}`);
  }
}
async function walk(root, current = root) {
  const files = [];
  for (const entry of await fs.readdir(current, { withFileTypes: true })) {
    const p = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`symlink staged entry: ${p}`);
    if (entry.isDirectory()) files.push(...await walk(root, p));
    else if (entry.isFile()) files.push(path.relative(root, p).split(path.sep).join('/'));
    else throw new Error(`non-regular staged entry: ${p}`);
  }
  return files;
}

export async function verifyStagedRoot(root, sourceManifestPath) {
  await assertNoSymlinkChain(root);
  const rootStat = await lstatOrNull(root);
  if (!rootStat?.isDirectory() || rootStat.isSymbolicLink()) throw new Error('staged root is not a regular directory');
  const sourceManifest = validateSourceManifest(JSON.parse(await fs.readFile(sourceManifestPath, 'utf8')));
  const stagedManifestPath = path.join(root, 'staged-manifest.json');
  const staged = JSON.parse(await fs.readFile(stagedManifestPath, 'utf8'));
  if (staged.schema !== 'quiet_la_web_local_staged_manifest_v1') throw new Error('unexpected staged manifest schema');
  const expected = new Map(sourceManifest.rows.map((row) => [row.target, row]));
  const actualFiles = await walk(root);
  const expectedFiles = [...expected.keys(), 'staged-manifest.json'].sort();
  if (JSON.stringify(actualFiles.sort()) !== JSON.stringify(expectedFiles)) throw new Error('staged file set differs from source manifest');
  if (!Array.isArray(staged.rows) || staged.rows.length !== expected.size) throw new Error('staged row count differs from source manifest');
  for (const row of sourceManifest.rows) {
    const target = path.join(root, row.target);
    await assertNoSymlinkChain(target);
    const stat = await lstatOrNull(target);
    if (!stat?.isFile() || stat.isSymbolicLink()) throw new Error(`staged target is not a regular file: ${row.target}`);
    const bytes = await fs.readFile(target);
    if (bytes.length !== row.bytes || sha256(bytes) !== row.sha256) throw new Error(`staged hash/size drift: ${row.target}`);
    const stagedRow = staged.rows.find((candidate) => candidate.target === row.target);
    if (!stagedRow || stagedRow.sha256 !== row.sha256 || stagedRow.bytes !== row.bytes) throw new Error(`staged manifest drift: ${row.target}`);
  }
  return { files: expected.size, bytes: sourceManifest.rows.reduce((sum, row) => sum + row.bytes, 0) };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
  const root = process.argv[2] ? path.resolve(process.argv[2]) : path.join(appRoot, 'public/_local-data');
  verifyStagedRoot(root, path.join(appRoot, 'src/data/local-source-manifest.json')).then((result) => console.log(JSON.stringify({ status: 'STAGED_LOCAL_VALID', ...result }, null, 2))).catch((error) => { console.error(`verify-staged-local-data: ${error.message}`); process.exitCode = 1; });
}
