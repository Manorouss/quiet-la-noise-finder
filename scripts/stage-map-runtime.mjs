import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const defaultAppRoot = path.resolve(new URL('..', import.meta.url).pathname);
const runtimeFiles = Object.freeze(['maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs']);

async function lstatOrNull(target) {
  return fs.lstat(target).catch(() => null);
}

async function assertNoSymlinkChain(target) {
  const absolute = path.resolve(target);
  let cursor = path.parse(absolute).root;
  for (const component of path.relative(cursor, absolute).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, component);
    const stat = await lstatOrNull(cursor);
    if (stat?.isSymbolicLink()) throw new Error(`symlink path component rejected: ${cursor}`);
  }
}

async function assertRegularFile(target, label) {
  await assertNoSymlinkChain(target);
  const stat = await lstatOrNull(target);
  if (!stat?.isFile() || stat.isSymbolicLink()) throw new Error(`${label} must be a regular non-symlink file: ${target}`);
}

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function pathsFor(appRoot) {
  return {
    sourceRoot: path.join(appRoot, 'node_modules/maplibre-gl/dist'),
    runtimeRoot: path.join(appRoot, 'public/maplibre'),
  };
}

async function sourceRows(appRoot) {
  const { sourceRoot } = pathsFor(appRoot);
  const rows = [];
  for (const name of runtimeFiles) {
    const source = path.join(sourceRoot, name);
    await assertRegularFile(source, 'installed MapLibre runtime source');
    const bytes = await fs.readFile(source);
    rows.push({ name, bytes, size: bytes.length, sha256: sha256(bytes) });
  }
  return rows;
}

async function actualRuntimeNames(runtimeRoot) {
  await assertNoSymlinkChain(runtimeRoot);
  const stat = await lstatOrNull(runtimeRoot);
  if (!stat?.isDirectory() || stat.isSymbolicLink()) throw new Error(`MapLibre runtime root is missing or unsafe: ${runtimeRoot}`);
  const entries = await fs.readdir(runtimeRoot, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.isSymbolicLink()) throw new Error(`symlink in MapLibre runtime root: ${path.join(runtimeRoot, entry.name)}`);
    if (!entry.isFile()) throw new Error(`non-file in MapLibre runtime root: ${path.join(runtimeRoot, entry.name)}`);
  }
  return entries.map((entry) => entry.name).sort();
}

/** Verify that a staged/exported runtime contains exactly the installed MapLibre sibling modules. */
export async function verifyMapRuntime(runtimeRoot = pathsFor(defaultAppRoot).runtimeRoot, appRoot = defaultAppRoot) {
  const expected = await sourceRows(appRoot);
  const actual = await actualRuntimeNames(runtimeRoot);
  if (JSON.stringify(actual) !== JSON.stringify([...runtimeFiles].sort())) {
    throw new Error(`MapLibre runtime file set differs from installed pair: ${actual.join(', ')}`);
  }
  for (const row of expected) {
    const target = path.join(runtimeRoot, row.name);
    await assertRegularFile(target, 'staged MapLibre runtime');
    const bytes = await fs.readFile(target);
    if (bytes.length !== row.size || sha256(bytes) !== row.sha256) throw new Error(`MapLibre runtime bytes differ from installed package: ${row.name}`);
  }
  return { files: expected.length, bytes: expected.reduce((sum, row) => sum + row.size, 0), rows: expected.map(({ name, size, sha256: digest }) => ({ path: name, bytes: size, sha256: digest })) };
}

/** Stage the exact installed MapLibre worker/shared module pair under public/maplibre. */
export async function stageMapRuntime(appRoot = defaultAppRoot) {
  const { runtimeRoot } = pathsFor(appRoot);
  await assertNoSymlinkChain(path.join(appRoot, 'public'));
  const existing = await lstatOrNull(runtimeRoot);
  if (existing?.isSymbolicLink()) throw new Error(`MapLibre runtime root is a symlink: ${runtimeRoot}`);
  if (existing && !existing.isDirectory()) throw new Error(`MapLibre runtime root is not a regular directory: ${runtimeRoot}`);

  const source = await sourceRows(appRoot);
  if (existing) {
    try {
      const result = await verifyMapRuntime(runtimeRoot, appRoot);
      return { status: 'MAP_RUNTIME_ALREADY_VALID', output: runtimeRoot, ...result };
    } catch {
      // Replace an invalid generated runtime atomically below.
    }
  }

  const staging = `${runtimeRoot}.staging-${process.pid}-${Date.now()}`;
  const superseded = `${runtimeRoot}.superseded-${process.pid}-${Date.now()}`;
  await assertNoSymlinkChain(path.dirname(staging));
  if (await lstatOrNull(staging)) throw new Error(`MapLibre runtime staging path already exists: ${staging}`);
  if (await lstatOrNull(superseded)) throw new Error(`MapLibre runtime superseded path already exists: ${superseded}`);
  await fs.mkdir(staging, { recursive: true });
  try {
    for (const row of source) {
      const target = path.join(staging, row.name);
      await assertNoSymlinkChain(target);
      await fs.writeFile(target, row.bytes, { flag: 'wx' });
    }
    await verifyMapRuntime(staging, appRoot);
    if (existing) await fs.rename(runtimeRoot, superseded);
    try {
      await fs.rename(staging, runtimeRoot);
    } catch (error) {
      if (existing) await fs.rename(superseded, runtimeRoot).catch(() => {});
      throw error;
    }
    let result;
    try {
      result = await verifyMapRuntime(runtimeRoot, appRoot);
    } catch (error) {
      const failed = `${runtimeRoot}.rejected-${process.pid}-${Date.now()}`;
      await fs.rename(runtimeRoot, failed).catch(() => {});
      if (existing) await fs.rename(superseded, runtimeRoot).catch(() => {});
      await fs.rm(failed, { recursive: true, force: true });
      throw error;
    }
    if (existing) await fs.rm(superseded, { recursive: true, force: true });
    return { status: 'MAP_RUNTIME_STAGED', output: runtimeRoot, ...result };
  } catch (error) {
    await fs.rm(staging, { recursive: true, force: true });
    const displaced = await lstatOrNull(superseded);
    if (displaced && !(await lstatOrNull(runtimeRoot))) await fs.rename(superseded, runtimeRoot).catch(() => {});
    throw error;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  stageMapRuntime().then((result) => console.log(JSON.stringify(result, null, 2))).catch((error) => {
    console.error(`stage-map-runtime: ${error.message}`);
    process.exitCode = 1;
  });
}
