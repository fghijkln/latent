"""Static dependency graph and lowering for local Latent modules.

The language-level module namespace is lowered to collision-free symbols in a
single backend compilation unit. Module initialization remains lazy and
cached: each generated unit gets one guarded initializer, called when its
importing module is initialized.
"""
from dataclasses import dataclass, field
import hashlib
import os
import re

import desugar
import lex
import nodes
import parse as parse_mod
import semant


_POS = re.compile(r"^(\d+):(\d+):\s*(.*)$", re.DOTALL)


class ModuleCompileError(Exception):
    def __init__(self, kind, path, line, col, message):
        super().__init__(message)
        self.kind = kind
        self.path = path
        self.line = line
        self.col = col
        self.message = message

    def raw_message(self):
        return f"{self.line}:{self.col}: {self.message}"


@dataclass
class _Unit:
    path: str
    source: str
    parsed: object
    module_id: str
    imports: list = field(default_factory=list)
    aliases: dict = field(default_factory=dict)
    globals: set = field(default_factory=set)
    symbols: dict = field(default_factory=dict)
    init_name: str = ""
    state_name: str = ""
    used_names: set = field(default_factory=set)
    lowered: object = None


def _position(raw):
    match = _POS.match(str(raw))
    if match:
        return int(match.group(1)), int(match.group(2)), match.group(3)
    return 1, 1, str(raw)


def _walk(value):
    if isinstance(value, nodes.Node):
        yield value
        for key, child in vars(value).items():
            if key != "source_path":
                yield from _walk(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk(child)


def _set_source_path(value, path):
    for node in _walk(value):
        node.source_path = path


def _nested_import(stmts):
    """Return the first import not in a module's top-level prologue."""
    for stmt in stmts:
        if isinstance(stmt, nodes.ImportStmt):
            continue
        children = []
        if isinstance(stmt, nodes.FnDef):
            children = stmt.body
        elif isinstance(stmt, nodes.ClassDef):
            children = [m for m in stmt.methods]
        elif isinstance(stmt, (nodes.If, nodes.While, nodes.For)):
            children = list(getattr(stmt, "then_body", []) or [])
            children += list(getattr(stmt, "else_body", []) or [])
            children += list(getattr(stmt, "body", []) or [])
        elif isinstance(stmt, nodes.Try):
            children = stmt.body + stmt.handler
        for child in children:
            if isinstance(child, nodes.ImportStmt):
                return child
            found = _nested_import([child])
            if found:
                return found
    return None


def _assigned_names(stmts):
    out = set()
    for stmt in stmts:
        if isinstance(stmt, nodes.Assign):
            out.add(stmt.name)
        elif isinstance(stmt, nodes.For):
            out.add(stmt.var)
            out.update(_assigned_names(stmt.body))
        elif isinstance(stmt, nodes.If):
            out.update(_assigned_names(stmt.then_body))
            if stmt.else_body:
                out.update(_assigned_names(stmt.else_body))
        elif isinstance(stmt, nodes.While):
            out.update(_assigned_names(stmt.body))
        elif isinstance(stmt, nodes.Try):
            out.add(stmt.var)
            out.update(_assigned_names(stmt.body))
            out.update(_assigned_names(stmt.handler))
    return out


def _module_globals(stmts):
    out = set()
    for stmt in stmts:
        if isinstance(stmt, (nodes.FnDef, nodes.ClassDef, nodes.ImportStmt)):
            continue
        out.update(_assigned_names([stmt]))
    return out


def _module_symbols(stmts):
    symbols = {}
    for stmt in stmts:
        if isinstance(stmt, nodes.FnDef):
            symbols[stmt.name] = "function"
        elif isinstance(stmt, nodes.ClassDef):
            symbols[stmt.name] = "class"
    for name in _module_globals(stmts):
        symbols.setdefault(name, "variable")
    return symbols


def _bound_names(stmts):
    """Collect module and function-local bindings for import alias checks."""
    out = set()
    for stmt in stmts:
        if isinstance(stmt, nodes.FnDef):
            out.add(stmt.name)
            out.update(_function_locals(stmt))
        elif isinstance(stmt, nodes.ClassDef):
            out.add(stmt.name)
            for method in stmt.methods:
                out.update(_function_locals(method))
        elif not isinstance(stmt, nodes.ImportStmt):
            out.update(_assigned_names([stmt]))
    return out


def _user_names(unit):
    return {tok.value for tok in unit_tokens(unit) if tok.kind == "NAME"}


def unit_tokens(unit):
    # Parser token streams are not retained on Program; all source NAMEs are
    # lexed again here only for collision-proof compiler symbol allocation.
    return lex.lex(unit.source)


def _fresh(base, used):
    candidate = base
    while candidate in used:
        candidate = "_" + candidate
    used.add(candidate)
    return candidate


def _meta(new_node, old_node):
    new_node.source_path = old_node.source_path
    return new_node


def _scope_globals(stmts):
    out = set()
    for stmt in stmts:
        if isinstance(stmt, nodes.GlobalStmt):
            out.update(stmt.names)
        elif isinstance(stmt, nodes.FnDef):
            continue
        elif isinstance(stmt, nodes.If):
            out.update(_scope_globals(stmt.then_body))
            out.update(_scope_globals(stmt.else_body or []))
        elif isinstance(stmt, (nodes.For, nodes.While)):
            out.update(_scope_globals(stmt.body))
        elif isinstance(stmt, nodes.Try):
            out.update(_scope_globals(stmt.body))
            out.update(_scope_globals(stmt.handler))
    return out


def _nested_function_names(stmts):
    out = set()
    for stmt in stmts:
        if isinstance(stmt, nodes.FnDef):
            out.add(stmt.name)
        elif isinstance(stmt, nodes.If):
            out.update(_nested_function_names(stmt.then_body))
            out.update(_nested_function_names(stmt.else_body or []))
        elif isinstance(stmt, (nodes.For, nodes.While)):
            out.update(_nested_function_names(stmt.body))
        elif isinstance(stmt, nodes.Try):
            out.update(_nested_function_names(stmt.body))
            out.update(_nested_function_names(stmt.handler))
    return out


def _function_locals(fn):
    return ((set(fn.params) | _assigned_names(fn.body) |
             _nested_function_names(fn.body)) - _scope_globals(fn.body))


def _rewrite_expr(expr, unit, locals_, allow_function=False):
    if isinstance(expr, nodes.Name):
        if locals_ is not None and expr.id in locals_:
            return expr
        if expr.id in unit.symbols:
            return _meta(nodes.Name(unit.symbols[expr.id][1],
                                    line=expr.line, col=expr.col), expr)
        return expr
    if isinstance(expr, nodes.Dot):
        if isinstance(expr.obj, nodes.Name) and expr.obj.id in unit.aliases:
            target = unit.aliases[expr.obj.id]
            symbol = target.symbols.get(expr.attr)
            kind = symbol[0] if symbol else None
            if expr.attr.startswith("_"):
                raise ModuleCompileError("module", unit.path, expr.line, expr.col,
                                         f"module member {expr.attr!r} is private")
            if kind is None:
                raise ModuleCompileError("module", unit.path, expr.line, expr.col,
                                         f"module has no exported name {expr.attr!r}")
            return _meta(nodes.Name(symbol[1],
                                    line=expr.line, col=expr.col), expr)
        expr.obj = _rewrite_expr(expr.obj, unit, locals_)
        return expr
    if isinstance(expr, nodes.Call):
        if isinstance(expr.func, nodes.Name) and expr.func.id == "__wgetattr" \
                and len(expr.args) == 2 and isinstance(expr.args[0], nodes.Name) \
                and expr.args[0].id in unit.aliases and \
                isinstance(expr.args[1], nodes.Str):
            alias = expr.args[0].id
            attr = expr.args[1].value
            target = unit.aliases[alias]
            symbol = target.symbols.get(attr)
            kind = symbol[0] if symbol else None
            if attr.startswith("_"):
                raise ModuleCompileError("module", unit.path, expr.line, expr.col,
                                         f"module member {attr!r} is private")
            if kind is None:
                raise ModuleCompileError("module", unit.path, expr.line, expr.col,
                                         f"module has no exported name {attr!r}")
            return _meta(nodes.Name(symbol[1],
                                    line=expr.line, col=expr.col), expr)
        if isinstance(expr.func, nodes.Name) and expr.func.id == "__wcall" \
                and len(expr.args) >= 2 and isinstance(expr.args[0], nodes.Name) \
                and expr.args[0].id in unit.aliases and \
                isinstance(expr.args[1], nodes.Str):
            alias = expr.args[0].id
            attr = expr.args[1].value
            target = unit.aliases[alias]
            symbol = target.symbols.get(attr)
            kind = symbol[0] if symbol else None
            if attr.startswith("_"):
                raise ModuleCompileError("module", unit.path, expr.line, expr.col,
                                         f"module member {attr!r} is private")
            if kind is None:
                raise ModuleCompileError("module", unit.path, expr.line, expr.col,
                                         f"module has no exported name {attr!r}")
            if kind != "function":
                raise ModuleCompileError("semant", unit.path, expr.line, expr.col,
                                         "imported value is not a function; use .new() for classes")
            args = [_rewrite_expr(arg, unit, locals_) for arg in expr.args[2:]]
            return _meta(nodes.Call(
                nodes.Name(symbol[1], line=expr.line, col=expr.col),
                args, line=expr.line, col=expr.col), expr)
        if isinstance(expr.func, nodes.Name) and expr.func.id == "__wsetattr" \
                and len(expr.args) >= 2 and isinstance(expr.args[0], nodes.Name) \
                and expr.args[0].id in unit.aliases:
            raise ModuleCompileError("module", unit.path, expr.line, expr.col,
                                     "imported module exports are read-only")
        expr.func = _rewrite_expr(expr.func, unit, locals_, allow_function=True)
        if isinstance(expr.func, nodes.Name) and expr.func.id in unit.aliases:
            # A bare namespace alias is never callable.
            raise ModuleCompileError("semant", unit.path, expr.line, expr.col,
                                     "module namespace is not callable; use alias.name(...)" )
        # If the imported attribute was a class/variable, it cannot be called
        # as a function. Constructors use alias.Class.new(...).
        if isinstance(expr.func, nodes.Name) and expr.func.id in {
                sym[1] for target in unit.aliases.values()
                for sym in target.symbols.values() if sym[0] != "function"}:
            raise ModuleCompileError("semant", unit.path, expr.line, expr.col,
                                     "imported value is not a function; use .new() for classes")
        expr.args = [_rewrite_expr(arg, unit, locals_) for arg in expr.args]
        return expr
    if isinstance(expr, nodes.SuperCall):
        expr.args = [_rewrite_expr(arg, unit, locals_) for arg in expr.args]
        return expr
    if isinstance(expr, nodes.List):
        expr.elts = [_rewrite_expr(x, unit, locals_) for x in expr.elts]
    elif isinstance(expr, nodes.Map):
        expr.pairs = [(k, _rewrite_expr(v, unit, locals_)) for k, v in expr.pairs]
    elif isinstance(expr, nodes.BinOp):
        expr.left = _rewrite_expr(expr.left, unit, locals_)
        expr.right = _rewrite_expr(expr.right, unit, locals_)
    elif isinstance(expr, nodes.UnOp):
        expr.operand = _rewrite_expr(expr.operand, unit, locals_)
    elif isinstance(expr, nodes.Subscript):
        expr.obj = _rewrite_expr(expr.obj, unit, locals_)
        expr.index = _rewrite_expr(expr.index, unit, locals_)
    elif isinstance(expr, nodes.PyImport):
        expr.module_expr = _rewrite_expr(expr.module_expr, unit, locals_)
    elif isinstance(expr, nodes.JavaImport):
        expr.class_expr = _rewrite_expr(expr.class_expr, unit, locals_)
    return expr


def _rewrite_stmt(stmt, unit, locals_=None, module_top=False):
    if isinstance(stmt, nodes.ImportStmt):
        return None
    if isinstance(stmt, nodes.GlobalStmt):
        stmt.names = [unit.symbols[name][1] if name in unit.symbols else name
                      for name in stmt.names]
        return stmt
    if isinstance(stmt, nodes.FnDef):
        if module_top:
            stmt.name = unit.symbols[stmt.name][1]
        visible = set(locals_ or ()) | _function_locals(stmt)
        visible.difference_update(_scope_globals(stmt.body))
        stmt.body = [x for x in (_rewrite_stmt(s, unit, visible) for s in stmt.body)
                     if x is not None]
        return stmt
    if isinstance(stmt, nodes.ClassDef):
        if module_top:
            stmt.name = unit.symbols[stmt.name][1]
        if stmt.parent is not None:
            ref = stmt.parent
            if ref.alias:
                target = unit.aliases.get(ref.alias)
                if target is None:
                    raise ModuleCompileError(
                        "semant", unit.path, ref.line, ref.col,
                        f"unknown module alias {ref.alias!r} in parent class")
                exported = target.symbols.get(ref.name)
                if ref.name.startswith("_"):
                    raise ModuleCompileError(
                        "module", unit.path, ref.line, ref.col,
                        f"module member {ref.name!r} is private")
                if exported is None:
                    raise ModuleCompileError(
                        "module", unit.path, ref.line, ref.col,
                        f"module has no exported name {ref.name!r}")
                if exported[0] != "class":
                    raise ModuleCompileError(
                        "semant", unit.path, ref.line, ref.col,
                        f"parent {ref.display!r} is not a Latent class")
                ref.name = exported[1]
                ref.alias = None
            elif ref.name in unit.symbols:
                kind, symbol = unit.symbols[ref.name]
                if kind != "class":
                    raise ModuleCompileError(
                        "semant", unit.path, ref.line, ref.col,
                        f"parent {ref.display!r} is not a Latent class")
                ref.name = symbol
        for method in stmt.methods:
            method.body = [x for x in (_rewrite_stmt(s, unit,
                                                     _function_locals(method))
                                       for s in method.body) if x is not None]
        return stmt
    if isinstance(stmt, nodes.Assign):
        if (locals_ is None or stmt.name not in locals_) and \
                stmt.name in unit.symbols:
            stmt.name = unit.symbols[stmt.name][1]
        stmt.value = _rewrite_expr(stmt.value, unit, locals_)
    elif isinstance(stmt, nodes.SetAttr):
        if isinstance(stmt.obj, nodes.Name) and stmt.obj.id in unit.aliases:
            raise ModuleCompileError("module", unit.path, stmt.line, stmt.col,
                                     "imported module exports are read-only")
        stmt.obj = _rewrite_expr(stmt.obj, unit, locals_)
        stmt.value = _rewrite_expr(stmt.value, unit, locals_)
    elif isinstance(stmt, nodes.SetIndex):
        stmt.obj = _rewrite_expr(stmt.obj, unit, locals_)
        stmt.index = _rewrite_expr(stmt.index, unit, locals_)
        stmt.value = _rewrite_expr(stmt.value, unit, locals_)
    elif isinstance(stmt, nodes.If):
        stmt.cond = _rewrite_expr(stmt.cond, unit, locals_)
        stmt.then_body = [x for x in (_rewrite_stmt(s, unit, locals_)
                                      for s in stmt.then_body) if x is not None]
        if stmt.else_body:
            stmt.else_body = [x for x in (_rewrite_stmt(s, unit, locals_)
                                          for s in stmt.else_body) if x is not None]
    elif isinstance(stmt, nodes.While):
        stmt.cond = _rewrite_expr(stmt.cond, unit, locals_)
        stmt.body = [x for x in (_rewrite_stmt(s, unit, locals_)
                                 for s in stmt.body) if x is not None]
    elif isinstance(stmt, nodes.For):
        stmt.iter = _rewrite_expr(stmt.iter, unit, locals_)
        if (locals_ is None or stmt.var not in locals_) and \
                stmt.var in unit.symbols:
            stmt.var = unit.symbols[stmt.var][1]
        stmt.body = [x for x in (_rewrite_stmt(s, unit, locals_)
                                 for s in stmt.body) if x is not None]
    elif isinstance(stmt, nodes.Try):
        if (locals_ is None or stmt.var not in locals_) and \
                stmt.var in unit.symbols:
            stmt.var = unit.symbols[stmt.var][1]
        stmt.body = [x for x in (_rewrite_stmt(s, unit, locals_)
                                 for s in stmt.body) if x is not None]
        stmt.handler = [x for x in (_rewrite_stmt(s, unit, locals_)
                                    for s in stmt.handler) if x is not None]
    elif isinstance(stmt, nodes.Say):
        stmt.value = _rewrite_expr(stmt.value, unit, locals_)
    elif isinstance(stmt, nodes.Throw):
        stmt.value = _rewrite_expr(stmt.value, unit, locals_)
    elif isinstance(stmt, nodes.Return):
        if stmt.value is not None:
            stmt.value = _rewrite_expr(stmt.value, unit, locals_)
    elif isinstance(stmt, nodes.ExprStmt):
        stmt.expr = _rewrite_expr(stmt.expr, unit, locals_)
    return stmt


def _load_graph(root_path):
    units = {}
    states = {}
    postorder = []

    def visit(path, from_path=None, from_node=None, stack=None):
        stack = stack or []
        path = os.path.realpath(path)
        if path in states:
            if states[path] == 1:
                cycle = stack + [path]
                importer = from_path or path
                node = from_node
                raise ModuleCompileError(
                    "module", importer, getattr(node, "line", 1),
                    getattr(node, "col", 1),
                    "module import cycle: " + " -> ".join(cycle))
            return units[path]
        try:
            with open(path, encoding="utf-8") as f:
                source = f.read()
        except OSError as exc:
            raise ModuleCompileError("module", from_path or path,
                                     getattr(from_node, "line", 1),
                                     getattr(from_node, "col", 1),
                                     f"cannot read module {path!r}: {exc.strerror or exc}")
        try:
            tokens = lex.lex(source)
        except lex.LexError as exc:
            line, col, msg = _position(exc)
            raise ModuleCompileError("lex", path, line, col, msg)
        try:
            parsed = parse_mod.parse(tokens)
        except parse_mod.ParseError as exc:
            line, col, msg = _position(exc)
            raise ModuleCompileError("parse", path, line, col, msg)
        _set_source_path(parsed, path)
        unit = _Unit(path, source, parsed, str(len(units)))
        unit.used_names = {tok.value for tok in tokens if tok.kind == "NAME"}
        units[path] = unit
        states[path] = 1
        local_stack = stack + [path]

        nested = _nested_import(parsed.stmts)
        if nested is not None:
            raise ModuleCompileError("module", path, nested.line, nested.col,
                                     "imports must be top-level module declarations")
        seen_code = False
        aliases = set()
        for stmt in parsed.stmts:
            if not isinstance(stmt, nodes.ImportStmt):
                seen_code = True
                continue
            if seen_code:
                raise ModuleCompileError("module", path, stmt.line, stmt.col,
                                         "imports must precede module code")
            if os.path.isabs(stmt.module_path):
                raise ModuleCompileError("module", path, stmt.line, stmt.col,
                                         "module path must be relative to the importing file")
            if not stmt.module_path.endswith(".lt"):
                raise ModuleCompileError("module", path, stmt.line, stmt.col,
                                         "module path must end in .lt")
            if stmt.alias in aliases:
                raise ModuleCompileError("module", path, stmt.line, stmt.col,
                                         f"duplicate module alias {stmt.alias!r}")
            aliases.add(stmt.alias)
            target_path = os.path.realpath(os.path.join(
                os.path.dirname(path), stmt.module_path))
            unit.imports.append((stmt, target_path))
            if target_path in local_stack:
                cycle = local_stack + [target_path]
                raise ModuleCompileError(
                    "module", path, stmt.line, stmt.col,
                    "module import cycle: " + " -> ".join(cycle))
            target = visit(target_path, path, stmt, local_stack)
            unit.aliases[stmt.alias] = target
        unit.globals = _module_globals(parsed.stmts)
        states[path] = 2
        postorder.append(unit)
        return unit

    root = visit(os.path.realpath(root_path))
    return root, units, postorder


def _prepare_symbols(units):
    used = set()
    for unit in units.values():
        used.update(unit.used_names)
    for unit in units.values():
        symbols = _module_symbols(unit.parsed.stmts)
        unit.symbols = {}
        digest = hashlib.sha1(unit.path.encode("utf-8")).hexdigest()[:8]
        for name, kind in symbols.items():
            generated = _fresh(f"_ltm_{digest}_{name}", used)
            unit.symbols[name] = (kind, generated)
        unit.init_name = _fresh(f"_lt_module_init_{digest}", used)
        unit.state_name = _fresh(f"_lt_module_state_{digest}", used)

        bound = _bound_names(unit.parsed.stmts)
        for alias, target in unit.aliases.items():
            if alias in bound:
                stmt = next(s for s, _ in unit.imports if s.alias == alias)
                raise ModuleCompileError(
                    "module", unit.path, stmt.line, stmt.col,
                    f"module alias {alias!r} conflicts with a local binding")


def compile_module_graph(root_path):
    """Return (lowered Program, None) or (None, ModuleCompileError)."""
    try:
        root, units, postorder = _load_graph(root_path)
        if len(units) == 1:
            try:
                prog = desugar.desugar(root.parsed)
            except desugar.DesugarError as exc:
                line, col, msg = _position(exc)
                raise ModuleCompileError("desugar", root.path, line, col, msg)
            _set_source_path(prog, root.path)
            try:
                return semant.check(prog), None
            except semant.SemantError as exc:
                node = exc.node
                raise ModuleCompileError("semant", root.path,
                                         getattr(node, "line", 1),
                                         getattr(node, "col", 1),
                                         str(exc).split(": ", 1)[-1])
        _prepare_symbols(units)
        declarations = []
        init_nodes = []
        for unit in postorder:
            try:
                lowered = desugar.desugar(unit.parsed)
            except desugar.DesugarError as exc:
                line, col, msg = _position(exc)
                raise ModuleCompileError("desugar", unit.path, line, col, msg)
            _set_source_path(lowered, unit.path)
            rewritten = []
            for stmt in lowered.stmts:
                out = _rewrite_stmt(stmt, unit, module_top=True)
                if out is not None:
                    rewritten.append(out)
            lowered = nodes.Program(rewritten)
            _set_source_path(lowered, unit.path)
            unit.lowered = lowered
            declarations.extend(s for s in lowered.stmts
                                if isinstance(s, (nodes.FnDef, nodes.ClassDef)))
        for unit in postorder:
            body = [s for s in unit.lowered.stmts
                    if not isinstance(s, (nodes.FnDef, nodes.ClassDef,
                                          nodes.ImportStmt))]
            deps = []
            for _, dep_path in unit.imports:
                dep_id = units[dep_path].module_id
                if dep_id not in deps:
                    deps.append(dep_id)
            global_names = [unit.symbols[name][1]
                            for name in sorted(unit.globals)
                            if name in unit.symbols and
                            unit.symbols[name][0] == "variable"]
            init = nodes.ModuleInit(unit.module_id, deps, body,
                                    global_names,
                                    unit.init_name, unit.state_name,
                                    root=(unit.path == root.path),
                                    line=1, col=1)
            init.source_path = unit.path
            init_nodes.append(init)
        prog = nodes.Program(declarations + init_nodes)
        prog.module_root = root.module_id
        prog.module_sources = {u.module_id: u.path for u in postorder}
        try:
            return semant.check(prog), None
        except semant.SemantError as exc:
            line, col, msg = _position(exc)
            node = exc.node
            path = getattr(node, "source_path", None) or root.path
            raise ModuleCompileError("semant", path,
                                     getattr(node, "line", line),
                                     getattr(node, "col", col), msg)
    except ModuleCompileError as exc:
        return None, exc
