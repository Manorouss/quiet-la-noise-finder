import { createHash, randomBytes } from 'node:crypto';
import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { assertNoSymlinkChain } from './export-profile-policy.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const nextBin = path.join(appRoot, 'node_modules/next/dist/bin/next');
const outRoot = path.join(appRoot, 'out');

async function lstatOrNull(target) {
  return fs.lstat(target).catch(() => null);
}

async function walkFiles(current) {
  const files = [];
  for (const entry of await fs.readdir(current, { withFileTypes: true })) {
    const target = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`symlink in deterministic build inputs: ${target}`);
    if (entry.isDirectory()) files.push(...await walkFiles(target));
    else files.push(target);
  }
  return files;
}

async function deterministicBuildId(token) {
  const inputs = [
    ...await walkFiles(path.join(appRoot, 'src/app')),
    ...await walkFiles(path.join(appRoot, 'src/components')),
    ...await walkFiles(path.join(appRoot, 'src/lib')),
    path.join(appRoot, 'src/data/layer-contract.json'),
    path.join(appRoot, 'src/data/deployment-contract.json'),
    path.join(appRoot, 'src/data/private-preview-payload-contract.json'),
    path.join(appRoot, 'package.json'),
    path.join(appRoot, 'package-lock.json'),
    path.join(appRoot, 'next.config.mjs'),
    path.join(appRoot, 'public/robots.txt'),
  ];
  const digest = createHash('sha256').update(`${token}\0`);
  for (const file of [...new Set(inputs)].sort()) {
    digest.update(`${path.relative(appRoot, file).split(path.sep).join('/')}\0`);
    digest.update(await fs.readFile(file));
    digest.update('\0');
  }
  return `ql-${token[0]}-${digest.digest('hex').slice(0, 24)}`;
}

async function requireDirectSafeDirectory(target, { allowMissing = false } = {}) {
  await assertNoSymlinkChain(path.dirname(target));
  const stat = await lstatOrNull(target);
  if (!stat && allowMissing) return;
  if (!stat?.isDirectory() || stat.isSymbolicLink()) throw new Error(`expected regular directory: ${target}`);
}

export async function publishVerifiedExport(stageRoot, verify, targetOutRoot = outRoot) {
  await requireDirectSafeDirectory(stageRoot);
  await assertNoSymlinkChain(targetOutRoot);
  const outStat = await lstatOrNull(targetOutRoot);
  if (outStat && (!outStat.isDirectory() || outStat.isSymbolicLink())) throw new Error('existing app out is not a regular directory');
  const backupRoot = path.join(path.dirname(targetOutRoot), `.quiet-la-out-backup-${process.pid}-${randomBytes(4).toString('hex')}`);
  if (await lstatOrNull(backupRoot)) throw new Error(`out backup path already exists: ${backupRoot}`);
  let oldMoved = false;
  let newPublished = false;
  try {
    if (outStat) {
      await fs.rename(targetOutRoot, backupRoot);
      oldMoved = true;
    }
    await fs.rename(stageRoot, targetOutRoot);
    newPublished = true;
    const result = await verify(targetOutRoot);
    if (oldMoved) await fs.rm(backupRoot, { recursive: true });
    return result;
  } catch (error) {
    if (newPublished) {
      const failedRoot = path.join(path.dirname(targetOutRoot), `.quiet-la-out-rejected-${process.pid}-${randomBytes(4).toString('hex')}`);
      await fs.rename(targetOutRoot, failedRoot).catch(() => {});
      if (oldMoved) await fs.rename(backupRoot, targetOutRoot).catch(() => {});
      await fs.rm(failedRoot, { recursive: true, force: true });
    } else if (oldMoved) {
      await fs.rename(backupRoot, targetOutRoot).catch(() => {});
    }
    throw error;
  }
}

export async function buildIsolatedProfile({ token, env, excludeFromExport = [], preBuild, verify }) {
  if (!['local', 'external', 'private'].includes(token)) throw new Error(`unknown build profile token: ${token}`);
  const suffix = randomBytes(4).toString('hex');
  const stageName = `.quiet-la-export-${token}-${process.pid}-${suffix}`;
  const stageRoot = path.join(appRoot, stageName);
  const buildEnv = { ...env };
  if (!buildEnv.QUIET_LA_PREVIEW_BUILD_ID) buildEnv.QUIET_LA_PREVIEW_BUILD_ID = await deterministicBuildId(token);
  if (await lstatOrNull(stageRoot)) throw new Error(`fresh export stage already exists: ${stageRoot}`);
  try {
    if (preBuild) await preBuild();
    const code = await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [nextBin, 'build'], {
        cwd: appRoot,
        env: { ...process.env, ...buildEnv, QUIET_LA_EXPORT_DIR: stageName },
        stdio: 'inherit',
      });
      child.once('error', reject);
      child.once('close', resolve);
    });
    if (code !== 0) throw new Error(`Next ${token} profile build exited ${code}`);
    await requireDirectSafeDirectory(stageRoot);
    for (const name of excludeFromExport) {
      if (!['_local-data', '_preview-data'].includes(name)) throw new Error(`unsupported export exclusion name: ${name}`);
      const target = path.join(stageRoot, name);
      const entry = await lstatOrNull(target);
      if (!entry) continue;
      if (!entry.isDirectory() || entry.isSymbolicLink()) throw new Error(`unsafe excluded export root: ${target}`);
      await assertNoSymlinkChain(target);
      await fs.rm(target, { recursive: true });
    }
    await verify(stageRoot);
    return await publishVerifiedExport(stageRoot, verify);
  } finally {
    const remainingStage = await lstatOrNull(stageRoot);
    if (remainingStage) {
      if (!remainingStage.isDirectory() || remainingStage.isSymbolicLink()) throw new Error(`unsafe leftover export stage: ${stageRoot}`);
      await fs.rm(stageRoot, { recursive: true });
    }
  }
}
