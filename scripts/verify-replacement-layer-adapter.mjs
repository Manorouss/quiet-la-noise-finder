import { createHash } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { verifyReplacementAssetRoot } from './replacement-layer-adapter-io.mjs';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const contractPath = path.join(appRoot, 'src/data/replacement-layer-adapter-contract.json');
const manifestPath = path.join(appRoot, 'src/data/replacement-layer-admission-manifest.json');
const assetRoot = process.argv[2] ? path.resolve(process.argv[2]) : path.join(appRoot, 'public/_preview-data');


const contractBytes = await fs.readFile(contractPath);
const manifestBytes = await fs.readFile(manifestPath);
try {
  const contract = JSON.parse(contractBytes);
  const manifest = JSON.parse(manifestBytes);
  const result = await verifyReplacementAssetRoot(assetRoot, contract, manifest);
  console.log(JSON.stringify({
    contractSha256: createHash('sha256').update(contractBytes).digest('hex'),
    manifestSha256: createHash('sha256').update(manifestBytes).digest('hex'),
    assetRoot,
    ...result,
    status: 'STRUCTURAL_VALID_ADMISSION_BLOCKED',
  }, null, 2));
} catch (error) {
  console.error(`verify-replacement-layer-adapter: ${error.message}`);
  process.exitCode = 1;
}
