import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const appRootDefault = path.resolve(new URL('..', import.meta.url).pathname);
const contractPath = (appRoot) => path.join(appRoot, 'src/data/pilot-release-contract.json');
const sourceRoot = (appRoot) => path.join(appRoot, 'src/data/pilot-release-v1');
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
async function regular(file) { const stat = await fs.lstat(file); if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`regular release asset required: ${file}`); }

export async function stagePilotLocalData(appRoot = appRootDefault) {
  const contract = JSON.parse(await fs.readFile(contractPath(appRoot), 'utf8'));
  if (contract.schema !== 'quiet_la_combined_road_study_release_contract_v1' || !Array.isArray(contract.tiles)) throw new Error('pilot release contract schema drift');
  const includedTiles = contract.tiles.filter((tile) => tile.status === 'accepted_legacy_default' || tile.status === 'accepted_expansion');
  if (!includedTiles.some((tile) => tile.tile_id === contract.default_tile_id)) throw new Error('pilot release default tile is not admitted');

  const targetRoot = path.join(appRoot, 'public/_local-data/v3/pilot');
  const prior = await fs.lstat(targetRoot).catch(() => null);
  if (prior && (!prior.isDirectory() || prior.isSymbolicLink())) throw new Error('pilot stage path must be a regular directory');
  await fs.rm(targetRoot, { recursive: true, force: true });
  await fs.mkdir(targetRoot, { recursive: true });
  const stagedTiles = [];
  const rows = [];
  for (const tile of includedTiles) {
    const tileRows = [];
    for (const asset of tile.assets) {
      const source = path.join(sourceRoot(appRoot), asset.path);
      const target = path.join(targetRoot, asset.path);
      await regular(source);
      const bytes = await fs.readFile(source);
      if (bytes.length !== asset.bytes || hash(bytes) !== asset.sha256) throw new Error(`pilot asset hash/size drift (${tile.tile_id}/${asset.path})`);
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.writeFile(target, bytes, { flag: 'wx' });
      const row = { path: asset.path, sha256: asset.sha256, bytes: asset.bytes, kind: asset.kind };
      tileRows.push(row); rows.push(row);
    }
    const manifestAsset = tile.assets.find((asset) => asset.kind === 'manifest');
    const publicManifest = JSON.parse(await fs.readFile(path.join(targetRoot, manifestAsset.path), 'utf8'));
    for (const field of ['receiver_count', 'numeric_rows', 'building_count', 'facade_receiver_count']) {
      if (publicManifest[field] !== tile[field]) throw new Error(`pilot release contract count drift for ${tile.tile_id}.${field}`);
    }
    if (JSON.stringify(publicManifest.masked_ids ?? []) !== JSON.stringify(tile.masked_ids)) throw new Error(`pilot mask contract drift for ${tile.tile_id}`);
    stagedTiles.push({ tile_id: tile.tile_id, status: tile.status, bbox_wgs84: tile.bbox_wgs84, counts: { receiver_count: tile.receiver_count, numeric_rows: tile.numeric_rows, building_count: tile.building_count, facade_receiver_count: tile.facade_receiver_count }, rows: tileRows });
  }
  const stagedManifest = {
    schema: 'quiet_la_combined_road_pilot_stage_v2',
    study_id: contract.study_id,
    default_tile_id: contract.default_tile_id,
    contract_path: 'src/data/pilot-release-contract.json',
    contract_sha256: hash(await fs.readFile(contractPath(appRoot))),
    tiles: stagedTiles,
    rows,
  };
  const manifestBytes = Buffer.from(`${JSON.stringify(stagedManifest, null, 2)}\n`);
  await fs.writeFile(path.join(targetRoot, 'staged-manifest.json'), manifestBytes, { flag: 'wx' });
  return { status: 'PILOT_LOCAL_STAGE_VALID', target: targetRoot, studyId: contract.study_id, tiles: stagedTiles.map((tile) => tile.tile_id), rows: rows.length, hashes: Object.fromEntries(rows.map((row) => [row.path, row.sha256])), contractSha256: stagedManifest.contract_sha256, stagedManifestSha256: hash(manifestBytes) };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  if (process.argv.length > 2) throw new Error(`unsupported stage-pilot-local-data arguments: ${process.argv.slice(2).join(' ')}`);
  stagePilotLocalData(appRootDefault).then((result) => console.log(JSON.stringify(result, null, 2))).catch((error) => { console.error(`stage-pilot-local-data: ${error.message}`); process.exitCode = 1; });
}
