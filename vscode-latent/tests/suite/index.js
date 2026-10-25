'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const Mocha = require('mocha');
const vscode = require('vscode');

const extensionRoot = path.resolve(__dirname, '..', '..');
const projectRoot = path.resolve(extensionRoot, '..');
const spacedOutput = 'vscode-latent/test output/.test-out';
const mocha = new Mocha({ ui: 'bdd', timeout: 900000, reporter: 'spec' });
const extensionSuite = Mocha.Suite.create(mocha.suite, 'Latent VS Code extension');
extensionSuite.timeout(900000);
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

extensionSuite.addTest(new Mocha.Test('shows nested function and class symbols and folds indented blocks', async () => {
  const fixturePath = path.join(extensionRoot, 'tests', 'fixtures', 'editor-metadata.lt');
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(fixturePath));

  const symbols = await vscode.commands.executeCommand('vscode.executeDocumentSymbolProvider', document.uri);
  assert.deepEqual(symbols.map((symbol) => symbol.name), ['Box', 'scale']);
  assert.equal(symbols[0].kind, vscode.SymbolKind.Class);
  assert.deepEqual(symbols[0].children.map((symbol) => symbol.name), ['init', 'add']);
  assert.equal(symbols[0].range.start.line, 1);
  assert.equal(symbols[0].range.end.line, 7);
  assert.equal(symbols[0].children[0].selectionRange.start.character, 7);
  assert.equal(symbols[0].children[0].range.end.line, 4);
  assert.equal(symbols[0].children[1].range.end.line, 7);
  assert.equal(symbols[1].kind, vscode.SymbolKind.Function);
  assert.equal(symbols[1].range.start.line, 9);
  assert.equal(symbols[1].range.end.line, 9);

  const folds = await vscode.commands.executeCommand('vscode.executeFoldingRangeProvider', document.uri);
  assert.deepEqual(folds.map(({ start, end }) => [start, end]), [[1, 7], [3, 4], [6, 7], [11, 12]]);
}));

extensionSuite.addTest(new Mocha.Test('completes every generated Latent lexer keyword', async () => {
  const document = await vscode.workspace.openTextDocument({ language: 'latent', content: 'glob' });
  const completionList = await vscode.commands.executeCommand(
    'vscode.executeCompletionItemProvider',
    document.uri,
    new vscode.Position(0, 4)
  );
  const items = Array.isArray(completionList) ? completionList : completionList?.items;
  assert.ok(Array.isArray(items), 'VS Code should return a completion list');
  const labels = new Set(items.map((item) => typeof item.label === 'string' ? item.label : item.label.label));
  const generatedKeywords = require('../../keyword-completions.json');

  assert.ok(generatedKeywords.every((keyword) => labels.has(keyword)));
  assert.ok(labels.has('global'));
  assert.ok(labels.has('nonlocal'));
}));

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

extensionSuite.addTest(new Mocha.Test('rejects output paths whose symlink target is outside the project before compiling', async () => {
  const config = vscode.workspace.getConfiguration('latent');
  const externalOutput = fs.mkdtempSync(path.join(os.tmpdir(), 'latent-vscode-output-'));
  const symlinkPath = path.join(extensionRoot, `.latent-vscode-output-link-${process.pid}`);
  const linkedOutput = path.join(path.relative(projectRoot, symlinkPath), 'out');
  fs.rmSync(symlinkPath, { recursive: true, force: true });
  fs.symlinkSync(externalOutput, symlinkPath, 'dir');

  try {
    await config.update('outputDirectory', linkedOutput, vscode.ConfigurationTarget.Global);
    const result = await vscode.commands.executeCommand('latent.compilePython');
    assert.equal(result.code, null, 'an output path through an external symlink must be rejected before spawning the compiler');
    assert.match(result.error.message, /outside the project/);
    assert.deepEqual(fs.readdirSync(externalOutput), [], 'the compiler must not write through the external symlink');
  } finally {
    await config.update('outputDirectory', spacedOutput, vscode.ConfigurationTarget.Global);
    fs.rmSync(symlinkPath, { recursive: true, force: true });
    fs.rmSync(externalOutput, { recursive: true, force: true });
  }
}));

extensionSuite.addTest(new Mocha.Test('saves dirty project documents before starting project tests', async () => {
  const config = vscode.workspace.getConfiguration('latent');
  const sourcePath = path.join(extensionRoot, 'tests', `.dirty-project-test-${process.pid}.lt`);
  const sourceUri = vscode.Uri.file(sourcePath);
  const diskContents = 'old disk contents\n';
  const bufferContents = 'latest saved project version\n';
  const executableDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'latent-vscode-test-runner-'));
  const executablePath = path.join(executableDirectory, 'python-test-double.js');
  const executable = [
    '#!/usr/bin/env node',
    "const fs = require('node:fs');",
    `const sourcePath = ${JSON.stringify(sourcePath)};`,
    `const expected = ${JSON.stringify(bufferContents)};`,
    "if (fs.readFileSync(sourcePath, 'utf8') !== expected) {",
    "  console.error('Project tests saw stale disk contents');",
    '  process.exit(23);',
    '}',
    "console.log('Observed latest saved project file');"
  ].join('\n');
  fs.writeFileSync(sourcePath, diskContents);
  fs.writeFileSync(executablePath, executable);
  fs.chmodSync(executablePath, 0o755);

  try {
    await config.update('pythonPath', executablePath, vscode.ConfigurationTarget.Global);
    const document = await vscode.workspace.openTextDocument(sourceUri);
    const editor = await vscode.window.showTextDocument(document);
    await editor.edit((edit) => edit.replace(
      new vscode.Range(new vscode.Position(0, 0), new vscode.Position(0, document.lineAt(0).text.length)),
      bufferContents.trimEnd()
    ));
    assert.equal(document.isDirty, true, 'the test buffer must differ from its disk contents');

    const result = await vscode.commands.executeCommand('latent.runTests');
    assert.equal(result.code, 0, `the test runner should observe the saved buffer: ${result.output}`);
    assert.match(result.output, /Observed latest saved project file/);
    assert.equal(document.isDirty, false);
    assert.equal(fs.readFileSync(sourcePath, 'utf8'), bufferContents);
  } finally {
    await config.update('pythonPath', 'python3', vscode.ConfigurationTarget.Global);
    fs.rmSync(sourcePath, { force: true });
    fs.rmSync(executableDirectory, { recursive: true, force: true });
  }
}));

extensionSuite.addTest(new Mocha.Test('aborts project tests when saving a dirty project document fails', async () => {
  const config = vscode.workspace.getConfiguration('latent');
  const sourceDirectory = path.join(extensionRoot, 'tests', `.unsavable-project-test-${process.pid}`);
  const sourcePath = path.join(sourceDirectory, 'source.lt');
  const sourceUri = vscode.Uri.file(sourcePath);
  fs.mkdirSync(sourceDirectory);
  fs.writeFileSync(sourcePath, 'old disk contents\n');
  fs.chmodSync(sourcePath, 0o444);
  fs.chmodSync(sourceDirectory, 0o555);
  const missingExecutable = path.join(os.tmpdir(), `latent-vscode-missing-python-${process.pid}`);
  let document;

  try {
    await config.update('pythonPath', missingExecutable, vscode.ConfigurationTarget.Global);
    document = await vscode.workspace.openTextDocument(sourceUri);
    const editor = await vscode.window.showTextDocument(document);
    await editor.edit((edit) => edit.insert(new vscode.Position(0, 0), '# unsaved edit\n'));
    assert.equal(document.isDirty, true);

    const result = await vscode.commands.executeCommand('latent.runTests');
    assert.equal(result.code, null);
    assert.match(result.error.message, /Project file was not saved/);
    assert.equal(document.isDirty, true);
  } finally {
    await config.update('pythonPath', 'python3', vscode.ConfigurationTarget.Global);
    fs.chmodSync(sourceDirectory, 0o755);
    fs.chmodSync(sourcePath, 0o644);
    if (document?.isDirty) await document.save();
    fs.rmSync(sourceDirectory, { recursive: true, force: true });
  }
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

const projectRegressionTest = new Mocha.Test('runs the complete project regression suite from the extension', async () => {
  const result = await vscode.commands.executeCommand('latent.runTests');
  assert.equal(result.code, 0, `project regression suite should pass: ${result.output}`);
  assert.match(result.output, /\d+ passed, 0 failed/);
  assert.ok(fs.existsSync(path.join(projectRoot, spacedOutput)));
});
extensionSuite.addTest(projectRegressionTest);
projectRegressionTest.timeout(25 * 60 * 1000);

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
