import { claimTextIsSafe } from './claim-policy.js';

const HASH = /^[0-9a-f]{64}$/;
const PERIODS = new Set(['D', 'E', 'N']);
export const REPLACEMENT_ADMISSION_BLOCKERS = Object.freeze([
  'INDEPENDENT_ADMISSION_REQUIRED',
  'MASTER_EXECUTION_AUDIT_REQUIRED',
]);
const MANIFEST_KEYS = new Set([
  'schema', 'adapterContract', 'candidate', 'profile', 'status',
  'rightsAdmission', 'admission', 'rows',
]);

function object(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value;
}

function exactKeys(value, expected, label) {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (JSON.stringify(actual) !== JSON.stringify(wanted)) throw new Error(`${label} fields differ from the adapter contract`);
}

function hash(value, label) {
  if (typeof value !== 'string' || !HASH.test(value)) throw new Error(`${label} must be a lowercase SHA-256`);
}

function safeAssetPath(value, policy) {
  if (typeof value !== 'string' || value.includes('\\') || value.startsWith('/') || value.includes('\0')) throw new Error('replacement asset path must be relative POSIX text');
  const normalized = value.split('/').filter((part) => part !== '').join('/');
  if (normalized !== value || value.split('/').some((part) => part === '' || part === '.' || part === '..')) throw new Error('replacement asset path contains traversal or empty segments');
  if (!value.startsWith(`${policy.root}/`) || value.includes('_local-data') || value.includes('_preview-data')) throw new Error('replacement asset path is outside the admitted root');
}

function safeUrl(value, label) {
  let parsed;
  try { parsed = new URL(value); } catch { throw new Error(`${label} must be an absolute HTTPS URL`); }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw new Error(`${label} must be an HTTPS URL without credentials`);
}

function assertAdmissionFlags(admission, label) {
  const expected = [
    'privatePreviewAuthorized', 'externalPreviewAuthorized', 'publicReleaseAuthorized',
    'acousticCombinationAdmitted', 'source341CorrectionAdmitted', 'rawEvidenceIncluded',
    'runtimeDatabasesIncluded', 'credentialsIncluded',
  ];
  exactKeys(admission, expected, label);
  for (const key of expected) if (admission[key] !== false) throw new Error(`${label}.${key} must remain false`);
}

export function validateReplacementAdapter(contract, manifest) {
  object(contract, 'adapter contract');
  exactKeys(contract, [
    'schema', 'candidate', 'allowedProfiles', 'allowedStatuses', 'allowedEvidenceClasses',
    'requiredRowFields', 'assetPolicy', 'claimPolicy', 'admissionPolicy',
  ], 'adapter contract');
  if (contract.schema !== 'quiet_la_replacement_layer_adapter_contract_v1') throw new Error('unexpected replacement adapter schema');
  if (!Array.isArray(contract.allowedProfiles) || JSON.stringify(contract.allowedProfiles) !== JSON.stringify(['local_v3', 'private_preview_v1'])) throw new Error('replacement profiles widened');
  if (!Array.isArray(contract.allowedStatuses) || !contract.allowedStatuses.includes('NOT_ADMITTED_PENDING_RIGHTS_CLEAR_REPLACEMENT') || !contract.allowedStatuses.includes('ADMITTED_RIGHTS_CLEAR_REPLACEMENT')) throw new Error('replacement statuses are incomplete');
  if (!Array.isArray(contract.allowedEvidenceClasses) || contract.allowedEvidenceClasses.length === 0) throw new Error('replacement evidence classes are empty');
  if (!Array.isArray(contract.requiredRowFields) || new Set(contract.requiredRowFields).size !== contract.requiredRowFields.length) throw new Error('replacement row schema is invalid');
  object(contract.assetPolicy, 'asset policy');
  exactKeys(contract.assetPolicy, ['root', 'controlFiles', 'relativePosixOnly', 'rejectSymlinks', 'maximumFileBytes', 'scientificDataSameOriginOnly'], 'asset policy');
  if (contract.assetPolicy.root !== 'v3/admitted' || !Array.isArray(contract.assetPolicy.controlFiles) || JSON.stringify(contract.assetPolicy.controlFiles) !== JSON.stringify(['preview-payload-manifest.json']) || contract.assetPolicy.relativePosixOnly !== true || contract.assetPolicy.rejectSymlinks !== true || contract.assetPolicy.scientificDataSameOriginOnly !== true || !Number.isSafeInteger(contract.assetPolicy.maximumFileBytes) || contract.assetPolicy.maximumFileBytes <= 0) throw new Error('asset policy widened or malformed');
  object(contract.claimPolicy, 'claim policy');
  exactKeys(contract.claimPolicy, ['forbiddenPositiveFragments', 'sentenceLocalNegationOnly'], 'claim policy');
  if (contract.claimPolicy.sentenceLocalNegationOnly !== true || !Array.isArray(contract.claimPolicy.forbiddenPositiveFragments) || contract.claimPolicy.forbiddenPositiveFragments.length === 0) throw new Error('replacement claim policy is incomplete');
  object(contract.admissionPolicy, 'adapter admission policy');
  assertAdmissionFlags(contract.admissionPolicy, 'adapter admission policy');

  object(manifest, 'replacement manifest');
  exactKeys(manifest, MANIFEST_KEYS, 'replacement manifest');
  if (manifest.schema !== 'quiet_la_replacement_layer_manifest_v1') throw new Error('unexpected replacement manifest schema');
  if (manifest.adapterContract !== 'src/data/replacement-layer-adapter-contract.json') throw new Error('replacement adapter contract binding drift');
  if (typeof manifest.candidate !== 'string' || !manifest.candidate) throw new Error('replacement candidate is missing');
  if (!contract.allowedProfiles.includes(manifest.profile)) throw new Error('replacement profile is not allowed');
  if (!contract.allowedStatuses.includes(manifest.status)) throw new Error('replacement status is not allowed');
  object(manifest.rightsAdmission, 'replacement rights admission');
  exactKeys(manifest.rightsAdmission, ['sourceTermsRequired', 'derivativeRightsRequired', 'attributionRequired', 'externalPreviewAuthorized'], 'replacement rights admission');
  for (const key of ['sourceTermsRequired', 'derivativeRightsRequired', 'attributionRequired']) if (manifest.rightsAdmission[key] !== true) throw new Error(`replacement rights requirement ${key} is missing`);
  if (manifest.rightsAdmission.externalPreviewAuthorized !== false) throw new Error('replacement external preview authorization must remain false');
  object(manifest.admission, 'replacement admission');
  exactKeys(manifest.admission, ['privatePreviewAuthorized', 'externalPreviewAuthorized', 'publicReleaseAuthorized', 'acousticCombinationAdmitted', 'source341CorrectionAdmitted', 'rawEvidenceIncluded', 'runtimeDatabasesIncluded', 'credentialsIncluded'], 'replacement admission');
  for (const key of ['externalPreviewAuthorized', 'publicReleaseAuthorized', 'acousticCombinationAdmitted', 'source341CorrectionAdmitted', 'rawEvidenceIncluded', 'runtimeDatabasesIncluded', 'credentialsIncluded']) if (manifest.admission[key] !== false) throw new Error(`replacement admission.${key} must remain false`);
  if (manifest.admission.privatePreviewAuthorized !== false && manifest.admission.privatePreviewAuthorized !== true) throw new Error('replacement admission.privatePreviewAuthorized must be boolean');
  if (!Array.isArray(manifest.rows)) throw new Error('replacement rows must be an array');
  const ids = new Set();
  const paths = new Set();
  let bytes = 0;
  for (const row of manifest.rows) {
    object(row, 'replacement row');
    exactKeys(row, contract.requiredRowFields, 'replacement row');
    if (typeof row.id !== 'string' || !row.id || ids.has(row.id)) throw new Error('replacement row IDs must be unique and non-empty');
    if (typeof row.sourceFamily !== 'string' || !row.sourceFamily || typeof row.region !== 'string' || !row.region || typeof row.metricKind !== 'string' || !row.metricKind) throw new Error('replacement row identity fields are incomplete');
    ids.add(row.id);
    if (!Array.isArray(row.periods) || row.periods.some((period) => !PERIODS.has(period)) || new Set(row.periods).size !== row.periods.length) throw new Error('replacement periods are invalid');
    if (!contract.allowedEvidenceClasses.includes(row.evidenceClass)) throw new Error('unknown or non-displayable replacement evidence class');
    if (row.acousticCombinationEligible !== false) throw new Error('replacement acoustic combination widened');
    safeAssetPath(row.assetPath, contract.assetPolicy);
    if (paths.has(row.assetPath)) throw new Error('replacement asset paths must be unique');
    paths.add(row.assetPath);
    if (!Number.isSafeInteger(row.bytes) || row.bytes <= 0 || row.bytes > contract.assetPolicy.maximumFileBytes) throw new Error('replacement row size is outside the policy');
    hash(row.sha256, 'replacement asset hash');
    if (!Number.isSafeInteger(row.sourceBytes) || row.sourceBytes <= 0) throw new Error('replacement source size is invalid');
    hash(row.sourceHash, 'replacement source hash');
    hash(row.termsSha256, 'replacement terms hash');
    safeUrl(row.termsUrl, 'replacement terms URL');
    if (row.rightsStatus !== 'ADMITTED_FOR_PRIVATE_PREVIEW') throw new Error('replacement row rights are not admitted');
    if (typeof row.attribution !== 'string' || !row.attribution.trim() || !claimTextIsSafe(row.attribution, contract.claimPolicy.forbiddenPositiveFragments)) throw new Error('replacement attribution is missing or unsafe');
    if (typeof row.claimBoundary !== 'string' || !row.claimBoundary.trim() || !claimTextIsSafe(row.claimBoundary, contract.claimPolicy.forbiddenPositiveFragments)) throw new Error('replacement claim boundary is missing or unsafe');
    bytes += row.bytes;
  }
  if (manifest.rows.length > 0) {
    if (manifest.status !== 'ADMITTED_RIGHTS_CLEAR_REPLACEMENT' || manifest.admission.privatePreviewAuthorized !== true || manifest.rightsAdmission.externalPreviewAuthorized !== false) throw new Error('replacement rows require a separately admitted private preview');
  } else if (manifest.status !== 'NOT_ADMITTED_PENDING_RIGHTS_CLEAR_REPLACEMENT' || manifest.admission.privatePreviewAuthorized !== false) {
    throw new Error('empty replacement manifest must remain not admitted');
  }
  return {
    rows: manifest.rows.length,
    bytes,
    status: manifest.status,
    declaredPrivatePreviewAuthorized: manifest.admission.privatePreviewAuthorized,
    structuralValid: true,
    admissible: false,
    blockers: [...REPLACEMENT_ADMISSION_BLOCKERS],
  };
}
