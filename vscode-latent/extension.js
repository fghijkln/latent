'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

function isWithin(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`));
}

function findProjectRoot(vscode, document) {
  const folder = vscode.workspace.getWorkspaceFolder(document.uri);
  if (!folder) return null;
  const workspaceRoot = path.resolve(folder.uri.fsPath);
  let current = path.dirname(document.uri.fsPath);
  while (isWithin(workspaceRoot, current)) {
    if (fs.existsSync(path.join(current, 'latent.py'))) return current;
    if (current === workspaceRoot) break;
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return null;
}

function requireWorkspaceTrust(vscode, operation) {
  if (vscode.workspace.isTrusted) return true;
  vscode.window.showWarningMessage(
    `Latent ${operation} 已在 Restricted Mode 中禁用：此操作会执行工作区代码。请先自行确认并信任工作区后再试。`
  );
  return false;
}

function parseDiagnostics(vscode, output, projectRoot, collection) {
  const grouped = new Map();
  const pattern = /^(.*):(\d+):(\d+): (lex|parse|desugar|semant) error: (.*)$/;
  for (const line of output.split(/\r?\n/)) {
    const match = pattern.exec(line);
    if (!match) continue;
    const filePath = path.resolve(projectRoot, match[1]);
    if (!isWithin(projectRoot, filePath)) continue;
    const uri = vscode.Uri.file(filePath);
    const lineIndex = Math.max(0, Number(match[2]) - 1);
    const columnIndex = Math.max(0, Number(match[3]) - 1);
    const diagnostic = new vscode.Diagnostic(
      new vscode.Range(lineIndex, columnIndex, lineIndex, columnIndex + 1),
      `${match[4]} error: ${match[5]}`,
      vscode.DiagnosticSeverity.Error
    );
    diagnostic.source = 'Latent';
    const diagnostics = grouped.get(uri.toString()) || { uri, items: [] };
    diagnostics.items.push(diagnostic);
    grouped.set(uri.toString(), diagnostics);
  }
  for (const { uri, items } of grouped.values()) collection.set(uri, items);
}

function runProcess(executable, args, cwd, outputChannel) {
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve({ ...result, stdout, stderr, output: stdout + stderr });
    };
    outputChannel.appendLine(`$ ${[executable, ...args].map((part) => JSON.stringify(part)).join(' ')}`);
    let child;
    try {
      child = spawn(executable, args, { cwd, shell: false, windowsHide: true });
    } catch (error) {
      outputChannel.appendLine(`Failed to start process: ${error.message}`);
      finish({ code: null, error });
      return;
    }
    child.stdout.on('data', (chunk) => {
      const text = chunk.toString();
      stdout += text;
      outputChannel.append(text);
    });
    child.stderr.on('data', (chunk) => {
      const text = chunk.toString();
      stderr += text;
      outputChannel.append(text);
    });
    child.once('error', (error) => {
      outputChannel.appendLine(`Failed to start process: ${error.message}`);
      finish({ code: null, error });
    });
    child.once('close', (code, signal) => {
      outputChannel.appendLine(`Process exited with code ${code === null ? 'unknown' : code}${signal ? ` (signal ${signal})` : ''}.`);
      finish({ code, signal });
    });
  });
}

function activate(context) {
  const vscode = require('vscode');
  const output = vscode.window.createOutputChannel('Latent');
  const diagnostics = vscode.languages.createDiagnosticCollection('latent');
  context.subscriptions.push(output, diagnostics);

  async function compileOrRun(target, run) {
    const operation = run ? '运行' : '编译';
    if (!requireWorkspaceTrust(vscode, operation)) {
      return { code: null, reason: 'Workspace is not trusted', error: new Error('Workspace is not trusted') };
    }
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.languageId !== 'latent') {
      vscode.window.showWarningMessage('请先打开一个 Latent（.lt）源文件。');
      return { code: null, error: new Error('No active Latent source file') };
    }
    const document = editor.document;
    if (document.isDirty && !(await document.save())) {
      vscode.window.showWarningMessage('文件未保存，已取消编译。');
      return { code: null, error: new Error('Source file was not saved') };
    }
    const projectRoot = findProjectRoot(vscode, document);
    if (!projectRoot) {
      vscode.window.showErrorMessage('找不到 Latent 项目根目录（其中应包含 latent.py）。');
      return { code: null, error: new Error('latent.py not found in workspace') };
    }
    const config = vscode.workspace.getConfiguration('latent', document.uri);
    const pythonPath = config.get('pythonPath', 'python3');
    const outputSetting = config.get('outputDirectory', '.latent-vscode/out');
    const outputDirectory = path.resolve(projectRoot, outputSetting);
    if (!isWithin(projectRoot, outputDirectory)) {
      vscode.window.showErrorMessage('Latent 输出目录必须位于项目目录内。');
      return { code: null, error: new Error('Output directory is outside the project') };
    }

    diagnostics.clear();
    output.show(true);
    output.appendLine(`\nLatent ${run ? 'run' : 'compile'} (${target}) — ${document.uri.fsPath}`);
    const args = [
      path.join(projectRoot, 'latent.py'),
      document.uri.fsPath,
      '-t', target,
      '-o', outputDirectory
    ];
    if (run) args.push('--run');
    const result = await runProcess(pythonPath, args, projectRoot, output);
    parseDiagnostics(vscode, result.output, projectRoot, diagnostics);
    if (result.code === 0) {
      vscode.window.setStatusBarMessage(`Latent: ${run ? '运行' : '编译'}（${target}）完成`, 3500);
    } else {
      const detail = result.error ? result.error.message : `退出码 ${result.code}`;
      vscode.window.showErrorMessage(`Latent ${run ? '运行' : '编译'}失败：${detail}`);
    }
    return result;
  }

  const commands = [
    vscode.commands.registerCommand('latent.compilePython', () => compileOrRun('py', false)),
    vscode.commands.registerCommand('latent.compileJava', () => compileOrRun('java', false)),
    vscode.commands.registerCommand('latent.runPython', () => compileOrRun('py', true)),
    vscode.commands.registerCommand('latent.runJava', () => compileOrRun('java', true)),
    vscode.commands.registerCommand('latent.runTests', async () => {
      if (!requireWorkspaceTrust(vscode, '运行项目测试')) {
        return { code: null, reason: 'Workspace is not trusted', error: new Error('Workspace is not trusted') };
      }
      const editor = vscode.window.activeTextEditor;
      const projectRoot = editor ? findProjectRoot(vscode, editor.document) : null;
      if (!projectRoot) {
        vscode.window.showErrorMessage('请在 Latent 项目中打开 .lt 文件后运行测试。');
        return { code: null, error: new Error('Latent project not found') };
      }
      const testScript = path.join(projectRoot, 'tests', 'run_tests.py');
      if (!fs.existsSync(testScript)) {
        vscode.window.showErrorMessage('找不到 tests/run_tests.py。');
        return { code: null, error: new Error('Latent test runner not found') };
      }
      const config = vscode.workspace.getConfiguration('latent', editor.document.uri);
      output.show(true);
      output.appendLine('\nLatent project test suite');
      const result = await runProcess(config.get('pythonPath', 'python3'), [testScript], projectRoot, output);
      if (result.code === 0) {
        vscode.window.setStatusBarMessage('Latent: 项目测试通过', 5000);
      } else {
        vscode.window.showErrorMessage(`Latent 项目测试失败：退出码 ${result.code}`);
      }
      return result;
    })
  ];
  context.subscriptions.push(...commands);
}

function deactivate() {}

module.exports = { activate, deactivate };
