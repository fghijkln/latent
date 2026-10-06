"""Stable, read-only JSON serialization for versioned Latent diagnostic ASTs."""
import json
import math

import nodes


FORMAT = "latent-ast"
VERSION = 5

# The public dump shape is explicit: compiler-only attributes and Python object
# reprs are never exposed. Version 2 adds NonlocalStmt, version 3 adds
# NamedArg, version 4 adds DefaultParam, and version 5 adds variadic parameter
# and unpacked-argument nodes; all older node shapes and field order remain.
_FIELDS = {
    nodes.Program: (("statements", "stmts"),),
    nodes.ImportStmt: (("module_path", "module_path"), ("alias", "alias")),
    nodes.GlobalStmt: (("names", "names"),),
    nodes.NonlocalStmt: (("names", "names"),),
    nodes.ModuleInit: (("module_id", "module_id"),
                       ("dependencies", "deps"), ("body", "body"),
                       ("globals", "globals_"),
                       ("init_name", "init_name"),
                       ("state_name", "state_name"), ("root", "root")),
    nodes.Assign: (("name", "name"), ("value", "value")),
    nodes.SetAttr: (("object", "obj"), ("attribute", "attr"),
                    ("value", "value")),
    nodes.SetIndex: (("object", "obj"), ("index", "index"),
                     ("value", "value")),
    nodes.ClassRef: (("name", "name"), ("alias", "alias")),
    nodes.ClassDef: (("name", "name"), ("parent", "parent"),
                     ("methods", "methods")),
    nodes.FnDef: (("name", "name"), ("parameters", "params"),
                  ("body", "body")),
    nodes.DefaultParam: (("name", "name"), ("default", "default")),
    nodes.RestParam: (("name", "name"),),
    nodes.ExtraParam: (("name", "name"),),
    nodes.If: (("condition", "cond"), ("then", "then_body"),
               ("else", "else_body")),
    nodes.While: (("condition", "cond"), ("body", "body")),
    nodes.For: (("variable", "var"), ("iterable", "iter"),
                ("body", "body")),
    nodes.Say: (("value", "value"),),
    nodes.Throw: (("value", "value"),),
    nodes.Try: (("body", "body"), ("variable", "var"),
                ("handler", "handler")),
    nodes.Return: (("value", "value"),),
    nodes.Break: (),
    nodes.Continue: (),
    nodes.ExprStmt: (("expression", "expr"),),
    nodes.Num: (("value", "value"),),
    nodes.Str: (("value", "value"),),
    nodes.Bool: (("value", "value"),),
    nodes.Nil: (),
    nodes.Name: (("identifier", "id"),),
    nodes.Super: (),
    nodes.SuperCall: (("method", "method"), ("arguments", "args")),
    nodes.List: (("elements", "elts"),),
    nodes.Map: (),
    nodes.BinOp: (("operator", "op"), ("left", "left"),
                  ("right", "right")),
    nodes.UnOp: (("operator", "op"), ("operand", "operand")),
    nodes.NamedArg: (("name", "name"), ("value", "value")),
    nodes.StarArg: (("value", "value"),),
    nodes.StarStarArg: (("value", "value"),),
    nodes.Call: (("function", "func"), ("arguments", "args")),
    nodes.PyImport: (("module", "module_expr"),),
    nodes.Dot: (("object", "obj"), ("attribute", "attr")),
    nodes.Subscript: (("object", "obj"), ("index", "index")),
    nodes.JavaImport: (("class", "class_expr"),),
}


def _number(value):
    if math.isfinite(value):
        return value
    if math.isnan(value):
        return "nan"
    return "inf" if value > 0 else "-inf"


def _value(value):
    if isinstance(value, nodes.Node):
        return _node(value)
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, (list, tuple)):
        return [_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_value(item) for item in value)
    return value


def _node(node):
    node_type = type(node)
    if node_type not in _FIELDS:
        raise TypeError("no stable AST dump schema for " + node_type.__name__)

    line = getattr(node, "line", 0)
    column = getattr(node, "col", 0)
    position = {
        "line": line if line > 0 else None,
        "column": column if column > 0 else None,
    }
    fields = {
        public_name: _value(getattr(node, attribute))
        for public_name, attribute in _FIELDS[node_type]
    }
    if node_type is nodes.Map:
        fields["entries"] = [
            {"key": key, "value": _value(value)}
            for key, value in node.pairs
        ]
    return {"node": node_type.__name__,
            "position": position,
            "fields": fields}


def dumps(tree, source, phase):
    """Return a deterministic, human-readable JSON document."""
    if phase not in ("parsed", "desugared"):
        raise ValueError("phase must be 'parsed' or 'desugared'")
    document = {
        "format": FORMAT,
        "version": VERSION,
        "phase": phase,
        "source": source,
        "tree": _node(tree),
    }
    return json.dumps(document, ensure_ascii=False, indent=2,
                      allow_nan=False) + "\n"
