import { spawn } from 'node:child_process';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const nextBin = path.join(appRoot, 'node_modules/next/dist/bin/next');
const command = process.argv[2] ?? 'dev';
const args = process.argv.slice(3);

if (command === 'build') {
  console.error('run-local-next: direct local build refused; use npm run build:local for isolated verified export publication');
  process.exit(1);
}

const child = spawn(process.execPath, [nextBin, command, ...args], {
  cwd: appRoot,
  env: {
    ...process.env,
    NEXT_PUBLIC_QUIET_LA_DATA_PROFILE: 'local_v3',
    NEXT_PUBLIC_QUIET_LA_DATA_ROOT: '/_local-data/v3',
    NEXT_PUBLIC_QUIET_LA_LOCAL_RECOVERY_COMMAND: 'npm run stage:local',
  },
  stdio: 'inherit',
});

child.once('error', (error) => { console.error(`run-local-next: ${error.message}`); process.exitCode = 1; });
child.once('close', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exitCode = code ?? 1;
});
