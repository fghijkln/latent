'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const oniguruma = require('vscode-oniguruma');
const { Registry } = require('vscode-textmate');

const extensionRoot = path.resolve(__dirname, '..');
const grammarPath = path.join(extensionRoot, 'syntaxes', 'latent.tmLanguage.json');
const snippetsPath = path.join(extensionRoot, 'snippets', 'latent.json');

async function loadGrammar() {
  await oniguruma.loadWASM(fs.readFileSync(require.resolve('vscode-oniguruma/release/onig.wasm')));
  const grammarData = JSON.parse(fs.readFileSync(grammarPath, 'utf8'));
  const registry = new Registry({
    onigLib: Promise.resolve({
      createOnigScanner: (sources) => new oniguruma.OnigScanner(sources),
      createOnigString: (value) => new oniguruma.OnigString(value)
    }),
    loadGrammar: async (scopeName) => scopeName === 'source.latent' ? grammarData : null
  });
  return registry.loadGrammar('source.latent');
}

function scopesAt(grammar, line, needle, occurrence = 0) {
  let index = -1;
  let from = 0;
  for (let i = 0; i <= occurrence; i += 1) {
    index = line.indexOf(needle, from);
    assert.notEqual(index, -1, `expected ${JSON.stringify(needle)} in ${JSON.stringify(line)}`);
    from = index + needle.length;
  }
  const token = grammar.tokenizeLine(line).tokens.find((candidate) => candidate.startIndex <= index && index < candidate.endIndex);
  assert.ok(token, `no TextMate token covers ${JSON.stringify(needle)} in ${JSON.stringify(line)}`);
  return token.scopes;
}

function assertScope(grammar, line, needle, expected, occurrence = 0) {
  const scopes = scopesAt(grammar, line, needle, occurrence);
  assert.ok(scopes.includes(expected), `${JSON.stringify(needle)} should have ${expected}; got ${scopes.join(' ')}`);
  return scopes;
}

async function main() {
  const grammar = await loadGrammar();

  const signature = 'fn collect(head, scale=2, *rest, **extra):';
  assertScope(grammar, signature, 'fn', 'keyword.control.latent');
  assertScope(grammar, signature, 'collect', 'entity.name.function.latent');
  assertScope(grammar, signature, 'head', 'variable.parameter.latent');
  assertScope(grammar, signature, 'scale', 'variable.parameter.latent');
  assertScope(grammar, signature, '*', 'keyword.operator.unpacking.positional.latent');
  assertScope(grammar, signature, 'rest', 'variable.parameter.variadic.positional.latent');
  assertScope(grammar, signature, '**', 'keyword.operator.unpacking.named.latent');
  assertScope(grammar, signature, 'extra', 'variable.parameter.variadic.named.latent');

  const call = 'collect(head, *items, scale=2, **mapping)';
  assertScope(grammar, call, 'items', 'variable.other.argument.unpacking.positional.latent');
  assertScope(grammar, call, 'mapping', 'variable.other.argument.unpacking.named.latent');
  assertScope(grammar, call, 'scale', 'variable.parameter.argument.latent');

  const nestedCall = 'collect(left * right, *items, **make_mapping(key=1))';
  const multiplicationScopes = assertScope(grammar, nestedCall, '*', 'keyword.operator.latent');
  assert.ok(!multiplicationScopes.includes('keyword.operator.unpacking.positional.latent'));
  assertScope(grammar, nestedCall, '*', 'keyword.operator.unpacking.positional.latent', 1);
  assertScope(grammar, nestedCall, '**', 'keyword.operator.unpacking.named.latent');
  assertScope(grammar, nestedCall, 'key', 'variable.parameter.argument.latent');

  const exponent = 'result = base ** exponent';
  const exponentScopes = assertScope(grammar, exponent, '**', 'keyword.operator.latent');
  assert.ok(!exponentScopes.includes('keyword.operator.unpacking.named.latent'));
  const ordinaryCall = 'combine(alpha + beta)';
  const ordinaryScopes = scopesAt(grammar, ordinaryCall, 'alpha');
  assert.ok(!ordinaryScopes.includes('variable.parameter.argument.latent'), 'ordinary positional expressions must not be highlighted as named labels');

  const snippets = JSON.parse(fs.readFileSync(snippetsPath, 'utf8'));
  assert.ok(snippets['Function with defaults'], 'P9 default-parameter snippet should exist');
  assert.ok(snippets['Named call'], 'P8 named-argument snippet should exist');
  assert.match(snippets['Function with variadic arguments'].body.join('\n'), /\*.*rest/);
  assert.match(snippets['Function with variadic arguments'].body.join('\n'), /\*\*.*extra/);
  assert.match(snippets['Call with unpacked arguments'].body.join('\n'), /\*.*items/);
  assert.match(snippets['Call with unpacked arguments'].body.join('\n'), /\*\*.*mapping/);

  console.log('Latent TextMate tokenization/snippet tests passed.');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
