import { spawn } from 'node:child_process';
import path from 'node:path';

const appRoot = path.resolve(new URL('..', import.meta.url).pathname);
const script = path.join(appRoot, 'scripts/browser-check.mjs');

for (const engine of ['chromium', 'webkit']) {
  const exitCode = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], {
      cwd: appRoot,
      env: { ...process.env, QUIET_LA_BROWSER_ENGINE: engine },
      stdio: 'inherit',
    });
    child.once('error', reject);
    child.once('close', (code, signal) => resolve(signal ? 1 : (code ?? 1)));
  });
  if (exitCode !== 0) {
    console.error(`run-browser-engines: ${engine} exited ${exitCode}`);
    process.exitCode = exitCode;
    break;
  }
}
