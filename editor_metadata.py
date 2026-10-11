"""Stable, read-only editor metadata derived from the parsed Latent AST.

The JSON contract is intentionally separate from the diagnostic AST dump: it
contains only language features needed by editor navigation and folding.
"""
import json

import nodes


FORMAT = "latent-editor-metadata"
VERSION = 1
_BLOCK_TYPES = (nodes.FnDef, nodes.ClassDef, nodes.If, nodes.While,
                nodes.For, nodes.Try)


def _statement_groups(statement):
    if isinstance(statement, (nodes.FnDef, nodes.ClassDef)):
        if isinstance(statement, nodes.FnDef):
            return [statement.body]
        return [statement.methods]
    if isinstance(statement, nodes.If):
        return [statement.then_body, statement.else_body or []]
    if isinstance(statement, (nodes.While, nodes.For)):
        return [statement.body]
    if isinstance(statement, nodes.Try):
        return [statement.body, statement.handler]
    return []


def _end_line(statement):
    return max([statement.line] + [
        _end_line(child)
        for group in _statement_groups(statement)
        for child in group
    ])


def _direct_declarations(statements):
    """Find declarations in a statement suite without crossing declarations."""
    for statement in statements:
        if isinstance(statement, (nodes.FnDef, nodes.ClassDef)):
            yield statement
            continue
        for group in _statement_groups(statement):
            yield from _direct_declarations(group)


def _symbol(statement):
    is_function = isinstance(statement, nodes.FnDef)
    keyword_width = 2 if is_function else 5
    children = (
        [_symbol(method) for method in statement.methods]
        if isinstance(statement, nodes.ClassDef)
        else [_symbol(child) for child in _direct_declarations(statement.body)]
    )
    return {
        "kind": "function" if is_function else "class",
        "name": statement.name,
        "range": {"startLine": statement.line,
                  "endLine": _end_line(statement)},
        "selection": {"line": statement.line,
                      "column": statement.col + keyword_width + 1},
        "children": children,
    }


def _folds(statements):
    ranges = []
    for statement in statements:
        if isinstance(statement, _BLOCK_TYPES):
            end_line = _end_line(statement)
            if end_line > statement.line:
                ranges.append({"startLine": statement.line,
                               "endLine": end_line})
        for group in _statement_groups(statement):
            ranges.extend(_folds(group))
    return ranges


def build(program):
    """Return the v1 editor metadata document for a parsed Program AST.

    Line numbers and columns in this public format are one-based. Range end
    lines are inclusive and identify the last source line belonging to the
    syntactic construct; the editor adapts these to its native coordinate model.
    """
    symbols = [_symbol(statement) for statement in _direct_declarations(
        program.stmts)]
    folding_ranges = _folds(program.stmts)
    folding_ranges.sort(key=lambda item: (item["startLine"],
                                         -item["endLine"]))
    return {
        "format": FORMAT,
        "version": VERSION,
        "symbols": symbols,
        "foldingRanges": folding_ranges,
    }


def dumps(program):
    """Serialize metadata deterministically as UTF-8-compatible JSON."""
    return json.dumps(build(program), ensure_ascii=False, indent=2) + "\n"
