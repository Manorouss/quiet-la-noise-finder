export const forbiddenPreviewPathFragments = [
  'airport-contour',
  'airport_contour',
  'official-context',
  'official_context',
  'context/rail',
  'metro-rail',
  'metro_rail',
  'source341',
  'source-341',
  'source_341',
];

const credentialPatterns = [
  /-----BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY-----/,
  /\b(?:VERCEL_TOKEN|AWS_SECRET_ACCESS_KEY|CLOUDFLARE_API_TOKEN)\s*[:=]\s*["']?(?![<$])[A-Za-z0-9_./+\-=]{20,}/,
  /\bgh[pousr]_[A-Za-z0-9_]{20,}/,
];

export function hasCredentialLikeContent(bytes) {
  const text = bytes.toString('utf8');
  return credentialPatterns.some((pattern) => pattern.test(text));
}

export function assertSafePreviewPath(relative) {
  const lowered = relative.toLowerCase();
  if (forbiddenPreviewPathFragments.some((fragment) => lowered.includes(fragment))) throw new Error(`forbidden private-preview path fragment: ${relative}`);
}
