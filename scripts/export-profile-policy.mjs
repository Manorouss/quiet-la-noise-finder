import { promises as fs } from 'node:fs';
import path from 'node:path';
import { createHash, randomBytes } from 'node:crypto';
import { hasCredentialLikeContent } from './preview-security-policy.mjs';

export const exactFrameworkPaths = new Set(['404.html', '404/index.html', 'index.html', 'index.txt', 'robots.txt']);
export const frameworkPatterns = [
  /^_next\/static\/[A-Za-z0-9_-]+\/_buildManifest\.js$/,
  /^_next\/static\/[A-Za-z0-9_-]+\/_ssgManifest\.js$/,
  /^_next\/static\/chunks\/[A-Za-z0-9._/-]+\.js$/,
  /^_next\/static\/css\/[a-f0-9]+\.css$/,
];

export function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

async function lstatOrNull(target) {
  return fs.lstat(target).catch(() => null);
}

export async function assertNoSymlinkChain(target) {
  const absolute = path.resolve(target);
  let cursor = path.parse(absolute).root;
  for (const component of path.relative(cursor, absolute).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, component);
    const stat = await lstatOrNull(cursor);
    if (stat?.isSymbolicLink()) throw new Error(`symlink path component rejected: ${cursor}`);
  }
}

export async function enumerateRegularTree(root, current = root) {
  await assertNoSymlinkChain(root);
  const rootStat = await lstatOrNull(root);
  if (!rootStat?.isDirectory() || rootStat.isSymbolicLink()) throw new Error(`export root is missing or unsafe: ${root}`);
  const rows = [];
  for (const entry of await fs.readdir(current, { withFileTypes: true })) {
    const target = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`symlink exported entry: ${target}`);
    if (entry.isDirectory()) rows.push(...await enumerateRegularTree(root, target));
    else {
      const content = await fs.readFile(target);
      rows.push({ path: path.relative(root, target).split(path.sep).join('/'), bytes: content.length, sha256: sha256(content), content });
    }
  }
  return rows.sort((left, right) => left.path.localeCompare(right.path));
}

export function assertFrameworkOrAllowedPath(relative, allowedDataPaths = new Set()) {
  if (relative.startsWith('/') || relative.includes('..') || relative.includes('\\')) throw new Error(`unsafe export path: ${relative}`);
  if (exactFrameworkPaths.has(relative) || allowedDataPaths.has(relative) || frameworkPatterns.some((pattern) => pattern.test(relative))) return;
  throw new Error(`unexpected profile export file: ${relative}`);
}

export function assertNoCredentialRows(rows) {
  for (const row of rows) if (hasCredentialLikeContent(row.content)) throw new Error(`credential-like content in profile export: ${row.path}`);
}

export function compactRows(rows) {
  return rows.map(({ path: relative, bytes, sha256: digest }) => ({ path: relative, bytes, sha256: digest }));
}

export async function writeCompleteExportManifest(appRoot, filename, result) {
  const releaseRoot = path.join(appRoot, 'release');
  await fs.mkdir(releaseRoot, { recursive: true });
  await assertNoSymlinkChain(releaseRoot);
  const target = path.join(releaseRoot, filename);
  const existing = await lstatOrNull(target);
  if (existing && (!existing.isFile() || existing.isSymbolicLink())) throw new Error(`unsafe export manifest target: ${target}`);
  const manifest = {
    schema: 'quiet_la_profile_export_manifest_v1',
    profile: result.profile,
    status: 'VERIFIED_COMPLETE_TREE',
    files: result.files,
    bytes: result.bytes,
    treeSha256: result.treeSha256,
    rows: result.rows,
  };
  const serialized = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`);
  const staging = `${target}.stage-${process.pid}-${randomBytes(4).toString('hex')}`;
  await fs.writeFile(staging, serialized, { flag: 'wx' });
  await fs.rename(staging, target);
  return { path: path.relative(appRoot, target).split(path.sep).join('/'), sha256: sha256(serialized), bytes: serialized.length };
}
