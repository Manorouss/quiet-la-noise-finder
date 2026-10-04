// Compress a 32-byte .splat scene to Niantic SPZ (via Spark's transcoder) for the web.
//   node scripts/splat-to-spz.mjs scene.splat scene.spz
import { readFileSync, writeFileSync } from 'node:fs';
import { transcodeSpz } from '@sparkjsdev/spark';

const [input, output] = process.argv.slice(2);
if (!input || !output) throw new Error('usage: splat-to-spz.mjs <in.splat> <out.spz>');
const bytes = new Uint8Array(readFileSync(input));
const { fileBytes } = await transcodeSpz({ inputs: [{ fileBytes: bytes, fileType: 'splat', pathOrUrl: input }], maxSh: 0, fractionalBits: 12 });
writeFileSync(output, fileBytes);
console.log(JSON.stringify({ splats: bytes.length / 32, inBytes: bytes.length, outBytes: fileBytes.length }));
