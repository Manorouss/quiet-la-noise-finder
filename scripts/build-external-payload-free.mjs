import { buildIsolatedProfile } from './profile-export-io.mjs';
import { verifyExternalPayloadFreeExport } from './verify-external-payload-free-export.mjs';
import { writeCompleteExportManifest } from './export-profile-policy.mjs';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);

buildIsolatedProfile({
  token: 'external',
  excludeFromExport: ['_local-data', '_preview-data'],
  env: {
    NEXT_PUBLIC_QUIET_LA_DATA_PROFILE: 'external_payload_free_v1',
    NEXT_PUBLIC_QUIET_LA_DATA_ROOT: '',
    NEXT_PUBLIC_QUIET_LA_LOCAL_RECOVERY_COMMAND: '',
  },
  verify: verifyExternalPayloadFreeExport,
}).then(async (result) => {
  const manifest = await writeCompleteExportManifest(appRoot, 'EXTERNAL_PAYLOAD_FREE_EXPORT_MANIFEST.json', result);
  const summary = { ...result };
  delete summary.rows;
  console.log(JSON.stringify({ status: 'EXTERNAL_PAYLOAD_FREE_BUILD_PASS', ...summary, payloadDirectories: 0, manifest }, null, 2));
}).catch((error) => {
  console.error(`build-external-payload-free: ${error.message}`);
  process.exitCode = 1;
});
