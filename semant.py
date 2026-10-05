"""Latent semantic check: name resolution, arity, definite-assignment
of locals (read-before-write is a compile error in v0.1)."""
from nodes import *

BUILTINS = {
    "__say": (1, 1), "len": (1, 1), "range": (1, 2), "str": (1, 1),
    "int": (1, 1), "push": (2, 2), "keys": (1, 1),
    "__wgetattr": (2, 2), "__wcall": (2, None), "__index": (2, 2),
    "__wsetattr": (3, 3), "__wsetindex": (3, 3),
}


class SemantError(Exception):
    pass


def _err(node, msg):
    raise SemantError(f"{node.line}:{node.col}: {msg}")


class Checker:
    def __init__(self):
        self.functions = {}   # name -> FnDef (top level)
        self.classes = {}     # name -> ClassDef (top level)
        self.globals = set()

    def run(self, prog):
        for s in prog.stmts:
            if isinstance(s, FnDef):
                if s.name in self.functions or s.name in self.classes:
                    _err(s, f"duplicate definition {s.name!r}")
                if len(set(s.params)) != len(s.params):
                    _err(s, f"duplicate parameter in {s.name!r}")
                self.functions[s.name] = s
            if isinstance(s, ClassDef):
                if s.name in self.functions or s.name in self.classes:
                    _err(s, f"duplicate definition {s.name!r}")
                seen = set()
                for m in s.methods:
                    if m.name in seen:
                        _err(m, f"duplicate method {m.name!r} in class {s.name!r}")
                    seen.add(m.name)
                    if m.name == "new":
                        _err(m, "'new' is reserved for construction "
                                f"in class {s.name!r}")
                    if len(set(m.params)) != len(m.params):
                        _err(m, f"duplicate parameter in {s.name}.{m.name}")
                self.classes[s.name] = s
        # generated Java method names must not collide with user functions
        for cname, cd in self.classes.items():
            for m in cd.methods:
                if f"{cname}_{m.name}" in self.functions:
                    _err(m, f"method {cname}.{m.name} collides with "
                            f"function {cname}_{m.name!r}")
        for s in prog.stmts:
            self.top_stmt(s, in_loop=0)
        return prog

    # ---- top level ----
    def top_stmt(self, s, in_loop):
        if isinstance(s, FnDef):
            self.fn_body(s)
        elif isinstance(s, ClassDef):
            for m in s.methods:
                self.fn_body(m)
        elif isinstance(s, Assign):
            self.top_expr(s.value, in_loop)
            self.globals.add(s.name)
        elif isinstance(s, If):
            self.top_expr(s.cond, in_loop)
            for x in s.then_body:
                self.top_stmt(x, in_loop)
            if s.else_body:
                for x in s.else_body:
                    self.top_stmt(x, in_loop)
        elif isinstance(s, While):
            self.top_expr(s.cond, in_loop)
            for x in s.body:
                self.top_stmt(x, in_loop + 1)
        elif isinstance(s, For):
            self.top_expr(s.iter, in_loop)
            self.globals.add(s.var)
            for x in s.body:
                self.top_stmt(x, in_loop + 1)
        elif isinstance(s, ExprStmt):
            self.top_expr(s.expr, in_loop)
            self._check_exprstmt(s)
        elif isinstance(s, Return):
            _err(s, "return outside function")
        elif isinstance(s, (Break, Continue)):
            if not in_loop:
                _err(s, f"{type(s).__name__.lower()} outside loop")
        else:
            _err(s, f"unexpected {type(s).__name__}")

    def top_expr(self, e, in_loop):
        self._expr(e, None, in_loop)

    # ---- function bodies ----
    def fn_body(self, fn):
        assigned = set()          # names assigned anywhere in fn -> locals
        self._collect_assigned(fn.body, assigned)
        assigned |= set(fn.params)
        ctx = {"params": set(fn.params), "assigned": assigned,
               "done": set(), "fn": fn}
        for s in fn.body:
            self.fn_stmt(s, ctx, in_loop=0)

    def _collect_assigned(self, stmts, out):
        for s in stmts:
            if isinstance(s, Assign):
                out.add(s.name)
            elif isinstance(s, For):
                out.add(s.var)
                self._collect_assigned(s.body, out)
            elif isinstance(s, If):
                self._collect_assigned(s.then_body, out)
                if s.else_body:
                    self._collect_assigned(s.else_body, out)
            elif isinstance(s, While):
                self._collect_assigned(s.body, out)
            elif isinstance(s, FnDef):
                _err(s, "nested functions not supported in v0.1")

    def fn_stmt(self, s, ctx, in_loop):
        if isinstance(s, FnDef):
            _err(s, "nested functions not supported in v0.1")
        elif isinstance(s, Assign):
            self._expr(s.value, ctx, in_loop)
            ctx["done"].add(s.name)
        elif isinstance(s, If):
            self._expr(s.cond, ctx, in_loop)
            for x in s.then_body:
                self.fn_stmt(x, ctx, in_loop)
            if s.else_body:
                for x in s.else_body:
                    self.fn_stmt(x, ctx, in_loop)
        elif isinstance(s, While):
            self._expr(s.cond, ctx, in_loop)
            for x in s.body:
                self.fn_stmt(x, ctx, in_loop + 1)
        elif isinstance(s, For):
            self._expr(s.iter, ctx, in_loop)
            ctx["done"].add(s.var)
            for x in s.body:
                self.fn_stmt(x, ctx, in_loop + 1)
        elif isinstance(s, ExprStmt):
            self._expr(s.expr, ctx, in_loop)
            self._check_exprstmt(s)
        elif isinstance(s, Return):
            if s.value is not None:
                self._expr(s.value, ctx, in_loop)
        elif isinstance(s, (Break, Continue)):
            if not in_loop:
                _err(s, f"{type(s).__name__.lower()} outside loop")
        else:
            _err(s, f"unexpected {type(s).__name__}")

    # ---- expressions ----
    def _expr(self, e, ctx, in_loop):
        if isinstance(e, (Num, Str, Bool, Nil)):
            return
        if isinstance(e, Name):
            self._resolve(e, ctx)
            return
        if isinstance(e, List):
            for x in e.elts:
                self._expr(x, ctx, in_loop)
            return
        if isinstance(e, Map):
            for _, v in e.pairs:
                self._expr(v, ctx, in_loop)
            return
        if isinstance(e, BinOp):
            self._expr(e.left, ctx, in_loop)
            self._expr(e.right, ctx, in_loop)
            return
        if isinstance(e, UnOp):
            self._expr(e.operand, ctx, in_loop)
            return
        if isinstance(e, PyImport):
            self._expr(e.module_expr, ctx, in_loop)
            return
        if isinstance(e, JavaImport):
            self._expr(e.class_expr, ctx, in_loop)
            return
        if isinstance(e, Dot):
            _err(e, "attribute access only allowed on py handles "
                    "(should have been desugared)")
        if isinstance(e, Call):
            for a in e.args:
                self._expr(a, ctx, in_loop)
            if not isinstance(e.func, Name):
                _err(e, "cannot call non-function in v0.1")
            self._check_call(e, ctx)
            return
        _err(e, f"unexpected {type(e).__name__}")

    def _check_exprstmt(self, s):
        if not isinstance(s.expr, Call):
            _err(s, "expression statement does nothing; "
                    "did you mean to call it or 'say' it?")

    def _resolve(self, e, ctx):
        name = e.id
        if name in BUILTINS or name in self.functions or name in self.classes:
            return
        if ctx is not None:
            if name in ctx["params"] or name == ctx["fn"].name:
                return
            if name in ctx["assigned"]:
                if name not in ctx["done"]:
                    _err(e, f"local {name!r} read before assignment")
                return
            if name in self.globals:
                return
            _err(e, f"undefined name {name!r}")
        else:
            if name in self.globals:
                return
            _err(e, f"undefined name {name!r}")

    def _check_call(self, e, ctx):
        name = e.func.id
        n = len(e.args)
        if name in BUILTINS:
            lo, hi = BUILTINS[name]
            if n < lo or (hi is not None and n > hi):
                _err(e, f"{name}() takes "
                        f"{lo}..{hi if hi is not None else 'many'} args, got {n}")
            return
        if name in self.functions:
            want = len(self.functions[name].params)
            if n != want:
                _err(e, f"{name}() takes {want} args, got {n}")
            return
        self._resolve(e.func, ctx)


def check(prog):
    return Checker().run(prog)
