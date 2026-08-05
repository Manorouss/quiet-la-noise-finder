import { promises as fs } from 'node:fs';
import path from 'node:path';
import { claimTextIsSafe } from '../src/lib/claim-policy.js';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const contract = JSON.parse(await fs.readFile(path.join(appRoot, 'src/data/layer-contract.json'), 'utf8'));

const input = process.argv.slice(2).join(' ');
if (!input) {
  console.error('claim-scan: provide user-visible copy');
  process.exit(2);
}
if (!claimTextIsSafe(input, contract.forbiddenPositiveFragments)) {
  console.error('claim-scan: forbidden positive claim');
  process.exit(1);
}
console.log('claim-scan: safe');
