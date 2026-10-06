'use strict';

const assert = require('node:assert/strict');
const Mocha = require('mocha');
const vscode = require('vscode');

const mocha = new Mocha({ ui: 'bdd', timeout: 120000, reporter: 'spec' });
const suite = Mocha.Suite.create(mocha.suite, 'Latent VS Code extension in Restricted Mode');

suite.addTest(new Mocha.Test('activates from a contributed command and refuses workspace execution', async () => {
  assert.equal(vscode.workspace.isTrusted, false, 'this test must run in Restricted Mode');

  const extension = vscode.extensions.getExtension('fghijkln.latent-language-support');
  assert.ok(extension, 'extension should be discoverable in Restricted Mode');
  assert.equal(extension.isActive, false, 'command invocation should be the activation trigger');

  const ids = [
    'latent.compilePython',
    'latent.compileJava',
    'latent.runPython',
    'latent.runJava',
    'latent.runTests'
  ];
  for (const id of ids) {
    const result = await vscode.commands.executeCommand(id);
    assert.equal(extension.isActive, true, 'the contributed command should activate the extension');
    assert.equal(result?.code, null, `${id} must not start a process in Restricted Mode`);
    assert.equal(result?.reason, 'Workspace is not trusted');
  }
}));

exports.run = function run() {
  return new Promise((resolve, reject) => {
    try {
      mocha.run((failures) => {
        if (failures > 0) reject(new Error(`${failures} Restricted Mode test(s) failed`));
        else resolve();
      });
    } catch (error) {
      reject(error);
    }
  });
};
