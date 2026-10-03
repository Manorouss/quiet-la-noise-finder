import { promises as fs } from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { assertFrameworkOrAllowedPath, assertNoCredentialRows, compactRows, enumerateRegularTree, sha256 } from './export-profile-policy.mjs';
import { verifyMapRuntime } from './stage-map-runtime.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const defaultRoot = path.join(appRoot, 'out');
const contractPath = path.join(appRoot, 'src/data/pilot-release-contract.json');
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');

export async function verifyPilotProfileExport(root = defaultRoot) {
  const contractBytes = await fs.readFile(contractPath);
  const contract = JSON.parse(contractBytes.toString('utf8'));
  if (contract.schema !== 'quiet_la_combined_road_study_release_contract_v1') throw new Error('pilot release contract schema drift');
  const pilotRoot = path.join(root, '_local-data/v3/pilot');
  const manifestPath = path.join(pilotRoot, 'staged-manifest.json');
  const manifest = JSON.parse(await fs.readFile(manifestPath).catch(() => { throw new Error('pilot stage is missing from the export'); }));
  if (manifest.schema !== 'quiet_la_combined_road_pilot_stage_v2' || manifest.study_id !== contract.study_id || manifest.default_tile_id !== contract.default_tile_id || manifest.contract_sha256 !== hash(contractBytes)) throw new Error('pilot stage manifest differs from the current study contract');
  const expectedTiles = contract.tiles.filter((tile) => tile.status === 'accepted_legacy_default' || tile.status === 'accepted_expansion');
  if (!Array.isArray(manifest.tiles) || JSON.stringify(manifest.tiles.map((tile) => tile.tile_id)) !== JSON.stringify(expectedTiles.filter((tile) => manifest.tiles.some((staged) => staged.tile_id === tile.tile_id)).map((tile) => tile.tile_id))) throw new Error('pilot stage contains an unknown or incorrectly ordered tile');
  if (manifest.tiles.some((staged) => !expectedTiles.some((tile) => tile.tile_id === staged.tile_id))) throw new Error('pilot stage includes a tile without release admission');
  if (!manifest.tiles.some((tile) => tile.tile_id === contract.default_tile_id)) throw new Error('default pilot tile missing from stage');

  const allowedPaths = new Set(['_local-data/v3/pilot/staged-manifest.json', 'pilot/index.html', 'pilot/index.txt']);
  let tileAssetBytes = 0;
  for (const stagedTile of manifest.tiles) {
    const tile = contract.tiles.find((candidate) => candidate.tile_id === stagedTile.tile_id);
    if (!tile || stagedTile.status !== tile.status || JSON.stringify(stagedTile.bbox_wgs84) !== JSON.stringify(tile.bbox_wgs84)) throw new Error(`pilot tile contract drift: ${stagedTile.tile_id}`);
    const expectedRows = tile.assets.map((asset) => ({ path: asset.path, sha256: asset.sha256, bytes: asset.bytes, kind: asset.kind }));
    if (JSON.stringify(stagedTile.rows) !== JSON.stringify(expectedRows)) throw new Error(`pilot tile asset inventory drift: ${tile.tile_id}`);
    for (const asset of tile.assets) {
      const rel = `_local-data/v3/pilot/${asset.path}`;
      allowedPaths.add(rel);
      const bytes = await fs.readFile(path.join(pilotRoot, asset.path)).catch(() => { throw new Error(`pilot asset missing: ${tile.tile_id}/${asset.path}`); });
      if (bytes.length !== asset.bytes || sha256(bytes) !== asset.sha256) throw new Error(`pilot asset hash/size drift: ${tile.tile_id}/${asset.path}`);
      tileAssetBytes += bytes.length;
      if (asset.kind === 'manifest') {
        const publicManifest = JSON.parse(bytes.toString('utf8'));
        if (/\/(?:Users|private)\//.test(bytes.toString('utf8'))) throw new Error(`private filesystem path leaked through ${tile.tile_id} manifest`);
        for (const field of ['receiver_count', 'numeric_rows', 'building_count', 'facade_receiver_count']) if (publicManifest[field] !== tile[field]) throw new Error(`public pilot manifest count drift: ${tile.tile_id}.${field}`);
        if (JSON.stringify(publicManifest.masked_ids ?? []) !== JSON.stringify(tile.masked_ids)) throw new Error(`public pilot manifest masks drift: ${tile.tile_id}`);
      }
    }
  }
  if (JSON.stringify(manifest.rows) !== JSON.stringify(manifest.tiles.flatMap((tile) => tile.rows))) throw new Error('pilot staged row index differs from tile records');
  const mapRuntime = await verifyMapRuntime(path.join(root, 'maplibre'));
  const rows = await enumerateRegularTree(root);
  for (const row of rows) {
    if (row.path.startsWith('_local-data/') && !allowedPaths.has(row.path)) throw new Error(`unadmitted scientific data leaked into pilot export: ${row.path}`);
    if (row.path.startsWith('_preview-data/')) throw new Error(`preview payload leaked into pilot export: ${row.path}`);
    assertFrameworkOrAllowedPath(row.path, allowedPaths);
  }
  assertNoCredentialRows(rows);
  for (const route of ['index.html', 'pilot/index.html']) {
    const html = await fs.readFile(path.join(root, route), 'utf8');
    if (!html.includes('Quiet LA') || !html.includes('Tarzana')) throw new Error(`pilot identity/disclosure missing from ${route}`);
    if (!html.includes('noindex,nofollow,noarchive')) throw new Error(`pilot export lacks noindex meta in ${route}`);
  }
  const compact = compactRows(rows);
  const serialized = Buffer.from(`${JSON.stringify(compact)}\n`);
  return {
    profile: contract.study_id,
    tiles: manifest.tiles.map((tile) => ({ tile_id: tile.tile_id, status: tile.status, counts: tile.counts })),
    files: compact.length,
    bytes: compact.reduce((sum, row) => sum + row.bytes, 0),
    scientificAssetBytes: tileAssetBytes,
    treeSha256: sha256(serialized),
    mapRuntime,
    rows: compact,
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  verifyPilotProfileExport(process.argv[2] ? path.resolve(process.argv[2]) : defaultRoot)
    .then((result) => { const summary = { ...result }; delete summary.rows; console.log(JSON.stringify({ status: 'PILOT_PROFILE_EXPORT_VALID', ...summary }, null, 2)); })
    .catch((error) => { console.error(`verify-pilot-profile-export: ${error.message}`); process.exitCode = 1; });
}
