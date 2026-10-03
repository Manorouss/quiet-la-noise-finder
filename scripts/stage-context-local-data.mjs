import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const sourceRoot = path.resolve(appRoot, '../../work');
const outputRoot = path.join(appRoot, 'public/_local-data/context');
const manifestPath = path.join(appRoot, 'src/data/map-context-source-manifest.json');
const rows = [
  { source: 'la_county_fire_stations.geojson', target: 'la_county_fire_stations.geojson', sha256: '66d63b91da0df1400f5a94d0d0de35110bc67393569a6fc10f1fcf6b078691dc', features: 185 },
  { source: 'la_city_fire_stations.geojson', target: 'la_city_fire_stations.geojson', sha256: '4fdbae2ecf9358eb257d5e6b04737d12f33edaec6bfd915336c57d6ada08af08', features: 106 },
  { source: 'la_county_heliports.geojson', target: 'la_county_heliports.geojson', sha256: 'deb7272780bc0fcf103262a6080e70a443fd59615ea891bfb8096a8bad34c787', features: 199 },
  { source: 'la_county_airport_noise_contours.geojson', target: 'la_county_airport_noise_contours.geojson', sha256: 'a15d4650f695300a550ae9b7f84d335784a204e1f0936de3379715bac45591d8', features: 35 },
];

function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
async function lstatOrNull(target) { return fs.lstat(target).catch(() => null); }
async function assertNoSymlinkChain(target) {
  const absolute = path.resolve(target);
  let cursor = path.parse(absolute).root;
  for (const component of path.relative(cursor, absolute).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, component);
    const stat = await lstatOrNull(cursor);
    if (stat?.isSymbolicLink()) throw new Error(`symlink path component rejected: ${cursor}`);
  }
}
async function assertRegular(target, label) {
  await assertNoSymlinkChain(target);
  const stat = await lstatOrNull(target);
  if (!stat?.isFile() || stat.isSymbolicLink()) throw new Error(`${label} must be a regular file: ${target}`);
}

export async function verifyContextLocalData(root = outputRoot) {
  const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
  if (manifest.schema !== 'quiet_la_web_context_source_manifest_v1' || manifest.rows?.length !== rows.length) throw new Error('context source manifest drift');
  for (const row of rows) {
    const expected = manifest.rows.find((item) => item.target === row.target);
    if (!expected || expected.sha256 !== row.sha256 || expected.features !== row.features) throw new Error(`context manifest row drift: ${row.target}`);
    const target = path.join(root, row.target);
    await assertRegular(target, 'context data');
    const bytes = await fs.readFile(target);
    if (sha256(bytes) !== row.sha256) throw new Error(`context hash drift: ${row.target}`);
    const collection = JSON.parse(bytes.toString('utf8'));
    if (collection.type !== 'FeatureCollection' || collection.features?.length !== row.features) throw new Error(`context feature count drift: ${row.target}`);
  }
  return { files: rows.length, features: rows.reduce((sum, row) => sum + row.features, 0) };
}

export async function stageContextLocalData() {
  await fs.mkdir(outputRoot, { recursive: true });
  for (const row of rows) {
    const source = path.join(sourceRoot, row.source);
    const target = path.join(outputRoot, row.target);
    await assertRegular(source, 'canonical context source');
    const bytes = await fs.readFile(source);
    if (sha256(bytes) !== row.sha256) throw new Error(`canonical context hash drift: ${row.source}`);
    await assertNoSymlinkChain(target);
    await fs.writeFile(target, bytes);
  }
  return { status: 'CONTEXT_LOCAL_STAGED', output: outputRoot, ...(await verifyContextLocalData()) };
}

if (import.meta.url === `file://${process.argv[1]}`) stageContextLocalData().then((result) => console.log(JSON.stringify(result, null, 2))).catch((error) => { console.error(`stage-context-local-data: ${error.message}`); process.exitCode = 1; });
