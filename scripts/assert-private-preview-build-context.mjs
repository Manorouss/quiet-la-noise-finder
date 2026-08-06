import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const deploymentPath = process.argv[2] ? path.resolve(process.argv[2]) : path.join(appRoot, 'src/data/deployment-contract.json');
const payloadPath = process.argv[3] ? path.resolve(process.argv[3]) : path.join(appRoot, 'src/data/private-preview-payload-contract.json');
const expectedProjectId = 'prj_XTkmGuFf6novot3axeqvnZAo0Ajx';
const expectedTeamId = 'team_TNeSdFukSIvhwO0UOGDgjreq';
const expectedBranch = 'private-preview';
const expectedCandidate = 'private_preview_shell_v1';

function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
function fail(message) { throw new Error(message); }

const deployment = JSON.parse(await fs.readFile(deploymentPath, 'utf8'));
const payloadBytes = await fs.readFile(payloadPath);
const payload = JSON.parse(payloadBytes.toString('utf8'));
const admission = deployment.privatePreviewUploadAdmission;

if (deployment.externalDeploymentAuthorized !== false || deployment.publicReleaseAuthorized !== false || deployment.mapPromotionAuthorized !== false) fail('generic external/public/promotion gates must remain closed');
if (admission?.requestedByUser !== true || admission?.localBuildAuthorized !== true) fail('private-preview request/local-build authorization drift');
if (admission.authorized !== false || admission.blockedReason !== 'external_rights_records_authorize_zero_scientific_payloads_and_future_preview_protection_is_unverified') fail('private-preview upload must remain rights-and-protection-blocked');
if (admission.authorizationBasis !== 'user_directive_2026-08-05_get_the_whole_work_done') fail('private-preview user authorization basis drift');
if (admission.projectId !== expectedProjectId || admission.teamId !== expectedTeamId || admission.gitBranch !== expectedBranch || admission.target !== 'preview') fail('private-preview project/team/branch/target binding drift');
if (admission.candidate !== expectedCandidate || payload.candidate !== expectedCandidate) fail('private-preview candidate identity drift');
if (admission.payloadContractSha256 !== sha256(payloadBytes)) fail('private-preview payload contract hash is stale');
if (admission.payloadRows !== payload.rows?.length || admission.payloadBytes !== payload.rows?.reduce((sum, row) => sum + row.bytes, 0)) fail('private-preview payload count/size binding drift');
if (payload.rightsAdmission?.externalSciencePayloadsAuthorized !== false || payload.rightsAdmission?.eligibleHashAllowlist?.length !== 0) fail('private-preview science-rights gate widened');
if (admission.productionAuthorized !== false || admission.publicReleaseAuthorized !== false || admission.mapPromotionAuthorized !== false || admission.acousticCombinationAuthorized !== false) fail('private-preview scoped authorization widened');
if (deployment.previewProtection?.configured !== false || deployment.previewProtection?.externallyVerified !== false || deployment.previewProtection?.futurePreviewScopeVerified !== false || deployment.previewProtection?.existingUniqueDeploymentAuthBehaviorVerified !== true || deployment.previewProtection?.method !== 'vercel_authentication' || 'expectedApiField' in deployment.previewProtection) fail('future preview protection evidence must remain unverified until retained external proof exists');
if (payload.deploymentAdmission?.vercelProjectId !== expectedProjectId || payload.deploymentAdmission?.vercelEnvironment !== 'preview' || payload.deploymentAdmission?.gitBranch !== expectedBranch || payload.deploymentAdmission?.mainBranchPayloadBuildAllowed !== false || payload.deploymentAdmission?.productionPayloadBuildAllowed !== false) fail('payload deployment boundary drift');

const vercelEnvironment = process.env.VERCEL_ENV;
const gitBranch = process.env.VERCEL_GIT_COMMIT_REF ?? process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_REF;
if (vercelEnvironment === 'production') fail('private-preview payload build is forbidden in Vercel production');

if (process.env.VERCEL === '1') {
  if (vercelEnvironment !== 'preview') fail(`private-preview Vercel build requires VERCEL_ENV=preview, received ${vercelEnvironment ?? 'unset'}`);
  if (gitBranch !== expectedBranch) fail(`private-preview Vercel build requires branch ${expectedBranch}, received ${gitBranch ?? 'unset'}`);
  if (process.env.VERCEL_PROJECT_ID && process.env.VERCEL_PROJECT_ID !== expectedProjectId) fail('private-preview Vercel project identity differs from the admitted project');
  if (process.env.VERCEL_ORG_ID && process.env.VERCEL_ORG_ID !== expectedTeamId) fail('private-preview Vercel team identity differs from the admitted team');
  if (admission.authorized !== true || payload.rightsAdmission.externalSciencePayloadsAuthorized !== true || payload.rightsAdmission.eligibleHashAllowlist.length < 1) fail('private-preview Vercel build is blocked until science payload rights are admitted');
  console.log(JSON.stringify({ status: 'PRIVATE_PREVIEW_VERCEL_CONTEXT_PASS', projectId: expectedProjectId, teamId: expectedTeamId, environment: vercelEnvironment, gitBranch, payloadContractSha256: admission.payloadContractSha256 }, null, 2));
} else {
  console.log(JSON.stringify({ status: 'PRIVATE_PREVIEW_LOCAL_BUILD_CONTEXT', privatePreviewUploadAuthorized: false, rightsBlocked: true, externalUploadPerformed: false, expectedProjectId, expectedTeamId, expectedBranch, payloadContractSha256: admission.payloadContractSha256 }, null, 2));
}
