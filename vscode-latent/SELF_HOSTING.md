# Language-to-extension synchronization

`lex.py` is the source of truth for reserved words through `KEYWORDS`. The VS Code extension has two generated projections of that vocabulary: TextMate scopes in `syntaxes/latent.tmLanguage.json` and the runtime completion list in `keyword-completions.json`. Both are produced by `tools/sync_vscode_keywords.py`.

From the repository root, run:

```sh
python3 tools/sync_vscode_keywords.py
```

The generator preserves specialized syntax scopes for control words, constants, word operators, and language/interoperability words. A new lexer keyword without a specialized scope receives the generic `keyword.other.latent` scope, and every keyword is included in completion suggestions. The extension CI runs the generator with `--check` and fails if either generated artifact has drifted. The completion list is packaged with the extension, so installed users do not need the compiler source tree present at runtime.

This is an incremental bootstrap loop: language vocabulary updates propagate automatically to editor highlighting and keyword completion. New syntax forms, context-sensitive completion, diagnostics, and semantic navigation still need deliberate design and tests. When a word deserves a more specific editor scope, add it to the corresponding group in the synchronization script and regenerate the artifacts.
