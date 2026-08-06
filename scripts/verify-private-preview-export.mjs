import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { assertSafePreviewPath, hasCredentialLikeContent } from './preview-security-policy.mjs';
import { verifyPrivatePreviewRoot } from './verify-private-preview-bundle.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const defaultExportRoot = path.join(appRoot, 'out');
const payloadContractPath = path.join(appRoot, 'src/data/private-preview-payload-contract.json');
const manifestPath = path.join(appRoot, 'release/PRIVATE_PREVIEW_EXPORT_MANIFEST.json');
const exactFrameworkPaths = new Set(['404.html', '404/index.html', 'index.html', 'index.txt', 'robots.txt']);
const frameworkPatterns = [
  /^_next\/static\/qlp-[a-f0-9]{24}\/_buildManifest\.js$/,
  /^_next\/static\/qlp-[a-f0-9]{24}\/_ssgManifest\.js$/,
  /^_next\/static\/chunks\/[A-Za-z0-9._/-]+\.js$/,
  /^_next\/static\/css\/[a-f0-9]+\.css$/,
];

export function assertAllowedPrivatePreviewExportPath(relative, allowedPreviewPaths = new Set()) {
  assertSafePreviewPath(relative);
  const allowed = exactFrameworkPaths.has(relative) || allowedPreviewPaths.has(relative) || frameworkPatterns.some((pattern) => pattern.test(relative));
  if (!allowed) throw new Error(`unexpected private-preview exported file: ${relative}`);
}

function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
async function lstatOrNull(target) { return fs.lstat(target).catch(() => null); }
async function walk(root, current = root) {
  const rows = [];
  for (const entry of await fs.readdir(current, { withFileTypes: true })) {
    const target = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`symlink exported entry: ${target}`);
    if (entry.isDirectory()) rows.push(...await walk(root, target));
    else {
      const bytes = await fs.readFile(target);
      rows.push({ path: path.relative(root, target).split(path.sep).join('/'), bytes: bytes.length, sha256: sha256(bytes), content: bytes });
    }
  }
  return rows;
}

export async function verifyPrivatePreviewExport(exportRoot = defaultExportRoot, { writeManifest = false } = {}) {
  const stat = await lstatOrNull(exportRoot);
  if (!stat?.isDirectory() || stat.isSymbolicLink()) throw new Error('Next export root is missing or not a regular directory');
  const contract = JSON.parse(await fs.readFile(payloadContractPath, 'utf8'));
  await verifyPrivatePreviewRoot(path.join(exportRoot, '_preview-data'));
  if (await lstatOrNull(path.join(exportRoot, '_local-data'))) throw new Error('local-only data leaked into private preview export');
  const rowsWithContent = (await walk(exportRoot)).sort((a, b) => a.path.localeCompare(b.path));
  const allowedPreviewPaths = new Set(['_preview-data/preview-payload-manifest.json', ...contract.rows.map((row) => `_preview-data/${row.target}`)]);
  const total = rowsWithContent.reduce((sum, row) => sum + row.bytes, 0);
  if (total > 20000000) throw new Error(`private preview export exceeds 20 MB cap: ${total}`);
  for (const row of rowsWithContent) {
    assertAllowedPrivatePreviewExportPath(row.path, allowedPreviewPaths);
    if (hasCredentialLikeContent(row.content)) throw new Error(`credential-like content in private-preview export: ${row.path}`);
  }
  const html = await fs.readFile(path.join(exportRoot, 'index.html'), 'utf8');
  if (!html.includes('noindex,nofollow,noarchive')) throw new Error('exported index lacks exact robots meta');
  if (!html.includes('Protected preview workspace') || !html.includes('Protected preview shell only. No scientific or context payload is admitted.')) {
    throw new Error('private-preview portal identity or payload boundary drift in exported index');
  }
  if (/stage:local|npm run|Map awaiting local v3 data/i.test(html)) throw new Error('local developer recovery text leaked into private-preview export');
  const robots = await fs.readFile(path.join(exportRoot, 'robots.txt'), 'utf8');
  if (!/^User-agent: \*\s+Disallow: \/\s*$/m.test(robots)) throw new Error('robots.txt is not fail-closed');
  const buildIdRows = rowsWithContent.filter((row) => /^_next\/static\/qlp-[a-f0-9]{24}\//.test(row.path));
  if (buildIdRows.length !== 2) throw new Error(`deterministic private-preview build ID is missing or ambiguous: ${buildIdRows.length}`);
  const buildId = buildIdRows[0].path.split('/')[2];
  const rows = rowsWithContent.map((row) => ({ path: row.path, bytes: row.bytes, sha256: row.sha256 }));
  const manifest = { schema: 'quiet_la_private_preview_export_manifest_v1', profile: 'private_preview_v1', buildId, payloadBytes: 0, exportBytes: total, rows };
  const serialized = `${JSON.stringify(manifest, null, 2)}\n`;
  const result = { profile: 'private_preview_v1', files: rows.length, bytes: total, payloadBytes: 0, buildId, manifestSha256: sha256(Buffer.from(serialized)), rows };
  if (writeManifest) {
    await fs.mkdir(path.dirname(manifestPath), { recursive: true });
    await fs.writeFile(manifestPath, serialized);
  }
  return result;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const exportRoot = process.argv[2] ? path.resolve(process.argv[2]) : defaultExportRoot;
  verifyPrivatePreviewExport(exportRoot, { writeManifest: exportRoot === defaultExportRoot }).then((result) => {
    const summary = { ...result };
    delete summary.rows;
    console.log(JSON.stringify({ status: 'PRIVATE_PREVIEW_EXPORT_VALID', ...summary, manifest: exportRoot === defaultExportRoot ? path.relative(appRoot, manifestPath) : null }, null, 2));
  }).catch((error) => { console.error(`verify-private-preview-export: ${error.message}`); process.exitCode = 1; });
}
