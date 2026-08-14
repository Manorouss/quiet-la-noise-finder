import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { validateReplacementAdapter } from '../src/lib/replacement-layer-adapter.js';

async function assertNoSymlinkComponents(target) {
  const absolute = path.isAbsolute(target) ? path.normalize(target) : path.resolve(target);
  let cursor = path.parse(absolute).root;
  for (const part of path.relative(cursor, absolute).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const stat = await fs.lstat(cursor).catch((error) => {
      if (error?.code === 'ENOENT') return null;
      throw error;
    });
    if (stat?.isSymbolicLink()) throw new Error(`replacement asset path component is a symlink: ${cursor}`);
  }
  return absolute;
}

async function walk(root, current = root) {
  const files = [];
  for (const entry of await fs.readdir(current, { withFileTypes: true })) {
    const target = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`replacement asset symlink: ${path.relative(root, target)}`);
    if (entry.isDirectory()) files.push(...await walk(root, target));
    else if (entry.isFile()) files.push(path.relative(root, target).split(path.sep).join('/'));
    else throw new Error(`replacement asset is not a regular file: ${path.relative(root, target)}`);
  }
  return files;
}

export async function verifyReplacementAssetRoot(root, contract, manifest) {
  const summary = validateReplacementAdapter(contract, manifest);
  const rootPath = await assertNoSymlinkComponents(root);
  const stat = await fs.lstat(rootPath).catch((error) => {
    if (error?.code === 'ENOENT') return null;
    throw error;
  });
  if (!stat && manifest.rows.length > 0) throw new Error('replacement asset root is missing for a nonempty manifest');
  if (!stat) return summary;
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('replacement asset root must be a regular directory');
  const controlFiles = new Set(contract.assetPolicy.controlFiles);
  for (const controlFile of controlFiles) {
    const controlPath = path.join(rootPath, controlFile);
    await assertNoSymlinkComponents(controlPath);
    const controlStat = await fs.lstat(controlPath).catch((error) => {
      if (error?.code === 'ENOENT') return null;
      throw error;
    });
    if (controlStat && (!controlStat.isFile() || controlStat.isSymbolicLink())) throw new Error(`replacement control file is not regular: ${controlFile}`);
  }
  const actual = (await walk(rootPath)).filter((file) => !controlFiles.has(file));
  const expected = manifest.rows.map((row) => row.assetPath).sort();
  if (JSON.stringify(actual.sort()) !== JSON.stringify(expected)) throw new Error('replacement asset file set differs from the admitted manifest');
  for (const row of manifest.rows) {
    const file = path.join(rootPath, row.assetPath);
    await assertNoSymlinkComponents(file);
    const fileStat = await fs.lstat(file);
    if (!fileStat.isFile() || fileStat.isSymbolicLink()) throw new Error(`replacement asset is not regular: ${row.assetPath}`);
    const bytes = await fs.readFile(file);
    const digest = createHash('sha256').update(bytes).digest('hex');
    if (bytes.length !== row.bytes || digest !== row.sha256) throw new Error(`replacement asset hash/size drift: ${row.assetPath}`);
  }
  return summary;
}
