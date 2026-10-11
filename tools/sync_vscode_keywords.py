#!/usr/bin/env python3
"""Synchronize VS Code keyword highlighting and completions with the Latent lexer."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lex import KEYWORDS  # noqa: E402


# Specialized editor scopes for the existing language vocabulary. Any future
# lexer keyword not listed here still receives the generic keyword.other scope.
KEYWORD_GROUPS = {
    "blockKeywords": {
        "fn", "class", "if", "elif", "else", "while", "for", "in",
        "try", "catch", "return", "break", "continue", "throw",
    },
    "constants": {"true", "false", "nil"},
    "wordOperators": {"and", "or", "not"},
    "languageWords": {"say", "py", "java", "super", "import", "as"},
}


def _match(words: set[str]) -> str:
    if not words:
        return "(?!)"
    return r"\b(?:" + "|".join(sorted(words)) + r")\b"


def _replace_group_match(text: str, group_name: str, value: str) -> str:
    pattern = re.compile(
        rf'(^    "{re.escape(group_name)}": \{{\n'
        rf'      "name": "[^"]+",\n'
        rf'      "match": )"(?:\\.|[^"\\])*"',
        re.MULTILINE,
    )
    replacement = json.dumps(value, ensure_ascii=False)
    updated, count = pattern.subn(lambda match: match.group(1) + replacement, text)
    if count != 1:
        raise ValueError(f"expected exactly one simple grammar rule for {group_name}; found {count}")
    return updated


def _render_artifacts() -> dict[Path, str]:
    assigned: set[str] = set()
    for group_name, words in KEYWORD_GROUPS.items():
        if not words <= KEYWORDS:
            missing = ", ".join(sorted(words - KEYWORDS))
            raise ValueError(f"{group_name} contains words absent from lex.KEYWORDS: {missing}")
        overlap = assigned & words
        if overlap:
            raise ValueError(f"keyword groups overlap: {', '.join(sorted(overlap))}")
        assigned.update(words)

    remaining = set(KEYWORDS) - assigned
    grammar_path = ROOT / "vscode-latent" / "syntaxes" / "latent.tmLanguage.json"
    grammar_text = grammar_path.read_text(encoding="utf-8")
    grammar = json.loads(grammar_text)
    repository = grammar["repository"]

    for group_name, words in KEYWORD_GROUPS.items():
        if group_name not in repository:
            raise ValueError(f"grammar is missing the {group_name} rule")
        grammar_text = _replace_group_match(grammar_text, group_name, _match(words))

    generic_match = _match(remaining)
    if "otherKeywords" not in repository:
        generic_rule = (
            '    "otherKeywords": {\n'
            '      "name": "keyword.other.latent",\n'
            f'      "match": {json.dumps(generic_match)}\n'
            '    },\n'
        )
        grammar_text, count = re.subn(
            r'(?m)^(    "numbers": \{)$',
            lambda match: generic_rule + match.group(1),
            grammar_text,
            count=1,
        )
        if count != 1:
            raise ValueError("could not insert the generated otherKeywords rule")
    else:
        grammar_text = _replace_group_match(grammar_text, "otherKeywords", generic_match)

    if {"include": "#otherKeywords"} not in grammar["patterns"]:
        language_include = re.compile(
            r'(?m)^(    \{ "include": "#languageWords" \},)$'
        )
        grammar_text, count = language_include.subn(
            r'\1\n    { "include": "#otherKeywords" },', grammar_text, count=1
        )
        if count != 1:
            raise ValueError("grammar is missing its #languageWords include")

    # Catch malformed output before either writing it or comparing it in CI.
    json.loads(grammar_text)
    completion_path = ROOT / "vscode-latent" / "keyword-completions.json"
    completion_text = json.dumps(sorted(KEYWORDS), ensure_ascii=False, indent=2) + "\n"
    return {grammar_path: grammar_text, completion_path: completion_text}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail instead of writing when generated editor artifacts are stale",
    )
    args = parser.parse_args()

    artifacts = _render_artifacts()
    stale = []
    for path, expected in artifacts.items():
        try:
            actual = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            actual = None
        if actual != expected:
            stale.append(path)

    if args.check:
        if stale:
            names = ", ".join(path.relative_to(ROOT).as_posix() for path in stale)
            print(
                f"Generated VS Code language support is out of date ({names}). "
                "Run python3 tools/sync_vscode_keywords.py from the repository root.",
                file=sys.stderr,
            )
            return 1
        print("VS Code grammar and completion keywords match lex.KEYWORDS.")
        return 0

    if not stale:
        print("VS Code grammar and completion keywords are already up to date.")
        return 0

    for path in stale:
        path.write_text(artifacts[path], encoding="utf-8")
        print(f"Updated {path.relative_to(ROOT)} from lex.KEYWORDS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
