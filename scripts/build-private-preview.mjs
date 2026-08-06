import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { verifyPrivatePreviewSourceTree } from './verify-private-preview-source-tree.mjs';
import { verifyPrivatePreviewExport } from './verify-private-preview-export.mjs';
import { buildIsolatedProfile } from './profile-export-io.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
async function walk(current) {
  const files = [];
  for (const entry of await fs.readdir(current, { withFileTypes: true })) {
    const target = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`symlink in private-preview build inputs: ${target}`);
    if (entry.isDirectory()) files.push(...await walk(target));
    else files.push(target);
  }
  return files;
}

async function deterministicBuildId() {
  const inputs = [
    ...await walk(path.join(appRoot, 'src/app')),
    ...await walk(path.join(appRoot, 'src/components')),
    ...await walk(path.join(appRoot, 'src/lib')),
    path.join(appRoot, 'src/data/layer-contract.json'),
    path.join(appRoot, 'src/data/deployment-contract.json'),
    path.join(appRoot, 'src/data/private-preview-payload-contract.json'),
    path.join(appRoot, 'package.json'),
    path.join(appRoot, 'package-lock.json'),
    path.join(appRoot, 'next.config.mjs'),
    path.join(appRoot, 'public/robots.txt'),
  ];
  const digest = createHash('sha256');
  for (const file of [...new Set(inputs)].sort()) {
    const relative = path.relative(appRoot, file).split(path.sep).join('/');
    digest.update(`${relative}\0`);
    digest.update(await fs.readFile(file));
    digest.update('\0');
  }
  return `qlp-${digest.digest('hex').slice(0, 24)}`;
}

async function main() {
  const buildId = await deterministicBuildId();
  const result = await buildIsolatedProfile({
    token: 'private',
    excludeFromExport: ['_local-data'],
    env: {
      NEXT_PUBLIC_QUIET_LA_DATA_PROFILE: 'private_preview_v1',
      NEXT_PUBLIC_QUIET_LA_DATA_ROOT: '/_preview-data/v3',
      NEXT_PUBLIC_QUIET_LA_LOCAL_RECOVERY_COMMAND: '',
      QUIET_LA_PREVIEW_BUILD_ID: buildId,
    },
    preBuild: async () => {
      const sourceResult = await verifyPrivatePreviewSourceTree();
      console.log(JSON.stringify({ status: 'PRIVATE_PREVIEW_SOURCE_TREE_PASS', ...sourceResult }, null, 2));
    },
    verify: (root) => verifyPrivatePreviewExport(root),
  });
  const final = await verifyPrivatePreviewExport(path.join(appRoot, 'out'), { writeManifest: true });
  const summary = { ...result };
  delete summary.rows;
  console.log(JSON.stringify({ status: 'PRIVATE_PREVIEW_NEXT_BUILD_PASS', buildId, ...summary, manifestSha256: final.manifestSha256 }, null, 2));
}

main().catch((error) => { console.error(`build-private-preview: ${error.message}`); process.exitCode = 1; });
