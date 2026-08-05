import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const output = path.join(appRoot, 'release/CLEAN_REPO_MANIFEST.json');
const includedRoots = ['src', 'scripts', 'tests'];
const includedFiles = ['package.json', 'package-lock.json', 'next.config.mjs', 'tsconfig.json', 'next-env.d.ts', 'eslint.config.mjs', 'DESIGN_PARITY.md', 'README.md', '.gitignore', 'public/robots.txt'];
const banned = /(_local-data|\.geojson|\.laz|\.las|\.tif|\.asc|\.mv\.db|\.trace\.db|\.env|secret|token)/i;

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
  for (const file of files.sort()) {
    const relative = path.relative(appRoot, file).split(path.sep).join('/');
    if (banned.test(relative)) throw new Error(`unallowlisted or local-only path: ${relative}`);
    const bytes = await fs.readFile(file);
    rows.push({ path: relative, bytes: bytes.length, sha256: hash(bytes) });
  }
  const manifest = { schema: 'quiet_la_web_clean_repo_manifest_v1', status: 'code_tests_schema_only', files: rows };
  await fs.mkdir(path.dirname(output), { recursive: true });
  await fs.writeFile(output, JSON.stringify(manifest, null, 2) + '\n');
  console.log(JSON.stringify({ status: 'CLEAN_MANIFEST_WRITTEN', path: output, files: rows.length, bytes: rows.reduce((sum, row) => sum + row.bytes, 0), sha256: hash(Buffer.from(JSON.stringify(manifest, null, 2) + '\n')) }, null, 2));
}
main().catch((error) => { console.error(`build-clean-manifest: ${error.message}`); process.exitCode = 1; });
