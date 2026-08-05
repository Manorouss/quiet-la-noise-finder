import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { verifyStagedRoot } from './verify-staged-local-data.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const projectRoot = path.resolve(appRoot, '../../..');
const manifestPath = path.join(appRoot, 'src/data/local-source-manifest.json');
const outputRoot = path.join(appRoot, 'public/_local-data');

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

async function assertRegularNoSymlink(filePath, label) {
  const stat = await fs.lstat(filePath).catch(() => null);
  if (!stat || !stat.isFile() || stat.isSymbolicLink()) throw new Error(`${label} must be a regular non-symlink file: ${filePath}`);
}

async function assertNoSymlinkComponents(targetPath) {
  const relative = path.relative(path.parse(targetPath).root, targetPath);
  let cursor = path.parse(targetPath).root;
  for (const component of relative.split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, component);
    const stat = await fs.lstat(cursor).catch(() => null);
    if (stat?.isSymbolicLink()) throw new Error(`symlink path component rejected: ${cursor}`);
  }
}

async function copyFresh(src, dest) {
  await assertRegularNoSymlink(src, 'source');
  await fs.mkdir(path.dirname(dest), { recursive: true });
  await assertNoSymlinkComponents(dest);
  const bytes = await fs.readFile(src);
  await fs.writeFile(dest, bytes, { flag: 'wx' });
  return bytes;
}

async function main() {
  const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
  if (manifest.schema !== 'quiet_la_web_local_source_manifest_v1') throw new Error('unexpected source manifest schema');
  const staging = `${outputRoot}.staging-${process.pid}-${Date.now()}`;
  await assertNoSymlinkComponents(outputRoot);
  if (await fs.lstat(outputRoot).then((s) => s.isSymbolicLink()).catch(() => false)) throw new Error('local data output is a symlink');
  if (await fs.stat(outputRoot).then(() => true).catch(() => false)) {
    const existing = await verifyStagedRoot(outputRoot, manifestPath);
    console.log(JSON.stringify({ status: 'STAGED_LOCAL_ALREADY_VALID', output: outputRoot, ...existing }, null, 2));
    return;
  }
  await fs.mkdir(staging, { recursive: true });
  const rows = [];
  try {
    for (const row of manifest.rows) {
      if (row.localOnly !== true) throw new Error(`local source row is not explicitly local-only: ${row.target}`);
      const source = path.join(manifest.sourceRoot, row.source);
      const sourcePath = path.join(projectRoot, source);
      const target = path.join(staging, row.target);
      const bytes = await copyFresh(sourcePath, target);
      const actual = sha256(bytes);
      if (actual !== row.sha256 || bytes.length !== row.bytes) throw new Error(`source drift for ${row.source}`);
      rows.push({ ...row, sha256: actual, bytes: bytes.length });
    }
    const stagedManifest = {
      schema: 'quiet_la_web_local_staged_manifest_v1',
      candidate: manifest.candidate,
      source_manifest: 'src/data/local-source-manifest.json',
      rows,
    };
    await fs.writeFile(path.join(staging, 'staged-manifest.json'), JSON.stringify(stagedManifest, null, 2) + '\n', { flag: 'wx' });
    await fs.rename(staging, outputRoot);
    console.log(JSON.stringify({ status: 'STAGED_LOCAL_ONLY', output: outputRoot, files: rows.length, bytes: rows.reduce((sum, row) => sum + row.bytes, 0) }, null, 2));
  } catch (error) {
    await fs.rm(staging, { recursive: true, force: true });
    throw error;
  }
}

main().catch((error) => { console.error(`stage-local-data: ${error.message}`); process.exitCode = 1; });
