import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const sourceRoot = path.join(appRoot, 'src/data/pilot-release-v1');
const rows = [
  { source: 'benchmark.geojson', target: 'benchmark.geojson', sha256: '16df6aaf99abaf0a791e5de95a49a5eda5760851994fd4636465086cffd59c93', bytes: 2754559 },
  { source: 'buildings.geojson', target: 'buildings.geojson', sha256: 'b1f25db888ebb749751fc9b4beb2a2ab93bb6be28e23e273243e03d1ef937bd5', bytes: 102267 },
  { source: 'build-manifest.json', target: 'build-manifest.json', sha256: 'd733b7e11af379b2a31a1cda7559481ef5d7a175464c5d3b92c5c273c2c15769', bytes: 496 },
];
const sourceManifestSha256 = 'f2f47b8a1a6a2af1c7885c763b12e58b970bb563a70822f6e1cbe7cd2e2fac37';
const hash = (b) => createHash('sha256').update(b).digest('hex');
async function regular(p) { const s = await fs.lstat(p); if (!s.isFile() || s.isSymbolicLink()) throw new Error(`regular file required: ${p}`); }
export async function stagePilotLocalData(appRoot = path.resolve(new URL('..', import.meta.url).pathname)) {
  const targetRoot = path.join(appRoot, 'public/_local-data/v3/pilot');
  await fs.mkdir(targetRoot, { recursive: true });
  for (const row of rows) {
    const source = path.join(sourceRoot, row.source); const target = path.join(targetRoot, row.target);
    await regular(source); const bytes = await fs.readFile(source);
    if (bytes.length !== row.bytes || hash(bytes) !== row.sha256) throw new Error(`pilot source drift: ${row.source}`);
    const prior = await fs.readFile(target).catch(() => null);
    if (row.target === 'build-manifest.json' && prior && hash(prior) === sourceManifestSha256) await fs.writeFile(target, bytes);
    else if (prior && (prior.length !== row.bytes || hash(prior) !== row.sha256)) throw new Error(`pilot staged drift: ${row.target}`);
    else if (!prior) await fs.writeFile(target, bytes, { flag: 'wx' });
  }
  const publicManifestPath = path.join(sourceRoot, 'build-manifest.json');
  const publicManifestBytes = await fs.readFile(publicManifestPath);
  const parsed = JSON.parse(publicManifestBytes.toString('utf8'));
  const expectedCounts = { receiver_count: 6421, numeric_rows: 19257, building_count: 80, facade_receiver_count: 1573 };
  if (Object.entries(expectedCounts).some(([key, value]) => parsed[key] !== value) || JSON.stringify(parsed.masked_ids) !== '[85715,88627]') throw new Error('pilot source manifest count/mask contract drift');
  if (!parsed.source_sha256 || !parsed.source_buildings_sha256 || !parsed.source_receivers_sha256 || ['source_csv', 'source_receivers', 'source_buildings'].some((key) => Object.hasOwn(parsed, key))) throw new Error('pilot release manifest provenance/path contract drift');
  const manifest = { schema: 'quiet_la_tarzana_pilot_local_stage_v1', model: 'tarzana-pilot-r02-c03-freeway-v1', source: 'canonical-pilot-release-v1', source_manifest_sha256: sourceManifestSha256, rows };
  await fs.writeFile(path.join(targetRoot, 'staged-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  return { status: 'PILOT_LOCAL_STAGE_VALID', target: targetRoot, rows: rows.length, hashes: Object.fromEntries(rows.map((r) => [r.target, r.sha256])), sourceManifestSha256, publicManifestSha256: hash(publicManifestBytes) };
}
if (import.meta.url === `file://${process.argv[1]}`) stagePilotLocalData().then((result) => console.log(JSON.stringify(result, null, 2))).catch((e) => { console.error(`stage-pilot-local-data: ${e.message}`); process.exitCode = 1; });
