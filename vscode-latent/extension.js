'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const KEYWORDS = require('./keyword-completions.json');
const EDITOR_METADATA_TIMEOUT_MS = 15 * 1000;
const EDITOR_METADATA_OUTPUT_LIMIT_BYTES = 1 * 1024 * 1024;
const PROCESS_TIMEOUT_MS = 10 * 60 * 1000;
const PROJECT_TEST_TIMEOUT_MS = 25 * 60 * 1000;
const PROCESS_OUTPUT_LIMIT_BYTES = 4 * 1024 * 1024;
const PROCESS_KILL_GRACE_MS = 1000;

function isWithin(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative === '' || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`));
}

function resolveRealPath(candidate) {
  let current = path.resolve(candidate);
  const unresolvedPath = [];

  while (true) {
    let stat;
    try {
      stat = fs.lstatSync(current);
    } catch (error) {
      if (error.code !== 'ENOENT' && error.code !== 'ENOTDIR') throw error;
      const parent = path.dirname(current);
      if (parent === current) throw error;
      unresolvedPath.unshift(path.basename(current));
      current = parent;
      continue;
    }

    const realCurrent = fs.realpathSync(current);
    if (unresolvedPath.length > 0 && !fs.statSync(realCurrent).isDirectory()) {
      throw new Error(`Existing path component is not a directory: ${current}`);
    }
    return path.resolve(realCurrent, ...unresolvedPath);
  }
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

function isOneBasedLine(value, document) {
  return Number.isInteger(value) && value >= 1 && value <= document.lineCount;
}

function isValidMetadata(metadata, document) {
  if (!metadata || metadata.format !== 'latent-editor-metadata' || metadata.version !== 1 ||
      !Array.isArray(metadata.symbols) || !Array.isArray(metadata.foldingRanges)) {
    return false;
  }

  function validSymbol(symbol) {
    if (!symbol || !['class', 'function'].includes(symbol.kind) ||
        typeof symbol.name !== 'string' || !symbol.name ||
        !Array.isArray(symbol.children) || !symbol.range || !symbol.selection) {
      return false;
    }
    const { startLine, endLine } = symbol.range;
    const { line, column } = symbol.selection;
    if (!isOneBasedLine(startLine, document) || !isOneBasedLine(endLine, document) ||
        startLine > endLine || line !== startLine ||
        !Number.isInteger(column) || column < 1) {
      return false;
    }
    const header = document.lineAt(line - 1).text;
    if (column - 1 + symbol.name.length > header.length) return false;
    return symbol.children.every(validSymbol);
  }

  return metadata.symbols.every(validSymbol) && metadata.foldingRanges.every((range) =>
    range && isOneBasedLine(range.startLine, document) &&
    isOneBasedLine(range.endLine, document) && range.startLine < range.endLine
  );
}

function documentSymbolsFromMetadata(metadata, document, vscode) {
  function convert(symbolData) {
    const startLine = symbolData.range.startLine - 1;
    const endLine = symbolData.range.endLine - 1;
    const selectionLine = symbolData.selection.line - 1;
    const selectionStart = symbolData.selection.column - 1;
    const selectionRange = new vscode.Range(
      selectionLine,
      selectionStart,
      selectionLine,
      selectionStart + symbolData.name.length
    );
    const symbol = new vscode.DocumentSymbol(
      symbolData.name,
      '',
      symbolData.kind === 'class' ? vscode.SymbolKind.Class : vscode.SymbolKind.Function,
      new vscode.Range(startLine, 0, endLine, document.lineAt(endLine).text.length),
      selectionRange
    );
    symbol.children = symbolData.children.map(convert);
    return symbol;
  }
  return metadata.symbols.map(convert);
}

function foldingRangesFromMetadata(metadata, vscode) {
  return metadata.foldingRanges.map((range) =>
    new vscode.FoldingRange(range.startLine - 1, range.endLine - 1)
  );
}

function signalProcessTree(child, signal) {
  if (process.platform === 'win32') {
    if (!child.pid) {
      try { child.kill(signal); } catch (_) {}
      return;
    }
    try {
      const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore'
      });
      const fallback = () => { try { child.kill(signal); } catch (_) {} };
      killer.once('error', fallback);
      killer.once('close', (code) => { if (code !== 0) fallback(); });
      killer.unref();
    } catch (_) {
      try { child.kill(signal); } catch (_) {}
    }
    return;
  }

  if (child.pid) {
    try {
      process.kill(-child.pid, signal);
      return;
    } catch (error) {
      if (error.code === 'ESRCH') return;
    }
  }
  try { child.kill(signal); } catch (_) {}
}

function runBoundedProcess(executable, args, cwd, options) {
  const {
    input,
    timeoutMs,
    outputLimitBytes,
    killGraceMs = PROCESS_KILL_GRACE_MS,
    outputChannel
  } = options;
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    let outputBytes = 0;
    let settled = false;
    let terminationError = null;
    let timeoutTimer;
    let killTimer;
    let forceKillTimer;
    const clearTimers = () => {
      clearTimeout(timeoutTimer);
      clearTimeout(killTimer);
      clearTimeout(forceKillTimer);
    };
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimers();
      resolve({ ...result, stdout, stderr, output: stdout + stderr });
    };
    let child;
    try {
      child = spawn(executable, args, {
        cwd,
        shell: false,
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe'],
        detached: process.platform !== 'win32'
      });
    } catch (error) {
      finish({ code: null, error });
      return;
    }

    let terminationReason = null;
    let terminationExit = null;
    const terminate = (reason, detail) => {
      if (settled || terminationReason) return;
      terminationReason = reason;
      const message = reason === 'timeout'
        ? `Process timed out after ${timeoutMs} ms`
        : reason === 'output-limit'
          ? `Process output exceeded the ${outputLimitBytes}-byte limit`
          : `Process input failed: ${detail}`;
      terminationError = new Error(message);
      if (outputChannel) outputChannel.appendLine(`${message}; terminating process.`);
      signalProcessTree(child, 'SIGTERM');
      killTimer = setTimeout(() => {
        if (settled) return;
        signalProcessTree(child, 'SIGKILL');
        forceKillTimer = setTimeout(() => {
          finish({
            code: null,
            signal: terminationExit ? terminationExit.signal : null,
            error: terminationError
          });
        }, killGraceMs);
      }, killGraceMs);
    };

    const collect = (stream, name) => {
      stream.on('data', (chunk) => {
        if (settled || terminationReason) return;
        const remaining = Math.max(0, outputLimitBytes - outputBytes);
        const accepted = chunk.subarray(0, remaining);
        outputBytes += accepted.length;
        const text = accepted.toString('utf8');
        if (name === 'stdout') stdout += text;
        else stderr += text;
        if (outputChannel && text) outputChannel.append(text);
        if (accepted.length < chunk.length) terminate('output-limit');
      });
    };
    collect(child.stdout, 'stdout');
    collect(child.stderr, 'stderr');
    child.once('error', (error) => {
      if (terminationReason) return;
      finish({ code: null, error: terminationError || error });
    });
    child.once('close', (code, signal) => {
      if (settled) return;
      if (terminationReason) {
        terminationExit = { code, signal };
        return;
      }
      finish({ code, signal });
    });
    if (timeoutMs > 0) timeoutTimer = setTimeout(() => terminate('timeout'), timeoutMs);
    if (input !== undefined) {
      child.stdin.once('error', (error) => terminate('input-error', error.message));
      child.stdin.end(input);
    }
  });
}

function runEditorMetadata(executable, compilerPath, cwd, source, options = {}) {
  return runBoundedProcess(executable, [compilerPath, '--editor-metadata'], cwd, {
    input: source,
    timeoutMs: options.timeoutMs ?? EDITOR_METADATA_TIMEOUT_MS,
    outputLimitBytes: options.outputLimitBytes ?? EDITOR_METADATA_OUTPUT_LIMIT_BYTES,
    killGraceMs: options.killGraceMs ?? PROCESS_KILL_GRACE_MS
  }).then((result) => {
    if (result.code !== 0 || result.error) return null;
    try {
      return JSON.parse(result.stdout);
    } catch (_) {
      return null;
    }
  });
}

function runProcess(executable, args, cwd, outputChannel, options = {}) {
  outputChannel.appendLine(`$ ${[executable, ...args].map((part) => JSON.stringify(part)).join(' ')}`);
  return runBoundedProcess(executable, args, cwd, {
    timeoutMs: options.timeoutMs ?? PROCESS_TIMEOUT_MS,
    outputLimitBytes: options.outputLimitBytes ?? PROCESS_OUTPUT_LIMIT_BYTES,
    killGraceMs: options.killGraceMs ?? PROCESS_KILL_GRACE_MS,
    outputChannel
  }).then((result) => {
    if (result.error) {
      outputChannel.appendLine(`Process failed: ${result.error.message}.`);
    } else {
      outputChannel.appendLine(`Process exited with code ${result.code === null ? 'unknown' : result.code}${result.signal ? ` (signal ${result.signal})` : ''}.`);
    }
    return result;
  });
}

function activate(context) {
  const vscode = require('vscode');
  const output = vscode.window.createOutputChannel('Latent');
  const diagnostics = vscode.languages.createDiagnosticCollection('latent');
  context.subscriptions.push(output, diagnostics);
  const metadataCache = new Map();

  function getEditorMetadata(document) {
    if (!vscode.workspace.isTrusted) return Promise.resolve(null);
    const projectRoot = findProjectRoot(vscode, document);
    if (!projectRoot) return Promise.resolve(null);
    const config = vscode.workspace.getConfiguration('latent', document.uri);
    const pythonPath = config.get('pythonPath', 'python3');
    const key = document.uri.toString();
    const cached = metadataCache.get(key);
    if (cached && cached.version === document.version &&
        cached.pythonPath === pythonPath && cached.projectRoot === projectRoot) {
      return cached.promise;
    }

    const compilerPath = path.join(projectRoot, 'latent.py');
    const promise = runEditorMetadata(
      pythonPath,
      compilerPath,
      projectRoot,
      document.getText()
    ).then((metadata) => isValidMetadata(metadata, document) ? metadata : null);
    metadataCache.set(key, {
      version: document.version,
      pythonPath,
      projectRoot,
      promise
    });
    return promise;
  }

  context.subscriptions.push(vscode.workspace.onDidCloseTextDocument((document) => {
    metadataCache.delete(document.uri.toString());
  }));
  context.subscriptions.push(
    vscode.languages.registerDocumentSymbolProvider({ language: 'latent' }, {
      provideDocumentSymbols: async (document) => {
        const metadata = await getEditorMetadata(document);
        return metadata ? documentSymbolsFromMetadata(metadata, document, vscode) : [];
      }
    }),
    vscode.languages.registerFoldingRangeProvider({ language: 'latent' }, {
      provideFoldingRanges: async (document) => {
        const metadata = await getEditorMetadata(document);
        return metadata ? foldingRangesFromMetadata(metadata, vscode) : [];
      }
    }),
    vscode.languages.registerCompletionItemProvider({ language: 'latent' }, {
      provideCompletionItems: () => KEYWORDS.map((keyword) => {
        const item = new vscode.CompletionItem(keyword, vscode.CompletionItemKind.Keyword);
        item.detail = 'Latent keyword';
        return item;
      })
    })
  );

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
    let realProjectRoot;
    let realOutputDirectory;
    try {
      realProjectRoot = fs.realpathSync(projectRoot);
      realOutputDirectory = resolveRealPath(outputDirectory);
    } catch (error) {
      vscode.window.showErrorMessage('无法验证 Latent 输出目录的真实路径，已拒绝编译。');
      return { code: null, error: new Error(`Output directory could not be resolved: ${error.message}`) };
    }
    if (!isWithin(realProjectRoot, realOutputDirectory)) {
      vscode.window.showErrorMessage('Latent 输出目录必须实际位于项目目录内（包括符号链接目标）。');
      return { code: null, error: new Error('Output directory resolves outside the project') };
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
      const projectDocuments = vscode.workspace.textDocuments.filter((document) =>
        document.isDirty &&
        document.uri.scheme === 'file' &&
        isWithin(projectRoot, path.resolve(document.uri.fsPath))
      );
      for (const document of projectDocuments) {
        try {
          if (await document.save()) continue;
        } catch (error) {
          vscode.window.showWarningMessage(`文件未保存，已取消项目测试：${document.uri.fsPath}`);
          return { code: null, error: new Error(`Project file was not saved: ${document.uri.fsPath}`), cause: error };
        }
        vscode.window.showWarningMessage(`文件未保存，已取消项目测试：${document.uri.fsPath}`);
        return { code: null, error: new Error(`Project file was not saved: ${document.uri.fsPath}`) };
      }
      const config = vscode.workspace.getConfiguration('latent', editor.document.uri);
      output.show(true);
      output.appendLine('\nLatent project test suite');
      const result = await runProcess(
        config.get('pythonPath', 'python3'),
        [testScript],
        projectRoot,
        output,
        { timeoutMs: PROJECT_TEST_TIMEOUT_MS }
      );
      if (result.code === 0) {
        vscode.window.setStatusBarMessage('Latent: 项目测试通过', 5000);
      } else {
        const detail = result.error ? result.error.message : `退出码 ${result.code}`;
        vscode.window.showErrorMessage(`Latent 项目测试失败：${detail}`);
      }
      return result;
    })
  ];
  context.subscriptions.push(...commands);
}

function deactivate() {}

module.exports = { activate, deactivate, runEditorMetadata, runProcess };
