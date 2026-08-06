import { promises as fs } from 'node:fs';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const scanRoot = process.env.QUIET_LA_EXTERNAL_ROOT ? path.resolve(process.env.QUIET_LA_EXTERNAL_ROOT) : appRoot;
const forbiddenRoots = [path.join(scanRoot, 'public/_local-data'), path.join(scanRoot, '_local-data'), path.join(scanRoot, 'public/_preview-data'), path.join(scanRoot, '_preview-data')];
const forbiddenNames = ['.geojson', '.geojson.gz', '.laz', '.las', '.tif', '.asc', '.mv.db', '.trace.db', '.zip'];
const secretPatterns = [
  /BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY/,
  new RegExp(['VERCEL', 'TOKEN'].join('_')),
  new RegExp(['AWS', 'SECRET', 'ACCESS', 'KEY'].join('_')),
  new RegExp(['CLOUDFLARE', 'API', 'TOKEN'].join('_')),
  /ghp_[A-Za-z0-9_]+/,
];

async function exists(p) { return fs.lstat(p).then(() => true).catch(() => false); }

async function walk(dir) {
  const result = [];
  for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.name === 'node_modules' || entry.name === '.next' || entry.name === 'out' || entry.name === '.git') continue;
    if (entry.isSymbolicLink()) throw new Error(`symlink is not allowed in external tree: ${p}`);
    if (entry.isDirectory()) result.push(...await walk(p));
    else result.push(p);
  }
  return result;
}

async function main() {
  for (const root of forbiddenRoots) if (await exists(root)) throw new Error(`local-only data present: ${root}`);
  const files = await walk(scanRoot);
  for (const file of files) {
    const name = path.basename(file).toLowerCase();
    if (forbiddenNames.some((suffix) => name.endsWith(suffix))) throw new Error(`scientific payload is not external-eligible: ${file}`);
    if (name === '.env' || name.startsWith('.env.')) throw new Error(`environment file is not external-eligible: ${file}`);
    const bytes = await fs.readFile(file);
    if (secretPatterns.some((pattern) => pattern.test(bytes.toString('utf8')))) throw new Error(`credential-like content found: ${file}`);
  }
  console.log(JSON.stringify({ status: 'EXTERNAL_TREE_CLEAN', localDataAbsent: true, filesChecked: files.length }, null, 2));
}

main().catch((error) => { console.error(`verify-external-tree: ${error.message}`); process.exitCode = 1; });
