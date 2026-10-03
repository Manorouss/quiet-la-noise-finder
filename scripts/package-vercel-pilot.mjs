import { promises as fs } from 'node:fs';
import path from 'node:path';
import { enumerateRegularTree, compactRows, sha256, assertNoCredentialRows } from './export-profile-policy.mjs';
import { verifyPilotProfileExport } from './verify-pilot-profile-export.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const projectRoot = path.resolve(appRoot, '../../..');
const outRoot = path.join(appRoot, 'out');
const vercelOutputRoot = path.join(appRoot, '.vercel/output');
const receiptPath = path.join(projectRoot, 'implementation/work/delivery_2026_10_02/portal/vercel_static_upload_receipt_2026-10-03.json');
const releaseContractPath = path.join(appRoot, 'src/data/pilot-release-contract.json');
if (process.argv.length > 2) throw new Error(`unsupported package-vercel-pilot arguments: ${process.argv.slice(2).join(' ')}`);
const config = {
  version: 3,
  routes: [
    { src: '/pilot', status: 308, headers: { Location: '/pilot/' } },
    { src: '/(.*)', headers: { 'X-Robots-Tag': 'noindex, nofollow, noarchive', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'strict-origin-when-cross-origin' }, continue: true },
  ],
};

async function packageVercelPilot() {
  const verified = await verifyPilotProfileExport(outRoot);
  const contract = JSON.parse(await fs.readFile(releaseContractPath, 'utf8'));
  const defaultTile = contract.tiles.find((tile) => tile.tile_id === contract.default_tile_id);
  if (!defaultTile || defaultTile.status !== 'accepted_legacy_default') throw new Error('Vercel pilot default tile is not admitted');
  const outputStat = await fs.lstat(vercelOutputRoot).catch(() => null);
  if (outputStat && (!outputStat.isDirectory() || outputStat.isSymbolicLink())) throw new Error('Vercel output path must be a regular directory');
  await fs.rm(vercelOutputRoot, { recursive: true, force: true });
  const staticRoot = path.join(vercelOutputRoot, 'static');
  await fs.mkdir(staticRoot, { recursive: true });
  await fs.cp(outRoot, staticRoot, { recursive: true, errorOnExist: true, force: false });
  await fs.writeFile(path.join(vercelOutputRoot, 'config.json'), `${JSON.stringify(config, null, 2)}\n`, { flag: 'wx' });

  const staticRows = await enumerateRegularTree(staticRoot);
  const expectedStaticRows = verified.rows;
  if (JSON.stringify(compactRows(staticRows)) !== JSON.stringify(expectedStaticRows)) throw new Error('Vercel static directory differs from the verified pilot export');
  const rows = await enumerateRegularTree(vercelOutputRoot);
  assertNoCredentialRows(rows);
  const compact = compactRows(rows);
  const upload = { schema: 'quiet_la_vercel_build_output_v3', status: 'VERIFIED_PREBUILT_STATIC_TREE', profile: 'pilot_v1', files: compact.length, bytes: compact.reduce((sum, row) => sum + row.bytes, 0), treeSha256: sha256(Buffer.from(`${JSON.stringify(compact)}\n`)), rows: compact };
  const receipt = {
    schema: 'quiet_la_tarzana_hosted_pilot_receipt_v1',
    admissionStatus: 'accepted_release',
    sourceManifestOriginalSha256: defaultTile.provenance.source_manifest_original_sha256,
    publicBundle: {
      assets: Object.fromEntries(defaultTile.assets.map((asset) => [asset.kind, { sha256: asset.sha256, bytes: asset.bytes }])),
      tiles: verified.tiles.map((verifiedTile) => {
        const tile = contract.tiles.find((entry) => entry.tile_id === verifiedTile.tile_id);
        return { tileId: tile.tile_id, admissionStatus: tile.status, assets: Object.fromEntries(tile.assets.map((asset) => [asset.kind, { path: asset.path, sha256: asset.sha256, bytes: asset.bytes }])) };
      }),
    },
    verifiedExport: { files: verified.files, bytes: verified.bytes, treeSha256: verified.treeSha256 },
    vercelUpload: upload,
  };
  await fs.mkdir(path.dirname(receiptPath), { recursive: true });
  await fs.writeFile(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`);
  console.log(JSON.stringify({ ...receipt, output: vercelOutputRoot, receipt: receiptPath }, null, 2));
  return receipt;
}

packageVercelPilot().catch((error) => { console.error(`package-vercel-pilot: ${error.message}`); process.exitCode = 1; });
