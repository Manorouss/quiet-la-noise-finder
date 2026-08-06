import path from 'node:path';
import { fileURLToPath } from 'node:url';

const appRoot = path.dirname(fileURLToPath(import.meta.url));
const isolatedExportDir = process.env.QUIET_LA_EXPORT_DIR;

if (isolatedExportDir && !/^\.quiet-la-export-(?:local|external|private)-\d+-[a-f0-9]{8}$/.test(isolatedExportDir)) {
  throw new Error(`unsafe QUIET_LA_EXPORT_DIR: ${isolatedExportDir}`);
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  ...(isolatedExportDir ? { distDir: isolatedExportDir } : {}),
  outputFileTracingRoot: appRoot,
  turbopack: { root: appRoot },
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  poweredByHeader: false,
  ...(process.env.QUIET_LA_PREVIEW_BUILD_ID
    ? { generateBuildId: async () => process.env.QUIET_LA_PREVIEW_BUILD_ID }
    : {}),
};

export default nextConfig;
