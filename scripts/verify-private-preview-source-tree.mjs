import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { assertSafePreviewPath, hasCredentialLikeContent } from './preview-security-policy.mjs';
import { verifyPrivatePreviewRoot } from './verify-private-preview-bundle.mjs';
import { verifyStagedRoot } from './verify-staged-local-data.mjs';

const defaultAppRoot = path.resolve(new URL('..', import.meta.url).pathname);
const cleanManifestPath = path.join(defaultAppRoot, 'release/CLEAN_REPO_MANIFEST.json');
const payloadContractPath = path.join(defaultAppRoot, 'src/data/private-preview-payload-contract.json');

function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }
async function lstatOrNull(target) { return fs.lstat(target).catch(() => null); }
async function walk(root, current = root) {
  const rows = [];
  for (const entry of await fs.readdir(current, { withFileTypes: true })) {
    const target = path.join(current, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`symlink in private-preview source tree: ${target}`);
    if (entry.isDirectory()) rows.push(...await walk(root, target));
    else rows.push(path.relative(root, target).split(path.sep).join('/'));
  }
  return rows;
}

export async function verifyPreviewPublicTree(publicRoot, selectedPayloadContractPath = payloadContractPath) {
  const contract = JSON.parse(await fs.readFile(selectedPayloadContractPath, 'utf8'));
  const allowed = ['robots.txt', '_preview-data/preview-payload-manifest.json', ...contract.rows.map((row) => `_preview-data/${row.target}`)].sort();
  const localRoot = path.join(publicRoot, '_local-data');
  if (await lstatOrNull(localRoot)) {
    await verifyStagedRoot(localRoot, path.join(path.dirname(publicRoot), 'src/data/local-source-manifest.json'));
  }
  const actual = (await walk(publicRoot)).filter((relative) => !relative.startsWith('_local-data/')).sort();
  if (JSON.stringify(actual) !== JSON.stringify(allowed)) throw new Error(`private-preview public file set differs from allowlist: ${actual.join(', ')}`);
  for (const relative of actual) {
    assertSafePreviewPath(relative);
    const bytes = await fs.readFile(path.join(publicRoot, relative));
    if (hasCredentialLikeContent(bytes)) throw new Error(`credential-like content in private-preview public file: ${relative}`);
  }
  await verifyPrivatePreviewRoot(path.join(publicRoot, '_preview-data'), selectedPayloadContractPath);
  return { files: actual.length, paths: actual };
}

export async function verifyPrivatePreviewSourceTree(appRoot = defaultAppRoot, selectedCleanManifestPath = cleanManifestPath, selectedPayloadContractPath = payloadContractPath) {
  const allowlistPath = path.join(appRoot, 'src/data/private-preview-source-allowlist.json');
  const allowlist = JSON.parse(await fs.readFile(allowlistPath, 'utf8'));
  if (allowlist.schema !== 'quiet_la_private_preview_source_allowlist_v1' || allowlist.status !== 'independent_exact_path_authority' || !Array.isArray(allowlist.paths)) throw new Error('independent source allowlist schema/status drift');
  const authoritativePaths = [...allowlist.paths];
  if (new Set(authoritativePaths).size !== authoritativePaths.length || JSON.stringify(authoritativePaths) !== JSON.stringify([...authoritativePaths].sort())) throw new Error('independent source allowlist paths are duplicate or unsorted');

  const clean = JSON.parse(await fs.readFile(selectedCleanManifestPath, 'utf8'));
  if (clean.schema !== 'quiet_la_web_clean_repo_manifest_v1' || clean.status !== 'code_tests_schema_only' || !Array.isArray(clean.files)) throw new Error('clean source manifest schema/status drift');
  const cleanPaths = clean.files.map((row) => row.path);
  if (new Set(cleanPaths).size !== cleanPaths.length || JSON.stringify(cleanPaths) !== JSON.stringify([...cleanPaths].sort())) throw new Error('clean source manifest paths are duplicate or unsorted');
  if (JSON.stringify(cleanPaths) !== JSON.stringify(authoritativePaths)) throw new Error('generated clean manifest membership differs from independent source allowlist');

  const actualSourcePaths = [];
  for (const root of ['src', 'scripts', 'tests']) actualSourcePaths.push(...(await walk(appRoot, path.join(appRoot, root))));
  for (const relative of authoritativePaths.filter((entry) => !entry.startsWith('src/') && !entry.startsWith('scripts/') && !entry.startsWith('tests/'))) {
    const stat = await lstatOrNull(path.join(appRoot, relative));
    if (stat?.isFile() && !stat.isSymbolicLink()) actualSourcePaths.push(relative);
  }
  actualSourcePaths.sort();
  if (JSON.stringify(actualSourcePaths) !== JSON.stringify(authoritativePaths)) throw new Error(`actual recursive source paths differ from independent allowlist: ${actualSourcePaths.join(', ')}`);
  for (const row of clean.files) {
    const target = path.join(appRoot, row.path);
    const stat = await lstatOrNull(target);
    if (!stat?.isFile() || stat.isSymbolicLink()) throw new Error(`clean source file missing or non-regular: ${row.path}`);
    const bytes = await fs.readFile(target);
    if (bytes.length !== row.bytes || sha256(bytes) !== row.sha256) throw new Error(`clean source manifest hash/size drift: ${row.path}`);
    if (hasCredentialLikeContent(bytes)) throw new Error(`credential-like content in private-preview source file: ${row.path}`);
  }

  const allowedTop = new Set(authoritativePaths.filter((relative) => !relative.includes('/')));
  const allowedDirectories = new Set(['src', 'scripts', 'tests', 'public']);
  const ignoredGenerated = new Set(['.git', '.next', '.vercel', 'node_modules', 'out', 'release', 'playwright-report', 'test-results', 'platform-evidence']);
  for (const entry of await fs.readdir(appRoot, { withFileTypes: true })) {
    if (ignoredGenerated.has(entry.name) || entry.name === 'tsconfig.tsbuildinfo') continue;
    if (entry.isSymbolicLink()) throw new Error(`symlink at private-preview source root: ${entry.name}`);
    if (entry.isDirectory() ? !allowedDirectories.has(entry.name) : !allowedTop.has(entry.name)) throw new Error(`unallowlisted private-preview source entry: ${entry.name}`);
  }
  const publicResult = await verifyPreviewPublicTree(path.join(appRoot, 'public'), selectedPayloadContractPath);
  return { cleanFiles: clean.files.length, publicFiles: publicResult.files };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  verifyPrivatePreviewSourceTree().then((result) => console.log(JSON.stringify({ status: 'PRIVATE_PREVIEW_SOURCE_TREE_VALID', ...result }, null, 2))).catch((error) => { console.error(`verify-private-preview-source-tree: ${error.message}`); process.exitCode = 1; });
}
