console.error('build: refused unprofiled external build. Use npm run build:local for local review or npm run build:private-preview for the audited private-preview package. A Vercel upload remains blocked until platform protection is externally verified.');
process.exitCode = 1;
