import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const output = path.join(appRoot, 'release/CLEAN_REPO_MANIFEST.json');
const includedRoots = ['src', 'scripts', 'tests'];
const includedFiles = ['package.json', 'package-lock.json', 'next.config.mjs', 'tsconfig.json', 'next-env.d.ts', 'eslint.config.mjs', 'DESIGN_PARITY.md', 'PRIVATE_PREVIEW_DEPLOYMENT.md', 'README.md', '.gitignore', '.vercelignore', 'vercel.json', 'public/robots.txt'];
const banned = /(_local-data|\.geojson|\.laz|\.las|\.tif|\.asc|\.mv\.db|\.trace\.db|\.env|secret|token)/i;
const publicPilotAssets = {
  'src/data/pilot-release-v1/benchmark.geojson': '16df6aaf99abaf0a791e5de95a49a5eda5760851994fd4636465086cffd59c93',
  'src/data/pilot-release-v1/buildings.geojson': 'b1f25db888ebb749751fc9b4beb2a2ab93bb6be28e23e273243e03d1ef937bd5',
  'src/data/pilot-release-v1/build-manifest.json': 'd733b7e11af379b2a31a1cda7559481ef5d7a175464c5d3b92c5c273c2c15769',
};

async function walk(dir) {
  const files = [];
  for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`symlink rejected: ${p}`);
    if (entry.isDirectory()) files.push(...await walk(p));
    else files.push(p);
  }
  return files;
}
function hash(bytes) { return createHash('sha256').update(bytes).digest('hex'); }

async function main() {
  const files = [];
  for (const root of includedRoots) files.push(...await walk(path.join(appRoot, root)));
  for (const file of includedFiles) files.push(path.join(appRoot, file));
  const rows = [];
  const pilotAssetRows = [];
  for (const file of files.sort()) {
    const relative = path.relative(appRoot, file).split(path.sep).join('/');
    if (relative.startsWith('src/data/pilot-release-v1/')) {
      const expectedHash = publicPilotAssets[relative];
      if (!expectedHash) throw new Error(`unallowlisted public pilot release asset: ${relative}`);
      const bytes = await fs.readFile(file);
      const sha256 = hash(bytes);
      if (sha256 !== expectedHash) throw new Error(`public pilot release asset hash drift: ${relative}`);
      pilotAssetRows.push({ path: relative, bytes: bytes.length, sha256 });
      continue;
    }
    if (banned.test(relative)) throw new Error(`unallowlisted or local-only path: ${relative}`);
    const bytes = await fs.readFile(file);
    rows.push({ path: relative, bytes: bytes.length, sha256: hash(bytes) });
  }
  if (pilotAssetRows.length !== Object.keys(publicPilotAssets).length) throw new Error(`public pilot asset set differs from exact allowlist: ${pilotAssetRows.map((row) => row.path).join(', ')}`);
  const manifest = { schema: 'quiet_la_web_clean_repo_manifest_v1', status: 'code_tests_schema_only', files: rows, public_pilot_assets: pilotAssetRows };
  await fs.mkdir(path.dirname(output), { recursive: true });
  await fs.writeFile(output, JSON.stringify(manifest, null, 2) + '\n');
  console.log(JSON.stringify({ status: 'CLEAN_MANIFEST_WRITTEN', path: output, files: rows.length, bytes: rows.reduce((sum, row) => sum + row.bytes, 0), sha256: hash(Buffer.from(JSON.stringify(manifest, null, 2) + '\n')) }, null, 2));
}
main().catch((error) => { console.error(`build-clean-manifest: ${error.message}`); process.exitCode = 1; });
