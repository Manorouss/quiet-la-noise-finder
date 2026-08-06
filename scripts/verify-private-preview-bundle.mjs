import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const contractPath = path.join(appRoot, 'src/data/private-preview-payload-contract.json');

function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
async function lstatOrNull(target) { return fs.lstat(target).catch(() => null); }

async function assertNoSymlinkChain(target) {
  let cursor = path.parse(target).root;
  for (const part of path.relative(cursor, target).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const stat = await lstatOrNull(cursor);
    if (stat?.isSymbolicLink()) throw new Error(`symlink path component: ${cursor}`);
  }
}

async function walk(root, current = root) {
  const files = [];
  for (const entry of await fs.readdir(current, { withFileTypes: true })) {
    const target = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`symlink preview entry: ${target}`);
    if (entry.isDirectory()) files.push(...await walk(root, target));
    else files.push(path.relative(root, target).split(path.sep).join('/'));
  }
  return files;
}

export async function verifyPrivatePreviewRoot(root, selectedContractPath = contractPath) {
  await assertNoSymlinkChain(root);
  const rootStat = await lstatOrNull(root);
  if (!rootStat?.isDirectory() || rootStat.isSymbolicLink()) throw new Error('private preview root is not a regular directory');

  const contractBytes = await fs.readFile(selectedContractPath);
  const contract = JSON.parse(contractBytes.toString('utf8'));
  if (contract.schema !== 'quiet_la_private_preview_payload_contract_v1') throw new Error('unexpected private preview contract schema');
  if (contract.profile !== 'private_preview_v1' || contract.admission.privatePreviewOnly !== true) throw new Error('private preview admission boundary drift');
  if (contract.candidate !== 'private_preview_shell_v1' || contract.status !== 'LOCAL_SHELL_VALIDATED_SCIENTIFIC_PAYLOAD_RIGHTS_BLOCKED') throw new Error('private preview shell status drift');
  if (contract.rightsAdmission?.externalSciencePayloadsAuthorized !== false || !Array.isArray(contract.rightsAdmission?.eligibleHashAllowlist) || contract.rightsAdmission.eligibleHashAllowlist.length !== 0) throw new Error('science payload rights gate widened');
  if (contract.deploymentAdmission?.vercelProjectId !== 'prj_XTkmGuFf6novot3axeqvnZAo0Ajx' || contract.deploymentAdmission?.vercelEnvironment !== 'preview' || contract.deploymentAdmission?.gitBranch !== 'private-preview') throw new Error('private preview target identity drift');
  if (contract.deploymentAdmission.mainBranchPayloadBuildAllowed !== false || contract.deploymentAdmission.productionPayloadBuildAllowed !== false) throw new Error('private preview production boundary widened');
  for (const flag of ['publicReleaseAuthorized', 'acousticCombinationAdmitted', 'source341CorrectionAdmitted', 'source341EvidenceIncluded', 'unresolvedLicensedPayloadsIncluded', 'rawEvidenceIncluded', 'runtimeDatabasesIncluded', 'credentialsIncluded']) {
    if (contract.admission[flag] !== false) throw new Error(`private preview gate widened: ${flag}`);
  }

  const manifestPath = path.join(root, 'preview-payload-manifest.json');
  const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
  if (manifest.schema !== 'quiet_la_private_preview_staged_manifest_v1') throw new Error('unexpected private preview manifest schema');
  if (manifest.profile !== contract.profile || manifest.candidate !== contract.candidate) throw new Error('private preview manifest identity drift');
  if (manifest.contractSha256 !== sha256(contractBytes)) throw new Error('private preview contract hash drift');

  const expected = new Map(contract.rows.map((row) => [row.target, row]));
  const expectedFiles = [...expected.keys(), 'preview-payload-manifest.json'].sort();
  const actualFiles = (await walk(root)).sort();
  if (JSON.stringify(actualFiles) !== JSON.stringify(expectedFiles)) throw new Error('private preview file set differs from allowlist');
  if (!Array.isArray(manifest.rows) || manifest.rows.length !== expected.size) throw new Error('private preview manifest row count drift');

  let total = 0;
  for (const [relative, row] of expected) {
    const target = path.join(root, relative);
    await assertNoSymlinkChain(target);
    const stat = await lstatOrNull(target);
    if (!stat?.isFile() || stat.isSymbolicLink()) throw new Error(`private preview target is not a regular file: ${relative}`);
    const bytes = await fs.readFile(target);
    if (bytes.length !== row.bytes || sha256(bytes) !== row.sha256) throw new Error(`private preview hash/size drift: ${relative}`);
    const staged = manifest.rows.find((candidate) => candidate.target === relative);
    if (!staged || staged.sha256 !== row.sha256 || staged.bytes !== row.bytes || staged.classification !== row.classification) throw new Error(`private preview staged manifest drift: ${relative}`);
    total += bytes.length;
  }
  if (total !== 0 || contract.maximumPayloadBytes !== 0 || total > contract.maximumPayloadBytes) throw new Error(`private preview payload size is not admitted: ${total}`);
  for (const excluded of contract.excludedTargets) {
    if (await lstatOrNull(path.join(root, excluded.target))) throw new Error(`excluded target present: ${excluded.target}`);
  }

  return { files: expected.size, bytes: total, contractSha256: sha256(contractBytes) };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const root = process.argv[2] ? path.resolve(process.argv[2]) : path.join(appRoot, 'public/_preview-data');
  verifyPrivatePreviewRoot(root).then((result) => console.log(JSON.stringify({ status: 'PRIVATE_PREVIEW_BUNDLE_VALID', ...result }, null, 2))).catch((error) => { console.error(`verify-private-preview-bundle: ${error.message}`); process.exitCode = 1; });
}
