import { spawn } from 'node:child_process';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const environment = process.env.VERCEL_ENV;
const branch = process.env.VERCEL_GIT_COMMIT_REF;

let script;
if (environment === 'production') script = 'build:external';
else if (environment === 'preview' && branch === 'private-preview') script = 'build:private-preview';
else {
  console.error(`build-vercel-profile: unsupported Vercel target environment=${environment ?? 'unset'} branch=${branch ?? 'unset'}`);
  process.exit(1);
}

const child = spawn('npm', ['run', script], { cwd: appRoot, env: process.env, stdio: 'inherit' });
child.once('error', (error) => { console.error(`build-vercel-profile: ${error.message}`); process.exitCode = 1; });
child.once('close', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exitCode = code ?? 1;
});
