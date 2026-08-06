import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { verifyPrivatePreviewRoot } from './verify-private-preview-bundle.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const projectRoot = path.resolve(appRoot, '../../..');
const payloadContractPath = path.join(appRoot, 'src/data/private-preview-payload-contract.json');
const sourceManifestPath = path.join(appRoot, 'src/data/local-source-manifest.json');
const outputRoot = path.join(appRoot, 'public/_preview-data');

function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
async function lstatOrNull(target) { return fs.lstat(target).catch(() => null); }
async function assertRegularFile(target, label) {
  const stat = await lstatOrNull(target);
  if (!stat?.isFile() || stat.isSymbolicLink()) throw new Error(`${label} must be a regular non-symlink file: ${target}`);
}
async function assertNoSymlinkChain(target) {
  let cursor = path.parse(target).root;
  for (const part of path.relative(cursor, target).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const stat = await lstatOrNull(cursor);
    if (stat?.isSymbolicLink()) throw new Error(`symlink path component rejected: ${cursor}`);
  }
}

async function main() {
  const contractBytes = await fs.readFile(payloadContractPath);
  const contract = JSON.parse(contractBytes.toString('utf8'));
  const sourceManifest = JSON.parse(await fs.readFile(sourceManifestPath, 'utf8'));
  if (contract.schema !== 'quiet_la_private_preview_payload_contract_v1' || sourceManifest.schema !== 'quiet_la_web_local_source_manifest_v1') throw new Error('unexpected staging contract schema');
  if (contract.rows.length > 0 && contract.candidate !== sourceManifest.candidate) throw new Error('candidate identity drift');

  const sourceRows = new Map(sourceManifest.rows.map((row) => [row.target, row]));
  for (const row of contract.rows) {
    const source = sourceRows.get(row.target);
    if (!source || source.sha256 !== row.sha256 || source.bytes !== row.bytes) throw new Error(`private preview row is not bound to source manifest: ${row.target}`);
  }
  const selected = new Set(contract.rows.map((row) => row.target));
  for (const excluded of contract.excludedTargets) if (selected.has(excluded.target)) throw new Error(`excluded target selected: ${excluded.target}`);

  await assertNoSymlinkChain(outputRoot);
  const current = await lstatOrNull(outputRoot);
  if (current) {
    try {
      const result = await verifyPrivatePreviewRoot(outputRoot, payloadContractPath);
      console.log(JSON.stringify({ status: 'PRIVATE_PREVIEW_ALREADY_VALID', output: outputRoot, ...result }, null, 2));
      return;
    } catch (error) {
      if (!current.isDirectory() || current.isSymbolicLink()) throw error;
      console.warn(`stage-private-preview: replacing invalid generated stage: ${error.message}`);
    }
  }

  const staging = `${outputRoot}.staging-${process.pid}-${Date.now()}`;
  const superseded = `${outputRoot}.superseded-${process.pid}-${Date.now()}`;
  await fs.mkdir(staging, { recursive: true });
  try {
    const stagedRows = [];
    for (const row of contract.rows) {
      const sourceRow = sourceRows.get(row.target);
      const source = path.join(projectRoot, sourceManifest.sourceRoot, sourceRow.source);
      const target = path.join(staging, row.target);
      await assertRegularFile(source, 'private preview source');
      await fs.mkdir(path.dirname(target), { recursive: true });
      await assertNoSymlinkChain(target);
      const bytes = await fs.readFile(source);
      if (bytes.length !== row.bytes || sha256(bytes) !== row.sha256) throw new Error(`private preview source drift: ${row.target}`);
      await fs.writeFile(target, bytes, { flag: 'wx' });
      stagedRows.push(row);
    }
    const manifest = {
      schema: 'quiet_la_private_preview_staged_manifest_v1',
      profile: contract.profile,
      candidate: contract.candidate,
      contractSha256: sha256(contractBytes),
      rows: stagedRows,
    };
    await fs.writeFile(path.join(staging, 'preview-payload-manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, { flag: 'wx' });
    const stagedResult = await verifyPrivatePreviewRoot(staging, payloadContractPath);
    if (current) await fs.rename(outputRoot, superseded);
    try {
      await fs.rename(staging, outputRoot);
    } catch (error) {
      if (current) await fs.rename(superseded, outputRoot);
      throw error;
    }
    const result = await verifyPrivatePreviewRoot(outputRoot, payloadContractPath);
    if (current) await fs.rm(superseded, { recursive: true, force: true });
    if (result.contractSha256 !== stagedResult.contractSha256 || result.bytes !== stagedResult.bytes || result.files !== stagedResult.files) throw new Error('private preview changed during atomic replacement');
    console.log(JSON.stringify({ status: 'PRIVATE_PREVIEW_STAGED', output: outputRoot, ...result }, null, 2));
  } catch (error) {
    await fs.rm(staging, { recursive: true, force: true });
    const displaced = await lstatOrNull(superseded);
    if (displaced && !(await lstatOrNull(outputRoot))) await fs.rename(superseded, outputRoot);
    throw error;
  }
}

main().catch((error) => { console.error(`stage-private-preview: ${error.message}`); process.exitCode = 1; });
