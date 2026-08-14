import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { gunzipSync } from 'node:zlib';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { validateSourceManifest, verifyStagedRoot } from '../scripts/verify-staged-local-data.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const sourceManifestPath = path.join(appRoot, 'src/data/local-source-manifest.json');
const contractPath = path.join(appRoot, 'src/data/layer-contract.json');
const deploymentContractPath = path.join(appRoot, 'src/data/deployment-contract.json');
const stageRoot = path.join(appRoot, 'public/_local-data');

async function readJson(file, gz = false) {
  const bytes = await fs.readFile(file);
  return JSON.parse((gz ? gunzipSync(bytes) : bytes).toString('utf8'));
}
async function copyStage() {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-stage-'));
  const copy = path.join(temp, 'stage');
  await fs.cp(stageRoot, copy, { recursive: true, dereference: false });
  return { temp, copy };
}
async function expectStageFailure(mutator, message) {
  const { temp, copy } = await copyStage();
  try { await mutator(copy); await assert.rejects(() => verifyStagedRoot(copy, sourceManifestPath), message); }
  finally { await fs.rm(temp, { recursive: true, force: true }); }
}
async function runClaimScan(text) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [path.join(appRoot, 'scripts/claim-scan.mjs'), text], { cwd: appRoot, stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '';
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.on('close', (code) => resolve({ code, stderr }));
  });
}
async function walkSourceFiles(directory, suffix) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await walkSourceFiles(file, suffix));
    else if (entry.name.endsWith(suffix)) files.push(file);
  }
  return files;
}
function collectContractStrings(value, key = '') {
  if (typeof value === 'string') return key === 'forbiddenPositiveFragments' ? [] : [value];
  if (Array.isArray(value)) return value.flatMap((entry) => collectContractStrings(entry, key));
  if (value && typeof value === 'object') return Object.entries(value).flatMap(([name, entry]) => collectContractStrings(entry, name));
  return [];
}

test('local stage is exact, regular, and hash-bound', async () => {
  const result = await verifyStagedRoot(stageRoot, sourceManifestPath);
  assert.deepEqual(result, { files: 10, bytes: 2007540 });
});

test('local source manifest rejects path widening and duplicate rows before staging', async () => {
  const baseline = JSON.parse(await fs.readFile(sourceManifestPath, 'utf8'));
  const traversal = structuredClone(baseline);
  traversal.rows[0].source = '../private-evidence.json';
  assert.throws(() => validateSourceManifest(traversal), /malformed|accepted v3 namespace/);
  const duplicate = structuredClone(baseline);
  duplicate.rows[1].target = duplicate.rows[0].target;
  assert.throws(() => validateSourceManifest(duplicate), /unique/);
  const widened = structuredClone(baseline);
  widened.rows[0].localOnly = false;
  assert.throws(() => validateSourceManifest(widened), /malformed|widened/);
});

test('staging rejects changed bytes even when the file remains regular', async () => {
  await expectStageFailure(async (copy) => { await fs.appendFile(path.join(copy, 'v3/tarzana-scenario.json'), '\n tamper'); }, /hash\/size drift/);
});

test('staging rejects missing and extra files', async () => {
  await expectStageFailure(async (copy) => { await fs.rm(path.join(copy, 'v3/context/rail.json')); }, /file set/);
  await expectStageFailure(async (copy) => { await fs.writeFile(path.join(copy, 'unexpected.bin'), 'x'); }, /file set/);
});

test('staging rejects symlinked payloads and parent directories', async () => {
  await expectStageFailure(async (copy) => { await fs.rm(path.join(copy, 'v3/context/rail.json')); await fs.symlink(path.join(appRoot, 'src/data/layer-contract.json'), path.join(copy, 'v3/context/rail.json')); }, /symlink/);
  await expectStageFailure(async (copy) => { const target = path.join(copy, 'v3/context'); const moved = path.join(copy, 'context-real'); await fs.rename(target, moved); await fs.symlink(moved, target); }, /symlink/);
});

test('taxonomy and source-341 semantics are explicit', async () => {
  const contract = JSON.parse(await fs.readFile(contractPath, 'utf8'));
  assert.deepEqual(contract.taxonomy.map((entry) => entry.id), ['measured', 'modeled_calibrated', 'modeled_relative_uncalibrated', 'scenario_assumed_inputs', 'official_record_context_only', 'incomplete_not_computable', 'not_shown']);
  assert.equal(contract.compatibleCombination.admitted, false);
  assert.equal(contract.layers.find((layer) => layer.id === 'source_341_incomplete_mask').acousticCombinationEligible, false);
  const mask = await readJson(path.join(stageRoot, 'v3/context/source341-mask.json'));
  assert.equal(mask.radius_m, 1500);
  assert.equal(mask.affected_receiver_count, 6866);
  assert.equal(mask.status_class, 'incomplete_not_computable');
  assert.equal(mask.source_341_excluded, true);
});

test('real staged payload counts match accepted v3 data', async () => {
  const four = await readJson(path.join(stageRoot, 'v3/four-region-relative.json.gz'), true);
  const tarzana = await readJson(path.join(stageRoot, 'v3/tarzana-scenario.json'));
  const airports = await readJson(path.join(stageRoot, 'v3/context/airport-contours.json.gz'), true);
  const rail = await readJson(path.join(stageRoot, 'v3/context/rail.json'));
  assert.equal(four.receiver_count, 26746);
  assert.equal(four.records.length, 26746);
  assert.equal(tarzana.receiver_count, 7875);
  assert.equal(tarzana.records.length, 7875);
  assert.equal(airports.feature_count, 35);
  assert.equal(rail.metro_route_count, 6);
  assert.match(JSON.stringify(rail), /context/);
});

test('claim policy rejects sentence-local adversarial positives and allows explicit disclosures', async () => {
  for (const claim of [
    'This is not bad. The quietest current traffic map.',
    'Not only the quietest map.',
    'Measured dBA current traffic at this address is available.',
    'This is a current live traffic address prediction.',
    'Not current traffic; this is the quietest map.',
    'Not measured dBA and this is an address prediction.',
    'No current traffic data, but observed traffic is available.',
    'Never a quietness estimate; the quietest street is shown.',
  ]) {
    const result = await runClaimScan(claim);
    assert.equal(result.code, 1, claim);
  }
  const disclosure = await runClaimScan('Not measured dBA/CNEL, not quietness, and not an address prediction.');
  assert.equal(disclosure.code, 0);
  const uiFiles = [...await walkSourceFiles(path.join(appRoot, 'src/app'), '.tsx'), ...await walkSourceFiles(path.join(appRoot, 'src/components'), '.tsx')];
  for (const file of uiFiles) {
    const source = await fs.readFile(file, 'utf8');
    const visibleForbidden = [...source.matchAll(/(?:'|\")([^'\"]*(?:quietest|quietness|measured dBA|measured CNEL|current traffic|live traffic|observed traffic|address prediction)[^'\"]*)(?:'|\")/gi)].map((match) => match[1]);
    for (const copy of visibleForbidden) assert.equal((await runClaimScan(copy)).code, 0, `${file}: ${copy}`);
  }
  const contractDocument = JSON.parse(await fs.readFile(contractPath, 'utf8'));
  for (const copy of collectContractStrings(contractDocument)) {
    if (contractDocument.forbiddenPositiveFragments.some((fragment) => copy.toLowerCase().includes(fragment.toLowerCase()))) {
      assert.equal((await runClaimScan(copy)).code, 0, `contract: ${copy}`);
    }
  }
  const policySource = await fs.readFile(path.join(appRoot, 'src/lib/claim-policy.js'), 'utf8');
  const contractsSource = await fs.readFile(path.join(appRoot, 'src/lib/contracts.ts'), 'utf8');
  const scannerSource = await fs.readFile(path.join(appRoot, 'scripts/claim-scan.mjs'), 'utf8');
  assert.match(contractsSource, /claim-policy\.js/);
  assert.match(scannerSource, /claim-policy\.js/);
  assert.match(policySource, /immediately[\s\S]*preceded/);
});

test('UI binds status labels to the typed contract and keeps combination disabled', async () => {
  const source = await fs.readFile(path.join(appRoot, 'src/app/page.tsx'), 'utf8');
  assert.match(source, /classLabel\(layer\.evidenceClass\)/);
  assert.match(source, /Compatible acoustic sum — not admitted/);
  assert.match(source, /visual-only|Visual co-display only/);
});

test('basemap is an explicit OSM contract with visible attribution and paper fallback', async () => {
  const deployment = JSON.parse(await fs.readFile(deploymentContractPath, 'utf8'));
  assert.deepEqual(deployment.basemap, {
    provider: 'OpenStreetMap',
    tileUrl: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap contributors',
    licenseUrl: 'https://www.openstreetmap.org/copyright',
    tilePolicyUrl: 'https://operations.osmfoundation.org/policies/tiles/',
    bundledTiles: false,
    visibleAttributionRequired: true,
    fallback: 'paper_background',
  });
  assert.equal(deployment.scientificDataAssetsSameOriginOnly, true);
  assert.deepEqual(deployment.remoteBasemapAllowedOrigins, ['https://tile.openstreetmap.org']);
  const mapSource = await fs.readFile(path.join(appRoot, 'src/components/MapCanvas.tsx'), 'utf8');
  assert.match(mapSource, /https:\/\/tile\.openstreetmap\.org\/\{z\}\/\{x\}\/\{y\}\.png/);
  assert.match(mapSource, /© OpenStreetMap contributors/);
  assert.match(mapSource, /paper field remains active/);
  const runtimeOrigins = [...mapSource.matchAll(/https?:\/\/[^'"`\s]+/g)].map((match) => new URL(match[0]).origin);
  assert.deepEqual([...new Set(runtimeOrigins)], deployment.remoteBasemapAllowedOrigins);
  const pageSource = await fs.readFile(path.join(appRoot, 'src/app/page.tsx'), 'utf8');
  assert.doesNotMatch(pageSource, /fetch\(\s*['"`]https?:\/\//);
});

test('external verifier fails closed when local-only payloads are present', async () => {
  const script = path.join(appRoot, 'scripts/verify-external-tree.mjs');
  const result = await new Promise((resolve) => {
    const child = spawn(process.execPath, [script], { cwd: appRoot, stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '';
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.on('close', (code) => resolve({ code, stderr }));
  });
  assert.equal(result.code, 1);
  assert.match(result.stderr, /local-only data present/);
});

test('external verifier rejects injected credential-like content', async () => {
  const temp = await fs.mkdtemp(path.join(appRoot, '.tmp-external-'));
  try {
    const credentialName = ['VERCEL', 'TOKEN'].join('_');
    await fs.writeFile(path.join(temp, 'injected.txt'), `${credentialName}=do-not-publish`);
    const script = path.join(appRoot, 'scripts/verify-external-tree.mjs');
    const result = await new Promise((resolve) => {
      const child = spawn(process.execPath, [script], { cwd: appRoot, env: { ...process.env, QUIET_LA_EXTERNAL_ROOT: temp }, stdio: ['ignore', 'pipe', 'pipe'] });
      let stderr = '';
      child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
      child.on('close', (code) => resolve({ code, stderr }));
    });
    assert.equal(result.code, 1);
    assert.match(result.stderr, /credential-like content/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});
