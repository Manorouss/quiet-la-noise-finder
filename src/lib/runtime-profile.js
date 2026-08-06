/** Fail-closed runtime profiles for the Quiet LA static client. */

export const RUNTIME_PROFILES = Object.freeze({
  local_v3: Object.freeze({
    id: 'local_v3',
    mode: 'local',
    dataRoot: '/_local-data/v3',
    scientificPayloadsAvailable: true,
    developerRecoveryAllowed: true,
    badge: 'Private model workspace',
    status: 'Interactive modeled field · OpenStreetMap basemap',
  }),
  private_preview_v1: Object.freeze({
    id: 'private_preview_v1',
    mode: 'private_preview',
    dataRoot: '/_preview-data/v3',
    scientificPayloadsAvailable: false,
    developerRecoveryAllowed: false,
    badge: 'Protected preview workspace',
    status: 'Scientific private preview is not yet admitted',
  }),
  external_payload_free_v1: Object.freeze({
    id: 'external_payload_free_v1',
    mode: 'external_payload_free',
    dataRoot: null,
    scientificPayloadsAvailable: false,
    developerRecoveryAllowed: false,
    badge: 'Payload-free public shell',
    status: 'Scientific private preview is not published on this URL',
  }),
});

export function resolveRuntimeProfile(value) {
  if (typeof value === 'string' && Object.hasOwn(RUNTIME_PROFILES, value)) return RUNTIME_PROFILES[value];
  return RUNTIME_PROFILES.external_payload_free_v1;
}

export function scientificAssetUrl(profile, relativePath) {
  if (!profile?.dataRoot || profile.mode === 'external_payload_free') throw new Error('scientific payload requests are disabled for this runtime profile');
  if (typeof relativePath !== 'string' || !relativePath || relativePath.startsWith('/') || relativePath.includes('..') || /^https?:/i.test(relativePath)) {
    throw new Error('scientific payload path is not a safe same-origin relative path');
  }
  const url = `${profile.dataRoot}/${relativePath}`;
  const requiredPrefix = profile.mode === 'local' ? '/_local-data/' : '/_preview-data/';
  if (!url.startsWith(requiredPrefix)) throw new Error('scientific payload root does not match the runtime profile');
  return url;
}

export function initialLayerToggles(profile, layerIds) {
  const enabled = profile?.mode === 'local' && profile.scientificPayloadsAvailable === true;
  return Object.fromEntries(layerIds.map((id) => [id, enabled]));
}
