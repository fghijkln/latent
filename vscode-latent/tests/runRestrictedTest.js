'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { runTests } = require('@vscode/test-electron');

async function main() {
  const extensionDevelopmentPath = path.resolve(__dirname, '..');
  const extensionTestsPath = path.resolve(__dirname, 'restricted-suite', 'index');
  const realExecutablePath = process.env.VSCODE_EXECUTABLE || '/usr/share/code/code';
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'latent-vscode-restricted-'));
  const workspacePath = fs.mkdtempSync(path.join(os.tmpdir(), 'lt-vscode-untrusted-'));
  const previousRuntimeDir = process.env.XDG_RUNTIME_DIR;
  const previousRealExecutable = process.env.LATENT_REAL_VSCODE_EXECUTABLE;
  const runtimeDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lt-vscode-ipc-'));
  const wrapperPath = path.join(userDataDir, 'code-with-trust-enabled.js');
  fs.writeFileSync(wrapperPath, `#!/usr/bin/env node
'use strict';
const { spawnSync } = require('node:child_process');
const executable = process.env.LATENT_REAL_VSCODE_EXECUTABLE;
const args = process.argv.slice(2).filter((arg) => arg !== '--disable-workspace-trust');
const result = spawnSync(executable, args, { stdio: 'inherit' });
if (result.error) {
  console.error(result.error);
  process.exit(1);
}
process.exit(result.status === null ? 1 : result.status);
`);
  fs.chmodSync(wrapperPath, 0o700);
  process.env.XDG_RUNTIME_DIR = runtimeDir;
  process.env.LATENT_REAL_VSCODE_EXECUTABLE = realExecutablePath;

  try {
    const userDir = path.join(userDataDir, 'User');
    fs.mkdirSync(userDir, { recursive: true });
    fs.writeFileSync(path.join(userDir, 'settings.json'), JSON.stringify({
      'security.workspace.trust.startupPrompt': 'never'
    }, null, 2));

    await runTests({
      vscodeExecutablePath: wrapperPath,
      extensionDevelopmentPath,
      extensionTestsPath,
      launchArgs: [
        workspacePath,
        '--no-sandbox',
        '--skip-welcome',
        '--skip-release-notes',
        '--user-data-dir', userDataDir
      ]
    });
  } finally {
    if (previousRuntimeDir === undefined) delete process.env.XDG_RUNTIME_DIR;
    else process.env.XDG_RUNTIME_DIR = previousRuntimeDir;
    if (previousRealExecutable === undefined) delete process.env.LATENT_REAL_VSCODE_EXECUTABLE;
    else process.env.LATENT_REAL_VSCODE_EXECUTABLE = previousRealExecutable;
    fs.rmSync(runtimeDir, { recursive: true, force: true });
    fs.rmSync(userDataDir, { recursive: true, force: true });
    fs.rmSync(workspacePath, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error('VS Code Restricted Mode tests failed:', error);
  process.exitCode = 1;
});
