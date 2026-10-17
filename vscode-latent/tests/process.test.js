'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { runEditorMetadata, runProcess } = require('../extension');

function makeTemporaryDirectory() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'latent-process-test-'));
}

function makeOutputChannel() {
  return {
    childOutput: '',
    append(text) { this.childOutput += text; },
    appendLine() {}
  };
}

test('runProcess caps combined captured and displayed output', async () => {
  const directory = makeTemporaryDirectory();
  try {
    const outputChannel = makeOutputChannel();
    const result = await runProcess(
      process.execPath,
      ['-e', "process.stdout.write('x'.repeat(8192)); process.stderr.write('y'.repeat(8192));"],
      directory,
      outputChannel,
      { timeoutMs: 3000, outputLimitBytes: 2048, killGraceMs: 50 }
    );

    assert.match(result.error.message, /output exceeded the 2048-byte limit/);
    assert.ok(Buffer.byteLength(result.output) <= 2048);
    assert.ok(Buffer.byteLength(outputChannel.childOutput) <= 2048);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('runProcess terminates a child that exceeds its time limit', async () => {
  const directory = makeTemporaryDirectory();
  const started = Date.now();
  try {
    const result = await runProcess(
      process.execPath,
      ['-e', "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);"],
      directory,
      makeOutputChannel(),
      { timeoutMs: 100, outputLimitBytes: 1024, killGraceMs: 50 }
    );

    assert.match(result.error.message, /timed out after 100 ms/);
    assert.ok(Date.now() - started < 2500);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('runProcess terminates descendants when a process group times out', {
  skip: process.platform === 'win32'
}, async () => {
  const directory = makeTemporaryDirectory();
  const marker = path.join(directory, 'descendant-output.txt');
  const descendantSource = `process.on('SIGTERM', () => {}); setInterval(() => require('node:fs').appendFileSync(${JSON.stringify(marker)}, 'x'), 10);`;
  const parentSource = [
    "const { spawn } = require('node:child_process');",
    `const descendant = spawn(process.execPath, ['-e', ${JSON.stringify(descendantSource)}], { stdio: 'ignore' });`,
    'descendant.unref();',
    "process.on('SIGTERM', () => process.exit(0));",
    'setInterval(() => {}, 1000);'
  ].join(' ');
  try {
    const result = await runProcess(
      process.execPath,
      ['-e', parentSource],
      directory,
      makeOutputChannel(),
      { timeoutMs: 500, outputLimitBytes: 4096, killGraceMs: 100 }
    );

    assert.match(result.error.message, /timed out after 500 ms/);
    assert.equal(result.code, null, 'terminated process must not report success');
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.ok(fs.existsSync(marker), 'descendant started before timeout');
    const sizeAfterTermination = fs.statSync(marker).size;
    assert.ok(sizeAfterTermination > 0);
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.equal(fs.statSync(marker).size, sizeAfterTermination, 'descendant stopped writing');
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('editor metadata rejects and terminates output over its configured limit', async () => {
  const directory = makeTemporaryDirectory();
  const compilerPath = path.join(directory, 'emit.js');
  fs.writeFileSync(compilerPath, "process.stdout.write('x'.repeat(8192)); setInterval(() => {}, 1000);\n");
  const started = Date.now();
  try {
    const metadata = await runEditorMetadata(
      process.execPath,
      compilerPath,
      directory,
      '',
      { timeoutMs: 3000, outputLimitBytes: 1024, killGraceMs: 50 }
    );

    assert.equal(metadata, null);
    assert.ok(Date.now() - started < 2500);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('editor metadata terminates a child that exceeds its time limit', async () => {
  const directory = makeTemporaryDirectory();
  const compilerPath = path.join(directory, 'hang.js');
  fs.writeFileSync(compilerPath, "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);\n");
  const started = Date.now();
  try {
    const metadata = await runEditorMetadata(
      process.execPath,
      compilerPath,
      directory,
      '',
      { timeoutMs: 100, outputLimitBytes: 1024, killGraceMs: 50 }
    );

    assert.equal(metadata, null);
    assert.ok(Date.now() - started < 2500);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
