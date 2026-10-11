'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
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

suite.addTest(new Mocha.Test('does not launch parser metadata in Restricted Mode', async () => {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  assert.ok(workspaceRoot, 'the Restricted Mode test must have an open workspace');
  const compilerPath = path.join(workspaceRoot, 'latent.py');
  const sourcePath = path.join(workspaceRoot, 'metadata-probe.lt');
  const markerPath = path.join(workspaceRoot, 'metadata-process-started');
  fs.writeFileSync(compilerPath,
    `from pathlib import Path\nPath(${JSON.stringify(markerPath)}).write_text('started')\nprint('{}')\n`);
  fs.writeFileSync(sourcePath, 'fn visible():\n    say 1\n');

  try {
    let document = await vscode.workspace.openTextDocument(vscode.Uri.file(sourcePath));
    if (document.languageId !== 'latent') {
      document = await vscode.languages.setTextDocumentLanguage(document, 'latent');
    }
    const symbols = await vscode.commands.executeCommand(
      'vscode.executeDocumentSymbolProvider', document.uri
    );
    const folds = await vscode.commands.executeCommand(
      'vscode.executeFoldingRangeProvider', document.uri
    );
    assert.deepEqual(symbols ?? [], []);
    assert.deepEqual(folds ?? [], []);
    assert.equal(fs.existsSync(markerPath), false,
      'Restricted Mode must not start the workspace compiler for editor metadata');
  } finally {
    fs.rmSync(compilerPath, { force: true });
    fs.rmSync(sourcePath, { force: true });
    fs.rmSync(markerPath, { force: true });
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
