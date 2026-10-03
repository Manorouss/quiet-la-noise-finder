import { promises as fs } from 'node:fs';
import path from 'node:path';
import { enumerateRegularTree, compactRows, sha256, assertNoCredentialRows } from './export-profile-policy.mjs';
import { verifyPilotProfileExport } from './verify-pilot-profile-export.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const projectRoot = path.resolve(appRoot, '../../..');
const outRoot = path.join(appRoot, 'out');
const vercelOutputRoot = path.join(appRoot, '.vercel/output');
const receiptPath = path.join(projectRoot, 'implementation/work/delivery_2026_10_02/portal/vercel_static_upload_receipt_2026-10-03.json');
const config = {
  version: 3,
  routes: [
    { src: '/pilot', status: 308, headers: { Location: '/pilot/' } },
    { src: '/(.*)', headers: { 'X-Robots-Tag': 'noindex, nofollow, noarchive', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' }, continue: true },
  ],
};

async function packageVercelPilot() {
  const verified = await verifyPilotProfileExport(outRoot);
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
    sourceManifestOriginalSha256: 'f2f47b8a1a6a2af1c7885c763b12e58b970bb563a70822f6e1cbe7cd2e2fac37',
    publicBundle: {
      benchmarkSha256: '16df6aaf99abaf0a791e5de95a49a5eda5760851994fd4636465086cffd59c93',
      buildingsSha256: 'b1f25db888ebb749751fc9b4beb2a2ab93bb6be28e23e273243e03d1ef937bd5',
      sanitizedManifestSha256: 'd733b7e11af379b2a31a1cda7559481ef5d7a175464c5d3b92c5c273c2c15769',
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
