import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const defaultAppRoot = path.resolve(new URL('..', import.meta.url).pathname);
const denseSourceRootRelative = 'implementation/outputs/corrected-per-region-release-candidate/v2.1-dense-ui/assets';
const denseLocalRootRelative = 'public/_local-data/dense';
const densePackageManifestRelative = 'dense/package-manifest.json';
const sceneManifestRelative = 'scene3d/scene-manifest.json';
const stageManifestName = 'dense-stage-manifest.json';
const sourceManifestSchema = 'quiet_la_v21_dense_static_package_v1';
const sceneManifestSchema = 'quiet_la_v2_display_scene_manifest_v1';
const stageManifestSchema = 'quiet_la_web_dense_staged_manifest_v1';
const hashPattern = /^[0-9a-f]{64}$/;

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

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

function assertSafeRelative(relative) {
  if (!relative || relative.startsWith('/') || relative.includes('..') || relative.includes('\\') || path.posix.normalize(relative) !== relative) {
    throw new Error(`unsafe dense asset path: ${relative}`);
  }
}

function sourceRootFor(appRoot) {
  return path.resolve(appRoot, '../../outputs/corrected-per-region-release-candidate/v2.1-dense-ui/assets');
}

function outputRootFor(appRoot) {
  return path.join(appRoot, denseLocalRootRelative);
}

async function readManifest(sourceRoot, relative, expectedSchema) {
  const target = path.join(sourceRoot, relative);
  await assertRegularFile(target, 'dense source manifest');
  const bytes = await fs.readFile(target);
  const manifest = JSON.parse(bytes.toString('utf8'));
  if (manifest.schema !== expectedSchema) throw new Error(`unexpected dense manifest schema: ${relative}`);
  return { target, bytes, manifest, sha256: sha256(bytes) };
}

function validatePackageRows(manifest) {
  if (!Array.isArray(manifest.files) || manifest.files.length !== 53) throw new Error('dense package manifest must contain exactly 53 files');
  const seen = new Set();
  for (const row of manifest.files) {
    if (!row || typeof row.path !== 'string' || seen.has(row.path) || !Number.isSafeInteger(row.bytes) || row.bytes <= 0 || typeof row.sha256 !== 'string' || !hashPattern.test(row.sha256)) throw new Error('dense package manifest row is malformed or duplicated');
    assertSafeRelative(row.path);
    seen.add(row.path);
  }
  const shards = manifest.files.filter((row) => /^shards\/r\d{2}-c\d{2}\.json\.gz$/.test(row.path));
  const fields = manifest.files.filter((row) => /^fields\/(?:23|585)-[DEN]\.webp$/.test(row.path));
  const styles = manifest.files.filter((row) => /^styles\/(?:bands|glow)\/(?:23|585)-[DEN]\.webp$/.test(row.path));
  const sources = manifest.files.filter((row) => /^sources\/(?:23|585)\.geojson\.gz$/.test(row.path));
  if (shards.length !== 30 || fields.length !== 6 || styles.length !== 12 || sources.length !== 2) throw new Error('dense package manifest does not contain the exact shard/style/source counts');
  if (!seen.has('manifest.json') || !seen.has('overview.json.gz') || !seen.has('neighborhood.json.gz')) throw new Error('dense package manifest is missing required overview files');
  if (manifest.total_bytes !== manifest.files.reduce((sum, row) => sum + row.bytes, 0)) throw new Error('dense package total bytes drift');
  return manifest.files;
}

function validateSceneRows(manifest) {
  const buildings = manifest.buildings?.tiles;
  const terrain = manifest.terrain?.tiles;
  if (!Array.isArray(buildings) || buildings.length !== 15 || manifest.buildings.tile_count !== 15) throw new Error('scene manifest must contain exactly 15 building tiles');
  if (!Array.isArray(terrain) || terrain.length !== 30 || manifest.terrain.tile_count !== 30) throw new Error('scene manifest must contain exactly 30 terrain tiles');
  const rows = [...buildings, ...terrain];
  const seen = new Set([sceneManifestRelative]);
  for (const row of rows) {
    if (!row || typeof row.path !== 'string' || seen.has(row.path) || !Number.isSafeInteger(row.bytes) || row.bytes <= 0 || typeof row.sha256 !== 'string' || !hashPattern.test(row.sha256)) throw new Error('scene manifest row is malformed or duplicated');
    assertSafeRelative(row.path);
    seen.add(row.path);
  }
  for (const row of buildings) if (!/^buildings\/tarzana\/14-\d{4}-\d{4}\.geojson\.gz$/.test(row.path)) throw new Error(`unexpected building tile path: ${row.path}`);
  for (const row of terrain) if (!/^terrain\/tarzana\/\d+\/\d+\/\d+\.png$/.test(row.path)) throw new Error(`unexpected terrain tile path: ${row.path}`);
  return rows;
}

async function sourceSpecs(appRoot) {
  const sourceRoot = sourceRootFor(appRoot);
  await assertNoSymlinkChain(sourceRoot);
  const packageRecord = await readManifest(sourceRoot, densePackageManifestRelative, sourceManifestSchema);
  const sceneRecord = await readManifest(sourceRoot, sceneManifestRelative, sceneManifestSchema);
  const packageRows = validatePackageRows(packageRecord.manifest);
  const sceneRows = validateSceneRows(sceneRecord.manifest);
  const rows = [
    ...packageRows.map((row) => ({ ...row, target: `dense/${row.path}`, source: path.join('dense', row.path) })),
    { target: sceneManifestRelative, source: sceneManifestRelative, bytes: sceneRecord.bytes.length, sha256: sceneRecord.sha256 },
    ...sceneRows.map((row) => ({ ...row, target: `scene3d/${row.path}`, source: path.join('scene3d', row.path) })),
  ];
  const targets = new Set();
  for (const row of rows) {
    assertSafeRelative(row.target);
    if (targets.has(row.target)) throw new Error(`duplicate dense staged target: ${row.target}`);
    targets.add(row.target);
  }
  return { sourceRoot, packageRecord, sceneRecord, rows: rows.sort((left, right) => left.target.localeCompare(right.target)) };
}

async function walkFiles(root, current = root) {
  const files = [];
  for (const entry of await fs.readdir(current, { withFileTypes: true })) {
    const target = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`symlink in dense staged tree: ${target}`);
    if (entry.isDirectory()) files.push(...await walkFiles(root, target));
    else if (entry.isFile()) files.push(path.relative(root, target).split(path.sep).join('/'));
    else throw new Error(`non-regular dense staged entry: ${target}`);
  }
  return files.sort();
}

export async function verifyDenseLocalData(root = outputRootFor(defaultAppRoot), appRoot = defaultAppRoot) {
  await assertNoSymlinkChain(root);
  const rootStat = await lstatOrNull(root);
  if (!rootStat?.isDirectory() || rootStat.isSymbolicLink()) throw new Error('dense local root is not a regular directory');
  const specs = await sourceSpecs(appRoot);
  const actual = await walkFiles(root);
  const expected = [...specs.rows.map((row) => row.target), stageManifestName].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error(`dense staged file set differs from canonical manifests: ${actual.join(', ')}`);
  const stagedPath = path.join(root, stageManifestName);
  await assertRegularFile(stagedPath, 'dense staged manifest');
  const staged = JSON.parse((await fs.readFile(stagedPath)).toString('utf8'));
  if (staged.schema !== stageManifestSchema || staged.candidate !== 'v21_dense_ui_local_recovery') throw new Error('dense staged manifest identity drift');
  if (staged.packageManifestSha256 !== specs.packageRecord.sha256 || staged.sceneManifestSha256 !== specs.sceneRecord.sha256) throw new Error('dense staged manifest source hash drift');
  if (!Array.isArray(staged.rows) || staged.rows.length !== specs.rows.length) throw new Error('dense staged manifest row count drift');
  const stagedRows = new Map(staged.rows.map((row) => [row.target, row]));
  let bytes = 0;
  for (const row of specs.rows) {
    const target = path.join(root, row.target);
    await assertRegularFile(target, 'dense staged target');
    const actualBytes = await fs.readFile(target);
    if (actualBytes.length !== row.bytes || sha256(actualBytes) !== row.sha256) throw new Error(`dense staged hash/size drift: ${row.target}`);
    const stagedRow = stagedRows.get(row.target);
    if (!stagedRow || stagedRow.source !== row.source || stagedRow.bytes !== row.bytes || stagedRow.sha256 !== row.sha256) throw new Error(`dense staged manifest row drift: ${row.target}`);
    bytes += row.bytes;
  }
  return { files: specs.rows.length, bytes, rows: specs.rows.map(({ target, bytes: size, sha256: digest }) => ({ path: target, bytes: size, sha256: digest })) };
}

export async function stageDenseLocalData(appRoot = defaultAppRoot) {
  const specs = await sourceSpecs(appRoot);
  const outputRoot = outputRootFor(appRoot);
  await assertNoSymlinkChain(path.dirname(outputRoot));
  const existing = await lstatOrNull(outputRoot);
  if (existing?.isSymbolicLink()) throw new Error(`dense local root is a symlink: ${outputRoot}`);
  if (existing && !existing.isDirectory()) throw new Error(`dense local root is not a regular directory: ${outputRoot}`);
  if (existing) {
    try {
      const result = await verifyDenseLocalData(outputRoot, appRoot);
      return { status: 'DENSE_LOCAL_ALREADY_VALID', output: outputRoot, ...result };
    } catch {
      // Replace invalid generated dense data atomically below.
    }
  }
  const staging = `${outputRoot}.staging-${process.pid}-${Date.now()}`;
  const superseded = `${outputRoot}.superseded-${process.pid}-${Date.now()}`;
  if (await lstatOrNull(staging)) throw new Error(`dense staging path already exists: ${staging}`);
  if (await lstatOrNull(superseded)) throw new Error(`dense superseded path already exists: ${superseded}`);
  await fs.mkdir(staging, { recursive: true });
  try {
    const stagedRows = [];
    for (const row of specs.rows) {
      const source = path.join(specs.sourceRoot, row.source);
      const target = path.join(staging, row.target);
      await assertRegularFile(source, 'dense source asset');
      await assertNoSymlinkChain(target);
      await fs.mkdir(path.dirname(target), { recursive: true });
      const bytes = await fs.readFile(source);
      if (bytes.length !== row.bytes || sha256(bytes) !== row.sha256) throw new Error(`canonical dense asset drift: ${row.source}`);
      await fs.writeFile(target, bytes, { flag: 'wx' });
      stagedRows.push({ source: row.source, target: row.target, bytes: row.bytes, sha256: row.sha256 });
    }
    await fs.writeFile(path.join(staging, stageManifestName), `${JSON.stringify({ schema: stageManifestSchema, candidate: 'v21_dense_ui_local_recovery', sourceRoot: denseSourceRootRelative, packageManifestSha256: specs.packageRecord.sha256, sceneManifestSha256: specs.sceneRecord.sha256, rows: stagedRows }, null, 2)}\n`, { flag: 'wx' });
    const stagedResult = await verifyDenseLocalData(staging, appRoot);
    if (existing) await fs.rename(outputRoot, superseded);
    try {
      await fs.rename(staging, outputRoot);
    } catch (error) {
      if (existing) await fs.rename(superseded, outputRoot).catch(() => {});
      throw error;
    }
    let result;
    try {
      result = await verifyDenseLocalData(outputRoot, appRoot);
    } catch (error) {
      const rejected = `${outputRoot}.rejected-${process.pid}-${Date.now()}`;
      await fs.rename(outputRoot, rejected).catch(() => {});
      if (existing) await fs.rename(superseded, outputRoot).catch(() => {});
      await fs.rm(rejected, { recursive: true, force: true });
      throw error;
    }
    if (existing) await fs.rm(superseded, { recursive: true, force: true });
    if (result.files !== stagedResult.files || result.bytes !== stagedResult.bytes) throw new Error('dense staged data changed during atomic replacement');
    return { status: 'DENSE_LOCAL_STAGED', output: outputRoot, ...result };
  } catch (error) {
    await fs.rm(staging, { recursive: true, force: true });
    const displaced = await lstatOrNull(superseded);
    if (displaced && !(await lstatOrNull(outputRoot))) await fs.rename(superseded, outputRoot).catch(() => {});
    throw error;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  stageDenseLocalData().then((result) => console.log(JSON.stringify(result, null, 2))).catch((error) => {
    console.error(`stage-dense-local-data: ${error.message}`);
    process.exitCode = 1;
  });
}
