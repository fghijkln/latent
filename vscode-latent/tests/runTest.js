'use strict';

const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { runTests } = require('@vscode/test-electron');

async function main() {
  const extensionDevelopmentPath = path.resolve(__dirname, '..');
  const extensionTestsPath = path.resolve(__dirname, 'suite', 'index');
  const vscodeExecutablePath = process.env.VSCODE_EXECUTABLE || '/usr/share/code/code';
  const workspacePath = path.resolve(__dirname, '..', '..');
  const previousRuntimeDir = process.env.XDG_RUNTIME_DIR;
  const runtimeDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lt-vscode-ipc-'));
  process.env.XDG_RUNTIME_DIR = runtimeDir;
  try {
    await runTests({
      vscodeExecutablePath,
      extensionDevelopmentPath,
      extensionTestsPath,
      launchArgs: [
        workspacePath,
        '--no-sandbox',
        '--disable-workspace-trust',
        '--skip-welcome',
        '--skip-release-notes',
        '--user-data-dir', path.join(__dirname, '..', '.vscode-test-user-data')
      ]
    });
  } finally {
    if (previousRuntimeDir === undefined) delete process.env.XDG_RUNTIME_DIR;
    else process.env.XDG_RUNTIME_DIR = previousRuntimeDir;
    fs.rmSync(runtimeDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error('VS Code extension tests failed:', error);
  process.exitCode = 1;
});
