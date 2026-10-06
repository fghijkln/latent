'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Mocha = require('mocha');
const vscode = require('vscode');

const extensionRoot = path.resolve(__dirname, '..', '..');
const projectRoot = path.resolve(extensionRoot, '..');
const spacedOutput = 'vscode-latent/test output/.test-out';
const mocha = new Mocha({ ui: 'bdd', timeout: 360000, reporter: 'spec' });
const extensionSuite = Mocha.Suite.create(mocha.suite, 'Latent VS Code extension');
extensionSuite.timeout(360000);
let sampleDocument;

extensionSuite.beforeAll(async () => {
  assert.equal(vscode.workspace.isTrusted, true, 'trusted integration tests must use their isolated trusted VS Code profile');
  const extension = vscode.extensions.getExtension('fghijkln.latent-language-support');
  assert.ok(extension, 'extension should be discoverable from the Extension Host');
  await extension.activate();

  const config = vscode.workspace.getConfiguration('latent');
  await config.update('pythonPath', 'python3', vscode.ConfigurationTarget.Global);
  await config.update('outputDirectory', spacedOutput, vscode.ConfigurationTarget.Global);

  const samplePath = path.join(extensionRoot, 'tests', 'fixtures', 'hello world.lt');
  sampleDocument = await vscode.workspace.openTextDocument(vscode.Uri.file(samplePath));
  await vscode.window.showTextDocument(sampleDocument);
});

extensionSuite.addTest(new Mocha.Test('recognizes .lt and runs the Python backend with spaced paths', async () => {
  assert.equal(sampleDocument.languageId, 'latent');
  const result = await vscode.commands.executeCommand('latent.runPython');
  assert.ok(result, 'Python command should return a process result');
  assert.equal(result.code, 0, `Python backend should exit successfully: ${result.output}`);
  assert.match(result.output, /hello latent from a spaced path/);
}));

extensionSuite.addTest(new Mocha.Test('runs the Java backend with spaced paths', async () => {
  const result = await vscode.commands.executeCommand('latent.runJava');
  assert.ok(result, 'Java command should return a process result');
  assert.equal(result.code, 0, `Java backend should exit successfully: ${result.output}`);
  assert.match(result.output, /hello latent from a spaced path/);
}));

extensionSuite.addTest(new Mocha.Test('runs P10 variadic syntax through both backends in the trusted test workspace', async () => {
  const p10Path = path.join(projectRoot, 'tests', 'p10_variadic_arguments.lt');
  const p10Document = await vscode.workspace.openTextDocument(vscode.Uri.file(p10Path));
  await vscode.window.showTextDocument(p10Document);

  for (const command of ['latent.runPython', 'latent.runJava']) {
    const result = await vscode.commands.executeCommand(command);
    assert.ok(result, `${command} should return a process result`);
    assert.equal(result.code, 0, `${command} should compile and run P10 syntax: ${result.output}`);
    assert.match(result.output, /default/);
  }
}));

extensionSuite.addTest(new Mocha.Test('rejects output paths outside the project', async () => {
  const config = vscode.workspace.getConfiguration('latent');
  const forbidden = '/tmp/latent-vscode-output-must-not-be-created';
  await config.update('outputDirectory', forbidden, vscode.ConfigurationTarget.Global);
  const result = await vscode.commands.executeCommand('latent.compilePython');
  assert.equal(result.code, null, 'external output path should be rejected before spawning the compiler');
  assert.ok(result.error);
  assert.ok(!fs.existsSync(forbidden));
  await config.update('outputDirectory', spacedOutput, vscode.ConfigurationTarget.Global);
}));

extensionSuite.addTest(new Mocha.Test('maps compiler failures to VS Code diagnostics', async () => {
  const errorPath = path.join(projectRoot, 'tests', 'err_undef.lt');
  const errorDocument = await vscode.workspace.openTextDocument(vscode.Uri.file(errorPath));
  await vscode.window.showTextDocument(errorDocument);
  const result = await vscode.commands.executeCommand('latent.compilePython');
  assert.equal(result.code, 1, 'invalid source should fail compilation');
  const found = vscode.languages.getDiagnostics(errorDocument.uri);
  assert.ok(found.some((diagnostic) => diagnostic.message.includes("undefined name 'x'")));
  assert.equal(found[0].range.start.line, 0, 'diagnostic should point at the first source line');
}));

extensionSuite.addTest(new Mocha.Test('runs the complete project regression suite from the extension', async () => {
  const result = await vscode.commands.executeCommand('latent.runTests');
  assert.equal(result.code, 0, `project regression suite should pass: ${result.output}`);
  assert.match(result.output, /\d+ passed, 0 failed/);
  assert.ok(fs.existsSync(path.join(projectRoot, spacedOutput)));
}));

extensionSuite.addTest(new Mocha.Test('declares limited Workspace Trust support and restricts workspace settings', async () => {
  const manifest = require('../../package.json');
  assert.equal(manifest.capabilities?.untrustedWorkspaces?.supported, 'limited');
  assert.deepEqual(manifest.capabilities.untrustedWorkspaces.restrictedConfigurations, [
    'latent.pythonPath',
    'latent.outputDirectory'
  ]);
}));

exports.run = function run() {
  return new Promise((resolve, reject) => {
    try {
      mocha.run((failures) => {
        if (failures > 0) reject(new Error(`${failures} VS Code extension test(s) failed`));
        else resolve();
      });
    } catch (error) {
      reject(error);
    }
  });
};
