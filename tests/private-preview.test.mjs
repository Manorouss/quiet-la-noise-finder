import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { verifyPrivatePreviewRoot } from '../scripts/verify-private-preview-bundle.mjs';
import { verifyPrivatePreviewSourceTree, verifyPreviewPublicTree } from '../scripts/verify-private-preview-source-tree.mjs';
import { assertAllowedPrivatePreviewExportPath, verifyPrivatePreviewExport } from '../scripts/verify-private-preview-export.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const previewRoot = path.join(appRoot, 'public/_preview-data');
const payloadContractPath = path.join(appRoot, 'src/data/private-preview-payload-contract.json');

async function copyPreview() {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-preview-'));
  const copy = path.join(temp, 'stage');
  await fs.cp(previewRoot, copy, { recursive: true, dereference: false });
  return { temp, copy };
}

async function expectFailure(mutator, pattern) {
  const { temp, copy } = await copyPreview();
  try {
    await mutator(copy);
    await assert.rejects(() => verifyPrivatePreviewRoot(copy), pattern);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
}

async function runNode(script, args = [], env = {}) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [path.join(appRoot, script), ...args], { cwd: appRoot, env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = ''; let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

test('private preview has an exact zero-byte scientific payload allowlist', async () => {
  assert.deepEqual(await verifyPrivatePreviewRoot(previewRoot), {
    files: 0,
    bytes: 0,
    contractSha256: '1ed88072d9d413d883da2b406a25292f0d855205743238106424c68f3fd2062e',
  });
});

test('private preview rejects tamper, extra files, and symlinks', async () => {
  await expectFailure(async (copy) => {
    const manifestPath = path.join(copy, 'preview-payload-manifest.json');
    const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
    manifest.candidate = 'tampered';
    await fs.writeFile(manifestPath, JSON.stringify(manifest));
  }, /manifest identity drift/);
  await expectFailure(async (copy) => { await fs.writeFile(path.join(copy, 'extra.json'), '{}'); }, /file set/);
  await expectFailure(async (copy) => {
    await fs.symlink(path.join(appRoot, 'src/data/layer-contract.json'), path.join(copy, 'unexpected-link.json'));
  }, /symlink/);
});

test('private preview contract excludes unresolved and local-evidence artifacts', async () => {
  const contract = JSON.parse(await fs.readFile(payloadContractPath, 'utf8'));
  assert.equal(contract.maximumPayloadBytes, 0);
  assert.deepEqual(contract.deploymentAdmission, {
    vercelProjectId: 'prj_XTkmGuFf6novot3axeqvnZAo0Ajx',
    vercelEnvironment: 'preview',
    gitBranch: 'private-preview',
    mainBranchPayloadBuildAllowed: false,
    productionPayloadBuildAllowed: false,
  });
  assert.deepEqual(contract.rows, []);
  assert.deepEqual(contract.rightsAdmission.eligibleHashAllowlist, []);
  assert.equal(contract.rightsAdmission.externalSciencePayloadsAuthorized, false);
  const excluded = new Set(contract.excludedTargets.map((row) => row.target));
  for (const target of ['v3/four-region-relative.json.gz', 'v3/tarzana-scenario.json', 'v3/context/airport-contours.json.gz', 'v3/context/rail.json', 'v3/context/source341-mask.json', 'v3/source-bindings.json', 'v3/unified-layer-manifest.json']) assert.equal(excluded.has(target), true);
  assert.deepEqual(contract.admission, {
    privatePreviewOnly: true,
    publicReleaseAuthorized: false,
    acousticCombinationAdmitted: false,
    source341CorrectionAdmitted: false,
    source341EvidenceIncluded: false,
    unresolvedLicensedPayloadsIncluded: false,
    rawEvidenceIncluded: false,
    runtimeDatabasesIncluded: false,
    credentialsIncluded: false,
  });
});

test('deployment contract distinguishes existing deployment auth behavior from unverified future-preview protection', async () => {
  const deployment = JSON.parse(await fs.readFile(path.join(appRoot, 'src/data/deployment-contract.json'), 'utf8'));
  assert.equal(deployment.schema, 'quiet_la_web_deployment_contract_v2');
  assert.equal('expectedApiField' in deployment.previewProtection, false);
  assert.equal(deployment.previewProtection.configured, false);
  assert.equal(deployment.previewProtection.externallyVerified, false);
  assert.equal(deployment.previewProtection.futurePreviewScopeVerified, false);
  assert.equal(deployment.previewProtection.existingUniqueDeploymentAuthBehaviorVerified, true);
  assert.equal(deployment.vercelBinding.expectedProjectId, 'prj_XTkmGuFf6novot3axeqvnZAo0Ajx');
  assert.equal(deployment.vercelBinding.expectedPreviewBranch, 'private-preview');
  assert.equal(deployment.externalDeploymentAuthorized, false);
  assert.equal(deployment.publicReleaseAuthorized, false);
  assert.equal(deployment.mapPromotionAuthorized, false);
  assert.deepEqual(deployment.privatePreviewUploadAdmission, {
    requestedByUser: true,
    localBuildAuthorized: true,
    authorized: false,
    authorizationBasis: 'user_directive_2026-08-05_get_the_whole_work_done',
    blockedReason: 'external_rights_records_authorize_zero_scientific_payloads_and_future_preview_protection_is_unverified',
    candidate: 'private_preview_shell_v1',
    payloadContractSha256: '1ed88072d9d413d883da2b406a25292f0d855205743238106424c68f3fd2062e',
    payloadRows: 0,
    payloadBytes: 0,
    teamId: 'team_TNeSdFukSIvhwO0UOGDgjreq',
    projectId: 'prj_XTkmGuFf6novot3axeqvnZAo0Ajx',
    gitBranch: 'private-preview',
    target: 'preview',
    protectionMethod: 'vercel_authentication_standard_protection',
    productionAuthorized: false,
    publicReleaseAuthorized: false,
    mapPromotionAuthorized: false,
    acousticCombinationAuthorized: false,
  });
  assert.deepEqual(deployment.requiredExternalPredicates, {
    vercelProjectCreated: true,
    vercelAuthenticationStandardProtectionVerified: false,
    existingUniqueDeploymentAuthBehaviorVerified: true,
    githubPrivateBindingVerified: true,
    previewDeploymentCreated: false,
    unauthenticatedAccessRejected: false,
    privateSoakStarted: false,
    twoKeyMapPromotionApproved: false,
  });
  assert.equal(deployment.productionBoundary.productionPayloadBuildAllowed, false);
  assert.equal(deployment.productionBoundary.mainBranchPayloadBuildAllowed, false);
});

test('external project verifier consumes retained hash-bound receipts and rejects drift', async () => {
  const evidenceRoot = path.resolve(appRoot, '../../work/release_packaging/external_platform_evidence_2026-08-06');
  const evidenceIndex = path.join(evidenceRoot, 'EVIDENCE_INDEX.json');
  const actual = await runNode('scripts/verify-vercel-project-evidence.mjs', [evidenceIndex]);
  assert.equal(actual.code, 0, actual.stderr);
  const verified = JSON.parse(actual.stdout);
  assert.deepEqual(verified, {
    status: 'EXTERNAL_CONTROL_PLANE_EVIDENCE_PASS_RIGHTS_BLOCKED',
    evidenceIndexSha256: 'bb0a74e01e3d2feaf01a101a59cb697a45cc31ecc636c2b6e2efa9bd2e1c1c80',
    projectId: 'prj_XTkmGuFf6novot3axeqvnZAo0Ajx',
    repository: 'Manorouss/quiet-la-noise-finder',
    deploymentCount: 1,
    previewDeploymentCreated: false,
    scientificPayloadDeployed: false,
    existingUniqueDeploymentAuthBehaviorVerified: true,
    futurePreviewProtectionConfigured: false,
    uniqueDeploymentUnauthenticatedStatus: 302,
    uniqueDeploymentNoindex: true,
    productionAliasStatus: 200,
    productionPayloadManifestStatus: 404,
  });

  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-preview-evidence-'));
  try {
    const copy = path.join(temp, 'evidence');
    await fs.cp(evidenceRoot, copy, { recursive: true, dereference: false });
    const copiedIndex = path.join(copy, 'EVIDENCE_INDEX.json');
    const projectReceipt = path.join(copy, 'vercel_project_response.json');
    await fs.appendFile(projectReceipt, ' ');
    assert.match((await runNode('scripts/verify-vercel-project-evidence.mjs', [copiedIndex])).stderr, /hash\/size drift/);
    await fs.cp(evidenceRoot, copy, { recursive: true, dereference: false, force: true });
    await fs.rm(path.join(copy, 'unique_deployment_http_receipt.json'));
    assert.match((await runNode('scripts/verify-vercel-project-evidence.mjs', [copiedIndex])).stderr, /ENOENT|no such file/i);
    await fs.cp(evidenceRoot, copy, { recursive: true, dereference: false, force: true });
    const index = JSON.parse(await fs.readFile(copiedIndex, 'utf8'));
    index.derived_facts.private_preview_deployment_exists = true;
    await fs.writeFile(copiedIndex, JSON.stringify(index));
    assert.match((await runNode('scripts/verify-vercel-project-evidence.mjs', [copiedIndex])).stderr, /index hash drift/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('default build and direct CLI upload fail closed while only the preview branch is enabled', async () => {
  const refused = await runNode('scripts/refuse-unprofiled-build.mjs');
  assert.equal(refused.code, 1);
  assert.match(refused.stderr, /refused unprofiled external build/);
  const ignore = await fs.readFile(path.join(appRoot, '.vercelignore'), 'utf8');
  assert.match(ignore, /^\/\*/m);
  assert.doesNotMatch(ignore, /^!/m);
  const config = JSON.parse(await fs.readFile(path.join(appRoot, 'vercel.json'), 'utf8'));
  assert.equal(config.buildCommand, 'npm run build:vercel-profile');
  assert.equal(config.outputDirectory, 'out');
  assert.equal(config.headers[0].headers.find((row) => row.key === 'X-Robots-Tag').value, 'noindex, nofollow, noarchive');
  assert.equal(config.git.deploymentEnabled.main, false);
  assert.equal(config.git.deploymentEnabled['private-preview'], true);
});

test('private payload build guard rejects closed/tampered admission, production, main, and wrong identities', async () => {
  const script = 'scripts/assert-private-preview-build-context.mjs';
  assert.equal((await runNode(script)).code, 0);
  assert.match((await runNode(script, [], { VERCEL: '1', VERCEL_ENV: 'production', VERCEL_GIT_COMMIT_REF: 'private-preview' })).stderr, /forbidden in Vercel production/);
  assert.match((await runNode(script, [], { VERCEL: '1', VERCEL_ENV: 'preview', VERCEL_GIT_COMMIT_REF: 'main' })).stderr, /requires branch private-preview/);
  assert.match((await runNode(script, [], { VERCEL: '1', VERCEL_ENV: 'preview', VERCEL_GIT_COMMIT_REF: 'private-preview', VERCEL_PROJECT_ID: 'prj_wrong' })).stderr, /project identity differs/);
  assert.match((await runNode(script, [], { VERCEL: '1', VERCEL_ENV: 'preview', VERCEL_GIT_COMMIT_REF: 'private-preview', VERCEL_ORG_ID: 'team_wrong' })).stderr, /team identity differs/);
  const rightsBlocked = await runNode(script, [], { VERCEL: '1', VERCEL_ENV: 'preview', VERCEL_GIT_COMMIT_REF: 'private-preview', VERCEL_PROJECT_ID: 'prj_XTkmGuFf6novot3axeqvnZAo0Ajx', VERCEL_ORG_ID: 'team_TNeSdFukSIvhwO0UOGDgjreq' });
  assert.equal(rightsBlocked.code, 1);
  assert.match(rightsBlocked.stderr, /blocked until science payload rights are admitted/);

  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-build-admission-'));
  try {
    const deployment = JSON.parse(await fs.readFile(path.join(appRoot, 'src/data/deployment-contract.json'), 'utf8'));
    const payload = path.join(appRoot, 'src/data/private-preview-payload-contract.json');
    const deploymentCopy = path.join(temp, 'deployment.json');
    deployment.privatePreviewUploadAdmission.authorized = true;
    await fs.writeFile(deploymentCopy, JSON.stringify(deployment));
    assert.match((await runNode(script, [deploymentCopy, payload])).stderr, /upload must remain rights-and-protection-blocked/);
    deployment.privatePreviewUploadAdmission.authorized = false;
    deployment.privatePreviewUploadAdmission.payloadContractSha256 = '0'.repeat(64);
    await fs.writeFile(deploymentCopy, JSON.stringify(deployment));
    assert.match((await runNode(script, [deploymentCopy, payload])).stderr, /contract hash is stale/);
    deployment.privatePreviewUploadAdmission.payloadContractSha256 = '1ed88072d9d413d883da2b406a25292f0d855205743238106424c68f3fd2062e';
    deployment.previewProtection.configured = true;
    deployment.previewProtection.externallyVerified = true;
    deployment.previewProtection.futurePreviewScopeVerified = true;
    deployment.previewProtection.expectedApiField = { 'ssoProtection.deploymentType': 'prod_deployment_urls_and_all_previews' };
    await fs.writeFile(deploymentCopy, JSON.stringify(deployment));
    assert.match((await runNode(script, [deploymentCopy, payload])).stderr, /future preview protection evidence must remain unverified/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('private preview UI uses the dedicated root and disables unresolved context layers', async () => {
  const source = await fs.readFile(path.join(appRoot, 'src/app/page.tsx'), 'utf8');
  const runtimeSource = await fs.readFile(path.join(appRoot, 'src/lib/runtime-profile.js'), 'utf8');
  assert.match(source, /NEXT_PUBLIC_QUIET_LA_DATA_PROFILE/);
  assert.match(runtimeSource, /private_preview_v1/);
  assert.match(runtimeSource, /\/_preview-data\/v3/);
  assert.match(runtimeSource, /\/_local-data\/v3/);
  assert.match(source, /scientificAssetUrl/);
  assert.match(source, /official-record redistribution terms unresolved/);
  assert.match(source, /Metro provider terms unresolved/);
  assert.match(source, /private review evidence excluded/);
  assert.match(source, /const emptyPayload:[\s\S]*four: \{ records: \[\] \}[\s\S]*tarzana: \{ records: \[\] \}/);
  assert.match(source, /async function loadPayloads[\s\S]*if \(!localProfile\) return emptyPayload/);
  assert.match(source, /source\/output rights are not externally admitted/);
});

test('independent source allowlist rejects benign extras and credential content', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-source-allowlist-'));
  try {
    const allowlist = JSON.parse(await fs.readFile(path.join(appRoot, 'src/data/private-preview-source-allowlist.json'), 'utf8'));
    for (const relative of allowlist.paths) {
      const target = path.join(temp, relative);
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.copyFile(path.join(appRoot, relative), target);
    }
    await fs.mkdir(path.join(temp, 'public/_preview-data'), { recursive: true });
    await fs.copyFile(path.join(previewRoot, 'preview-payload-manifest.json'), path.join(temp, 'public/_preview-data/preview-payload-manifest.json'));
    await fs.mkdir(path.join(temp, 'release'), { recursive: true });
    const cleanPath = path.join(temp, 'release/CLEAN_REPO_MANIFEST.json');
    await fs.copyFile(path.join(appRoot, 'release/CLEAN_REPO_MANIFEST.json'), cleanPath);
    const payloadPath = path.join(temp, 'src/data/private-preview-payload-contract.json');
    await verifyPrivatePreviewSourceTree(temp, cleanPath, payloadPath);

    await fs.writeFile(path.join(temp, 'src/data/foo.json'), '{}');
    await assert.rejects(() => verifyPrivatePreviewSourceTree(temp, cleanPath, payloadPath), /actual recursive source paths differ/);
    await fs.rm(path.join(temp, 'src/data/foo.json'));

    const credentialName = ['VERCEL', 'TOKEN'].join('_');
    const credentialValue = ['abcdefghijkl', 'mnopqrstuvwx'].join('');
    const injected = Buffer.from(`${credentialName}=${credentialValue}\n`);
    await fs.writeFile(path.join(temp, 'README.md'), injected);
    const clean = JSON.parse(await fs.readFile(cleanPath, 'utf8'));
    const readme = clean.files.find((row) => row.path === 'README.md');
    readme.bytes = injected.length;
    readme.sha256 = createHash('sha256').update(injected).digest('hex');
    await fs.writeFile(cleanPath, JSON.stringify(clean));
    await assert.rejects(() => verifyPrivatePreviewSourceTree(temp, cleanPath, payloadPath), /credential-like content/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('public preview allowlist rejects unexpected data paths and credential-bearing allowed files', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-public-allowlist-'));
  try {
    const baseline = path.join(temp, 'public');
    await fs.mkdir(path.join(baseline, '_preview-data'), { recursive: true });
    await fs.copyFile(path.join(appRoot, 'public/robots.txt'), path.join(baseline, 'robots.txt'));
    await fs.copyFile(path.join(previewRoot, 'preview-payload-manifest.json'), path.join(baseline, '_preview-data/preview-payload-manifest.json'));
    await verifyPreviewPublicTree(baseline);
    for (const relative of ['foo.json', 'rail.json', 'source341-mask.json']) {
      await fs.writeFile(path.join(baseline, relative), '{}');
      await assert.rejects(() => verifyPreviewPublicTree(baseline), /public file set differs|forbidden private-preview path/);
      await fs.rm(path.join(baseline, relative));
    }
    const credentialName = ['VERCEL', 'TOKEN'].join('_');
    const credentialValue = ['abcdefghijkl', 'mnopqrstuvwx'].join('');
    await fs.writeFile(path.join(baseline, 'robots.txt'), `${credentialName}=${credentialValue}`);
    await assert.rejects(() => verifyPreviewPublicTree(baseline), /credential-like content/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('export allowlist rejects unexpected served files outside the payload root', () => {
  for (const relative of ['foo.json', 'rail.json', 'source341-mask.json', 'airport-contours.json', 'malicious.js']) {
    assert.throws(() => assertAllowedPrivatePreviewExportPath(relative), /unexpected private-preview exported file|forbidden private-preview path/);
  }
  assert.doesNotThrow(() => assertAllowedPrivatePreviewExportPath('index.html'));
  assert.doesNotThrow(() => assertAllowedPrivatePreviewExportPath('_preview-data/preview-payload-manifest.json', new Set(['_preview-data/preview-payload-manifest.json'])));
});

test('post-build export verifier rejects injected files and credential content', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-export-allowlist-'));
  const copy = path.join(temp, 'out');
  try {
    const buildId = 'qlp-0123456789abcdef01234567';
    await fs.mkdir(path.join(copy, `_next/static/${buildId}`), { recursive: true });
    await fs.mkdir(path.join(copy, '_preview-data'), { recursive: true });
    await fs.writeFile(path.join(copy, 'index.html'), '<meta name="robots" content="noindex,nofollow,noarchive"><p>Protected preview workspace</p><p>Protected preview shell only. No scientific or context payload is admitted.</p>');
    await fs.writeFile(path.join(copy, 'robots.txt'), 'User-agent: *\nDisallow: /\n');
    await fs.writeFile(path.join(copy, `_next/static/${buildId}/_buildManifest.js`), 'self.__BUILD_MANIFEST={}');
    await fs.writeFile(path.join(copy, `_next/static/${buildId}/_ssgManifest.js`), 'self.__SSG_MANIFEST=new Set');
    await fs.copyFile(path.join(previewRoot, 'preview-payload-manifest.json'), path.join(copy, '_preview-data/preview-payload-manifest.json'));
    await verifyPrivatePreviewExport(copy);
    for (const relative of ['foo.json', 'source341-mask.json']) {
      await fs.writeFile(path.join(copy, relative), '{}');
      await assert.rejects(() => verifyPrivatePreviewExport(copy), /unexpected private-preview exported file|forbidden private-preview path/);
      await fs.rm(path.join(copy, relative));
    }
    await fs.mkdir(path.join(copy, '_local-data'), { recursive: true });
    await fs.writeFile(path.join(copy, '_local-data/staged-manifest.json'), '{}');
    await assert.rejects(() => verifyPrivatePreviewExport(copy), /local-only data leaked/);
    await fs.rm(path.join(copy, '_local-data'), { recursive: true });
    const symlinkPath = path.join(copy, 'linked.js');
    await fs.symlink(path.join(copy, `_next/static/${buildId}/_buildManifest.js`), symlinkPath);
    await assert.rejects(() => verifyPrivatePreviewExport(copy), /symlink exported entry/);
    await fs.rm(symlinkPath);
    const credentialPath = path.join(copy, '_next/static/chunks/injected.js');
    await fs.mkdir(path.dirname(credentialPath), { recursive: true });
    const credentialName = ['VERCEL', 'TOKEN'].join('_');
    const credentialValue = ['abcdefghijkl', 'mnopqrstuvwx'].join('');
    await fs.writeFile(credentialPath, `const ${credentialName}="${credentialValue}";`);
    await assert.rejects(() => verifyPrivatePreviewExport(copy), /credential-like content/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});
