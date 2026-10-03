import { promises as fs } from 'node:fs';
import path from 'node:path';
import { buildIsolatedProfile } from './profile-export-io.mjs';
import { verifyPilotProfileExport } from './verify-pilot-profile-export.mjs';
import { writeCompleteExportManifest } from './export-profile-policy.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const projectRoot = path.resolve(appRoot, '../../..');
const args = process.argv.slice(2);
const hosted = args.includes('--hosted');
if (args.some((arg) => arg !== '--hosted')) throw new Error(`unsupported build-pilot-profile arguments: ${args.join(' ')}`);
const targetOutRoot = hosted
  ? path.join(appRoot, 'out')
  : path.join(projectRoot, 'implementation/work/delivery_2026_10_02/portal/pilot-export');

buildIsolatedProfile({
  token: 'pilot',
  targetOutRoot,
  excludeFromExport: ['_preview-data'],
  env: {
    NEXT_PUBLIC_QUIET_LA_DATA_PROFILE: 'pilot_v1',
    NEXT_PUBLIC_QUIET_LA_DATA_ROOT: '/_local-data/v3',
    NEXT_PUBLIC_QUIET_LA_LOCAL_RECOVERY_COMMAND: '',
  },
  postBuild: async (stageRoot) => {
    const stageLocalRoot = path.join(stageRoot, '_local-data');
    await fs.rm(stageLocalRoot, { recursive: true, force: true });
    await fs.mkdir(path.join(stageLocalRoot, 'v3'), { recursive: true });
    await fs.cp(path.join(appRoot, 'public/_local-data/v3/pilot'), path.join(stageLocalRoot, 'v3/pilot'), { recursive: true, errorOnExist: true, force: false });
  },
  verify: (root) => verifyPilotProfileExport(root),
}).then(async (result) => {
  const manifest = await writeCompleteExportManifest(appRoot, hosted ? 'HOSTED_PILOT_EXPORT_MANIFEST.json' : 'PILOT_PROFILE_EXPORT_MANIFEST.json', result);
  const summary = { ...result }; delete summary.rows;
  console.log(JSON.stringify({ status: hosted ? 'HOSTED_PILOT_BUILD_PASS' : 'PILOT_PROFILE_BUILD_PASS', ...summary, output: targetOutRoot, manifest }, null, 2));
}).catch((error) => { console.error(`build-pilot-profile: ${error.message}`); process.exitCode = 1; });
