import { buildIsolatedProfile } from './profile-export-io.mjs';
import { verifyLocalProfileExport } from './verify-local-profile-export.mjs';
import { writeCompleteExportManifest } from './export-profile-policy.mjs';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);

buildIsolatedProfile({
  token: 'local',
  excludeFromExport: ['_preview-data'],
  env: {
    NEXT_PUBLIC_QUIET_LA_DATA_PROFILE: 'local_v3',
    NEXT_PUBLIC_QUIET_LA_DATA_ROOT: '/_local-data/v3',
    NEXT_PUBLIC_QUIET_LA_LOCAL_RECOVERY_COMMAND: 'npm run stage:local',
  },
  verify: verifyLocalProfileExport,
}).then(async (result) => {
  const manifest = await writeCompleteExportManifest(appRoot, 'LOCAL_PROFILE_EXPORT_MANIFEST.json', result);
  const summary = { ...result };
  delete summary.rows;
  console.log(JSON.stringify({ status: 'LOCAL_PROFILE_BUILD_PASS', ...summary, manifest }, null, 2));
}).catch((error) => {
  console.error(`build-local-profile: ${error.message}`);
  process.exitCode = 1;
});
