import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { promisify } from 'node:util';
import test from 'node:test';
import { REPLACEMENT_ADMISSION_BLOCKERS, validateReplacementAdapter } from '../src/lib/replacement-layer-adapter.js';
import { verifyReplacementAssetRoot } from '../scripts/replacement-layer-adapter-io.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const execFileAsync = promisify(execFile);
const contract = JSON.parse(await fs.readFile(path.join(appRoot, 'src/data/replacement-layer-adapter-contract.json'), 'utf8'));
const emptyManifest = JSON.parse(await fs.readFile(path.join(appRoot, 'src/data/replacement-layer-admission-manifest.json'), 'utf8'));

function clone(value) { return structuredClone(value); }
function validRow(bytes = Buffer.from('abc')) {
  return {
    id: 'replacement-1',
    sourceFamily: 'rights_clear_freeway',
    region: 'Tarzana',
    periods: ['D', 'E', 'N'],
    metricKind: 'relative_model_index',
    evidenceClass: 'modeled_relative_uncalibrated',
    calibration: 'uncalibrated',
    currentness: 'not_current_or_observed',
    acousticCombinationEligible: false,
    assetPath: 'v3/admitted/replacement.json',
    bytes: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'),
    sourceHash: 'a'.repeat(64),
    sourceBytes: bytes.length,
    attribution: 'Rights-clear replacement display derivative; source terms and attribution remain bound.',
    termsUrl: 'https://example.com/terms',
    termsSha256: 'b'.repeat(64),
    rightsStatus: 'ADMITTED_FOR_PRIVATE_PREVIEW',
    claimBoundary: 'Modeled relative and uncalibrated; not measured dBA/CNEL; not quietness; not an address prediction.',
  };
}
function admittedManifest(row = validRow()) {
  const manifest = clone(emptyManifest);
  manifest.status = 'ADMITTED_RIGHTS_CLEAR_REPLACEMENT';
  manifest.admission.privatePreviewAuthorized = true;
  manifest.rows = [row];
  return manifest;
}

test('current replacement adapter is structurally valid but permanently admission-blocked', () => {
  assert.deepEqual(validateReplacementAdapter(contract, emptyManifest), {
    rows: 0,
    bytes: 0,
    status: 'NOT_ADMITTED_PENDING_RIGHTS_CLEAR_REPLACEMENT',
    declaredPrivatePreviewAuthorized: false,
    structuralValid: true,
    admissible: false,
    blockers: [...REPLACEMENT_ADMISSION_BLOCKERS],
  });
});

test('admitted-looking same-manifest evidence cannot authorize a future preview', () => {
  const result = validateReplacementAdapter(contract, admittedManifest());
  assert.equal(result.structuralValid, true);
  assert.equal(result.admissible, false);
  assert.deepEqual(result.blockers, [...REPLACEMENT_ADMISSION_BLOCKERS]);
  assert.equal(result.declaredPrivatePreviewAuthorized, true);
});

test('adapter rejects unknown taxonomy, claims, path traversal, and combination widening', () => {
  const cases = [
    (manifest) => { manifest.rows[0].evidenceClass = 'made_up_class'; },
    (manifest) => { manifest.rows[0].claimBoundary = 'The quietest current traffic map is here.'; },
    (manifest) => { manifest.rows[0].assetPath = '../local-data/payload.json'; },
    (manifest) => { manifest.rows[0].acousticCombinationEligible = true; },
  ];
  for (const mutate of cases) {
    const manifest = admittedManifest();
    mutate(manifest);
    assert.throws(() => validateReplacementAdapter(contract, manifest));
  }
});

test('adapter rejects widened root admission flags and malformed schema fields', () => {
  const manifest = admittedManifest();
  manifest.admission.acousticCombinationAdmitted = true;
  assert.throws(() => validateReplacementAdapter(contract, manifest), /acousticCombinationAdmitted/);
  const malformed = clone(emptyManifest);
  malformed.extra = true;
  assert.throws(() => validateReplacementAdapter(contract, malformed), /fields differ/);
});

test('asset verifier binds bytes, exact paths, partial files, and symlinks', async () => {
  const manifest = admittedManifest();
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-replacement-adapter-'));
  const root = path.join(temp, 'assets');
  try {
    await fs.mkdir(path.join(root, 'v3/admitted'), { recursive: true });
    await fs.writeFile(path.join(root, 'v3/admitted/replacement.json'), 'abc');
    assert.equal((await verifyReplacementAssetRoot(root, contract, manifest)).bytes, 3);
    await assert.rejects(() => verifyReplacementAssetRoot(path.join(root, 'v3/admitted'), contract, manifest), /file set/);
    await fs.writeFile(path.join(root, 'v3/admitted/replacement.json'), 'tampered');
    await assert.rejects(() => verifyReplacementAssetRoot(root, contract, manifest), /hash\/size drift/);
    await fs.writeFile(path.join(root, 'v3/admitted/replacement.json'), 'abc');
    await fs.writeFile(path.join(root, 'v3/admitted/unlisted.json'), '{}');
    await assert.rejects(() => verifyReplacementAssetRoot(root, contract, manifest), /file set/);
    await fs.rm(path.join(root, 'v3/admitted/unlisted.json'));
    await fs.rm(path.join(root, 'v3/admitted/replacement.json'));
    await fs.symlink(path.join(appRoot, 'src/data/layer-contract.json'), path.join(root, 'v3/admitted/replacement.json'));
    await assert.rejects(() => verifyReplacementAssetRoot(root, contract, manifest), /symlink/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('asset verifier rejects missing roots/files and parent symlink redirection', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-replacement-root-'));
  try {
    const emptyMissing = await verifyReplacementAssetRoot(path.join(temp, 'empty-missing'), contract, emptyManifest);
    assert.equal(emptyMissing.admissible, false);
    await assert.rejects(() => verifyReplacementAssetRoot(path.join(temp, 'nonempty-missing'), contract, admittedManifest()), /root is missing/);

    const root = path.join(temp, 'real', 'assets');
    await fs.mkdir(path.join(root, 'v3/admitted'), { recursive: true });
    await assert.rejects(() => verifyReplacementAssetRoot(root, contract, admittedManifest()), /file set/);

    const aliasParent = path.join(temp, 'alias-parent');
    await fs.symlink(path.join(temp, 'real'), aliasParent, 'dir');
    await assert.rejects(() => verifyReplacementAssetRoot(path.join(aliasParent, 'assets'), contract, admittedManifest()), /path component is a symlink/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('current empty manifest cannot be turned into a payload by adding files', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-replacement-empty-'));
  try {
    const root = path.join(temp, 'assets');
    await fs.mkdir(root);
    await fs.writeFile(path.join(root, 'unexpected.json'), '{}');
    await assert.rejects(() => verifyReplacementAssetRoot(root, contract, emptyManifest), /file set/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('empty adapter root allows only the private-preview control manifest, never payload files', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-replacement-control-'));
  try {
    const root = path.join(temp, 'assets');
    await fs.mkdir(root);
    await fs.copyFile(path.join(appRoot, 'public/_preview-data/preview-payload-manifest.json'), path.join(root, 'preview-payload-manifest.json'));
    await assert.doesNotReject(() => verifyReplacementAssetRoot(root, contract, emptyManifest));
    await fs.writeFile(path.join(root, 'unexpected.json'), '{}');
    await assert.rejects(() => verifyReplacementAssetRoot(root, contract, emptyManifest), /file set/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});

test('replacement adapter CLI reports structural validity but blocked admission', async () => {
  const { stdout } = await execFileAsync(process.execPath, ['scripts/verify-replacement-layer-adapter.mjs'], { cwd: appRoot });
  const result = JSON.parse(stdout);
  assert.equal(result.status, 'STRUCTURAL_VALID_ADMISSION_BLOCKED');
  assert.equal(result.structuralValid, true);
  assert.equal(result.admissible, false);
  assert.deepEqual(result.blockers, [...REPLACEMENT_ADMISSION_BLOCKERS]);
});
