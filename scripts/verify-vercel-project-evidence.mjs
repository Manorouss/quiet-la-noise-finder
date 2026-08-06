import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const appRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const projectRoot = path.resolve(appRoot, '../../..');
const defaultEvidenceIndex = path.join(
  projectRoot,
  'implementation/work/release_packaging/external_platform_evidence_2026-08-06/EVIDENCE_INDEX.json',
);
const deploymentContractPath = path.join(appRoot, 'src/data/deployment-contract.json');

const expected = Object.freeze({
  teamId: 'team_TNeSdFukSIvhwO0UOGDgjreq',
  projectId: 'prj_XTkmGuFf6novot3axeqvnZAo0Ajx',
  deploymentId: 'dpl_C4wJodBMZawFVYevVtYSdbE8Tbcc',
  repository: 'Manorouss/quiet-la-noise-finder',
  commit: 'c77306608091fb22bc01e57f65df2fe615fe9ada',
  previewBranch: 'private-preview',
  uniqueUrl: 'https://quiet-la-noise-finder-ehpmce16z-manouks-projects.vercel.app/',
  productionUrl: 'https://quiet-la-noise-finder.vercel.app/',
  manifestUrl: 'https://quiet-la-noise-finder.vercel.app/_preview-data/preview-payload-manifest.json',
  indexSha256: 'bb0a74e01e3d2feaf01a101a59cb697a45cc31ecc636c2b6e2efa9bd2e1c1c80',
  ownerSha256: '535255317d81bbec9e05e6577d5724798013a9d7608562a0a02e84044f690963',
});

const expectedRecords = Object.freeze({
  vercel_project: ['vercel_project_response.json', 582, 'ea4c207d5939958de97a240f78d1c6287ab308f319de4bcf7c15f3276ce02fe2'],
  vercel_deployments: ['vercel_deployments_response.json', 1273, 'bc3282ff310147c2158bffe863945e62ff4529ba2e0640fec0bb0a411e0b2a5d'],
  github_repository: ['github_repository_response.json', 645, '4ea241f9116f54bf207cc31439a66ed15ed367d495b4bb7ffcf96481b707f3c1'],
  github_main_branch: ['github_main_branch_response.json', 47, '2e0406aa2e841b2207de45e87d727cd13d456a5c6de7a6e6af56c81e56749fbc'],
  github_private_preview_branch: ['github_private_preview_branch_response.json', 30, 'eef62faa2c5cd2cdb12396edb4c77eeec3987c6111800029f5b64bae53615a6a'],
  github_latest_commit: ['github_latest_commit_response.json', 417, 'f7093876e2b968333a7c379ce2990c1676cf6b45f01491f3cc930330a1378d13'],
  unique_deployment_unauthenticated_http: ['unique_deployment_http_receipt.json', 724, '2daf4c5b59833cfe26f40b9a72957010b07f024fc04fad68db79b1f91cb55da1'],
  production_alias_unauthenticated_http: ['production_alias_http_receipt.json', 662, '4de1c7e31f18307d32d4bd536d3f3ba820cb6541ad2e2aefeaad3cbd15f8f8b5'],
  production_payload_manifest_unauthenticated_http: ['production_payload_manifest_http_receipt.json', 702, '888cda0b9211427f9765499046c7a5ce0c08ae273bef4e2a18674fca9bbecb77'],
});

function fail(message) { throw new Error(message); }
function digest(bytes) { return createHash('sha256').update(bytes).digest('hex'); }

async function assertRegularPath(file) {
  const absolute = path.resolve(file);
  const parsed = path.parse(absolute);
  let current = parsed.root;
  for (const part of absolute.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    const status = await fs.lstat(current);
    if (status.isSymbolicLink()) fail(`symlinked evidence path component: ${current}`);
  }
  const status = await fs.lstat(absolute);
  if (!status.isFile()) fail(`evidence is not a regular file: ${absolute}`);
  return absolute;
}

async function readBoundJson(file, sha256, bytes) {
  const absolute = await assertRegularPath(file);
  const raw = await fs.readFile(absolute);
  if (raw.byteLength !== bytes || digest(raw) !== sha256) fail(`evidence hash/size drift: ${absolute}`);
  const value = JSON.parse(raw.toString('utf8'));
  if (!value || Array.isArray(value) || typeof value !== 'object') fail(`evidence JSON object required: ${absolute}`);
  return value;
}

function allFalse(object, label) {
  if (!object || Array.isArray(object) || Object.keys(object).length === 0) fail(`${label} missing`);
  if (Object.values(object).some((value) => value !== false)) fail(`${label} widened`);
}

export async function verifyEvidenceIndex(indexPath = defaultEvidenceIndex) {
  const absoluteIndex = await assertRegularPath(indexPath);
  const indexRaw = await fs.readFile(absoluteIndex);
  if (digest(indexRaw) !== expected.indexSha256) fail('external evidence index hash drift');
  const index = JSON.parse(indexRaw.toString('utf8'));
  if (index.schema !== 'quiet_la_external_platform_evidence_index_v1' || index.owner !== 'external_platform_read_only_capture') fail('external evidence index identity drift');
  if (index.connector_capture?.external_mutation_performed !== false) fail('external evidence capture claims a mutation');
  allFalse(index.authorizations, 'external evidence authorizations');

  const evidenceRoot = path.dirname(absoluteIndex);
  const owner = await readBoundJson(path.join(evidenceRoot, 'OWNER.json'), expected.ownerSha256, 345);
  if (owner.owner !== index.owner || owner.external_mutation_authorized !== false) fail('external evidence owner contract drift');

  const records = index.records;
  if (!Array.isArray(records) || records.length !== Object.keys(expectedRecords).length) fail('external evidence record count drift');
  const byId = new Map(records.map((row) => [row.id, row]));
  if (byId.size !== records.length || [...byId.keys()].some((id) => !(id in expectedRecords))) fail('external evidence record identity drift');
  const documents = {};
  for (const [id, [relative, bytes, sha256]] of Object.entries(expectedRecords)) {
    const row = byId.get(id);
    if (!row || row.path !== relative || row.bytes !== bytes || row.sha256 !== sha256) fail(`external evidence binding drift: ${id}`);
    if (typeof row.captured_at_utc !== 'string' || !row.captured_at_utc.endsWith('Z') || typeof row.tool !== 'string' || !row.arguments) fail(`external evidence receipt metadata missing: ${id}`);
    if (path.isAbsolute(relative) || relative.includes('..')) fail(`unsafe evidence path: ${relative}`);
    documents[id] = await readBoundJson(path.join(evidenceRoot, relative), sha256, bytes);
  }

  const project = documents.vercel_project;
  if (project.id !== expected.projectId || project.accountId !== expected.teamId || project.name !== 'quiet-la-noise-finder' || project.framework !== 'nextjs') fail('Vercel project identity mismatch');
  if (project.latestDeployment?.id !== expected.deploymentId || project.latestDeployment?.target !== 'production') fail('Vercel project deployment pointer mismatch');

  const deploymentRows = documents.vercel_deployments?.deployments?.deployments;
  if (!Array.isArray(deploymentRows) || deploymentRows.length !== 1) fail('Vercel deployment count does not prove no-preview state');
  const deployment = deploymentRows[0];
  if (deployment.id !== expected.deploymentId || deployment.target !== 'production' || deployment.state !== 'READY') fail('Vercel deployment identity/target mismatch');
  if (deployment.meta?.githubCommitRef !== 'main' || deployment.meta?.githubCommitSha !== expected.commit || deployment.meta?.githubRepoVisibility !== 'private') fail('Vercel deployment Git binding mismatch');
  if (deploymentRows.some((row) => row.target === 'preview' || row.meta?.githubCommitRef === expected.previewBranch)) fail('private-preview deployment unexpectedly exists');

  const repositories = documents.github_repository?.repositories;
  if (!Array.isArray(repositories) || repositories.length !== 1) fail('GitHub repository receipt shape mismatch');
  const repository = repositories[0];
  if (repository.repository_full_name !== expected.repository || repository.visibility !== 'private' || repository.default_branch !== 'main' || repository.archived !== false) fail('GitHub repository privacy/identity mismatch');
  if (JSON.stringify(documents.github_main_branch) !== JSON.stringify({ branches: [{ branch: 'main' }], cursor: 'MQ' })) fail('GitHub main branch receipt mismatch');
  if (JSON.stringify(documents.github_private_preview_branch) !== JSON.stringify({ branches: [], cursor: null })) fail('GitHub private-preview absence receipt mismatch');
  const commit = documents.github_latest_commit;
  if (commit.sha !== expected.commit || commit.repository_full_name !== expected.repository || commit.message !== 'Initialize Quiet LA web portal') fail('GitHub main commit receipt mismatch');

  const unique = documents.unique_deployment_unauthenticated_http;
  if (unique.request?.url !== expected.uniqueUrl || unique.response?.status !== 302 || unique.response?.redirect_origin !== 'https://vercel.com' || unique.response?.redirect_path !== '/sso-api' || unique.response?.x_robots_tag !== 'noindex') fail('Vercel Authentication/noindex behavior receipt mismatch');
  const production = documents.production_alias_unauthenticated_http;
  if (production.request?.url !== expected.productionUrl || production.response?.status !== 200 || !production.response?.body_markers?.includes('Map awaiting local v3 data')) fail('payload-free public production alias receipt mismatch');
  const manifest = documents.production_payload_manifest_unauthenticated_http;
  if (manifest.request?.url !== expected.manifestUrl || manifest.response?.status !== 404) fail('production payload-absence receipt mismatch');

  const deploymentContract = JSON.parse(await fs.readFile(deploymentContractPath, 'utf8'));
  if (deploymentContract.vercelBinding?.expectedProjectId !== expected.projectId || deploymentContract.vercelBinding?.expectedTeamId !== expected.teamId || deploymentContract.vercelBinding?.expectedPreviewBranch !== expected.previewBranch) fail('local Vercel binding contract mismatch');
  if (deploymentContract.githubBinding?.expectedRepository !== expected.repository || deploymentContract.githubBinding?.privateRequired !== true) fail('local GitHub binding contract mismatch');
  if (deploymentContract.externalDeploymentAuthorized !== false || deploymentContract.publicReleaseAuthorized !== false || deploymentContract.mapPromotionAuthorized !== false) fail('local deployment authorization widened');
  if (deploymentContract.productionBoundary?.productionPayloadBuildAllowed !== false || deploymentContract.productionBoundary?.mainBranchPayloadBuildAllowed !== false) fail('production payload boundary widened');
  const admission = deploymentContract.privatePreviewUploadAdmission;
  if (admission?.authorized !== false || admission?.projectId !== expected.projectId || admission?.teamId !== expected.teamId || admission?.gitBranch !== expected.previewBranch || admission?.target !== 'preview') fail('rights-blocked private-preview binding failed');
  if (admission.productionAuthorized !== false || admission.publicReleaseAuthorized !== false || admission.mapPromotionAuthorized !== false || admission.acousticCombinationAuthorized !== false) fail('private-preview authorization widened');
  const protection = deploymentContract.previewProtection;
  if (protection?.configured !== false || protection?.externallyVerified !== false || protection?.futurePreviewScopeVerified !== false || protection?.existingUniqueDeploymentAuthBehaviorVerified !== true || 'expectedApiField' in protection) fail('future preview protection is self-certified or widened');
  if (deploymentContract.requiredExternalPredicates?.vercelAuthenticationStandardProtectionVerified !== false || deploymentContract.requiredExternalPredicates?.existingUniqueDeploymentAuthBehaviorVerified !== true) fail('future preview protection predicate drift');

  return {
    status: 'EXTERNAL_CONTROL_PLANE_EVIDENCE_PASS_RIGHTS_BLOCKED',
    evidenceIndexSha256: expected.indexSha256,
    projectId: expected.projectId,
    repository: expected.repository,
    deploymentCount: 1,
    previewDeploymentCreated: false,
    scientificPayloadDeployed: false,
    existingUniqueDeploymentAuthBehaviorVerified: true,
    futurePreviewProtectionConfigured: false,
    uniqueDeploymentUnauthenticatedStatus: 302,
    uniqueDeploymentNoindex: true,
    productionAliasStatus: 200,
    productionPayloadManifestStatus: 404,
  };
}

async function main() {
  const [indexPath] = process.argv.slice(2);
  console.log(JSON.stringify(await verifyEvidenceIndex(indexPath || defaultEvidenceIndex), null, 2));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(`verify-vercel-project-evidence: ${error.message}`);
    process.exitCode = 1;
  });
}
