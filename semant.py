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
    def __init__(self, message, node=None):
        super().__init__(message)
        self.node = node


def _err(node, msg):
    raise SemantError(f"{node.line}:{node.col}: {msg}", node=node)


class Checker:
    def __init__(self):
        self.functions = {}   # name -> FnDef (top level)
        self.classes = {}     # name -> ClassDef (top level)
        self.globals = set()

    def run(self, prog):
        if any(isinstance(s, ModuleInit) for s in prog.stmts):
            return self._run_modules(prog)
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
                    if not m.params:
                        _err(m, f"method {s.name}.{m.name} must declare a receiver parameter")
                self.classes[s.name] = s
        self._validate_inheritance()
        # generated Java method names must not collide with user functions
        for cname, cd in self.classes.items():
            for m in cd.methods:
                if f"{cname}_{m.name}" in self.functions:
                    _err(m, f"method {cname}.{m.name} collides with "
                            f"function {cname}_{m.name!r}")
        for s in prog.stmts:
            self.top_stmt(s, in_loop=0)
        return prog

    def _run_modules(self, prog):
        """Check bundled module declarations and each module's init body."""
        modules = [s for s in prog.stmts if isinstance(s, ModuleInit)]
        defs = [s for s in prog.stmts if isinstance(s, (FnDef, ClassDef))]
        all_globals = set()
        by_id = {m.module_id: m for m in modules}
        for m in modules:
            all_globals.update(m.globals)
        # Collect all declarations before checking any body so cross-module
        # qualified references are visible regardless of file order.
        self.globals = set(all_globals)
        for s in defs:
            if isinstance(s, FnDef):
                if s.name in self.functions or s.name in self.classes:
                    _err(s, f"duplicate definition {s.name!r}")
                if len(set(s.params)) != len(s.params):
                    _err(s, f"duplicate parameter in {s.name!r}")
                self.functions[s.name] = s
            else:
                if s.name in self.functions or s.name in self.classes:
                    _err(s, f"duplicate definition {s.name!r}")
                seen = set()
                for method in s.methods:
                    if method.name in seen:
                        _err(method, f"duplicate method {method.name!r} in class {s.name!r}")
                    seen.add(method.name)
                    if method.name == "new":
                        _err(method, "'new' is reserved for construction "
                                    f"in class {s.name!r}")
                    if len(set(method.params)) != len(method.params):
                        _err(method, f"duplicate parameter in {s.name}.{method.name}")
                    if not method.params:
                        _err(method, f"method {s.name}.{method.name} must declare a receiver parameter")
                self.classes[s.name] = s
        self._validate_inheritance()
        for cname, cd in self.classes.items():
            for method in cd.methods:
                if f"{cname}_{method.name}" in self.functions:
                    _err(method, f"method {cname}.{method.name} collides with "
                                  f"function {cname}_{method.name!r}")
        # Function bodies can refer to any declared global in their own
        # module and to imported globals (which have already been rewritten
        # to unique symbols).
        for s in defs:
            if isinstance(s, FnDef):
                self.fn_body(s)
            else:
                for method in s.methods:
                    self.fn_body(method, classdef=s)

        # Top-level code is checked in dependency-first order. A module may
        # read imported globals after its declared imports, but same-module
        # reads still must follow their assignment as in single-file code.
        function_names = set(self.functions) | set(self.classes)
        for m in modules:
            visible = set(function_names)
            for dep_id in m.deps:
                dep = by_id[dep_id]
                visible.update(dep.globals)
                # Dependencies are emitted post-order; their initializer
                # locals are all statically allocated and readable after init.
            self.globals = visible
            for s in m.body:
                self.top_stmt(s, in_loop=0)
        return prog

    # ---- top level ----
    def top_stmt(self, s, in_loop):
        if isinstance(s, ImportStmt):
            _err(s, "imports are only supported when compiling a .lt file")
        if isinstance(s, FnDef):
            self.fn_body(s)
        elif isinstance(s, ClassDef):
            for m in s.methods:
                self.fn_body(m, classdef=s)
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
        elif isinstance(s, Try):
            for x in s.body:
                self.top_stmt(x, in_loop)
            self.globals.add(s.var)
            for x in s.handler:
                self.top_stmt(x, in_loop)
        elif isinstance(s, Throw):
            self.top_expr(s.value, in_loop)
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
    def fn_body(self, fn, classdef=None):
        assigned = set()          # names assigned anywhere in fn -> locals
        self._collect_assigned(fn.body, assigned)
        assigned |= set(fn.params)
        ctx = {"params": set(fn.params), "assigned": assigned,
               "done": set(), "fn": fn, "classdef": classdef}
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
            elif isinstance(s, Try):
                out.add(s.var)
                self._collect_assigned(s.body, out)
                self._collect_assigned(s.handler, out)
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
        elif isinstance(s, Try):
            for x in s.body:
                self.fn_stmt(x, ctx, in_loop)
            ctx["assigned"].add(s.var)
            ctx["done"].add(s.var)
            for x in s.handler:
                self.fn_stmt(x, ctx, in_loop)
        elif isinstance(s, Throw):
            self._expr(s.value, ctx, in_loop)
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
        if isinstance(e, Super):
            _err(e, "super must be called as super.method(self, ...)")
        if isinstance(e, SuperCall):
            if ctx is None or ctx["classdef"] is None:
                _err(e, "super is only valid inside an instance method")
            owner = ctx["classdef"]
            if owner.parent is None:
                _err(e, f"class {owner.source_name!r} has no parent for super")
            if not e.args or not isinstance(e.args[0], Name) or \
                    not ctx["fn"].params or \
                    e.args[0].id != ctx["fn"].params[0]:
                _err(e, "super.method() must pass the current receiver first")
            parent = self.classes[owner.parent.name]
            target = self._method_in_chain(parent, e.method)
            if target is None:
                _err(e, f"parent chain of {owner.source_name!r} has no method "
                        f"{e.method!r}")
            got = len(e.args) - 1
            want = len(target.params) - 1
            if got != want:
                _err(e, f"super.{e.method}() takes {want} args, got {got}")
            e.owner = owner
            for arg in e.args:
                self._expr(arg, ctx, in_loop)
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

    def _method_in_chain(self, cd, name):
        while cd is not None:
            for method in cd.methods:
                if method.name == name:
                    return method
            cd = self.classes.get(cd.parent.name) if cd.parent else None
        return None

    def _inheritance_error(self, cd, message):
        node = cd.parent if cd.parent is not None else cd
        raise SemantError(f"{node.line}:{node.col}: {message}", node=node)

    def _validate_inheritance(self):
        for cd in self.classes.values():
            ref = cd.parent
            if ref is None:
                continue
            if ref.alias:
                self._inheritance_error(
                    cd, f"qualified parent {ref.display!r} requires a module import")
            if ref.name not in self.classes:
                if ref.name in self.functions or ref.name in self.globals:
                    self._inheritance_error(
                        cd, f"parent {ref.display!r} is not a Latent class")
                self._inheritance_error(
                    cd, f"unknown parent class {ref.display!r}")

        state = {}
        stack = []

        def visit(cd):
            state[cd.name] = 1
            stack.append(cd)
            ref = cd.parent
            parent = self.classes.get(ref.name) if ref else None
            if parent is not None:
                if state.get(parent.name) == 1:
                    start = next(i for i, item in enumerate(stack)
                                 if item.name == parent.name)
                    chain = stack[start:] + [parent]
                    names = [item.source_name for item in chain]
                    self._inheritance_error(
                        cd, "inheritance cycle: " + " -> ".join(names))
                if state.get(parent.name, 0) == 0:
                    visit(parent)
            stack.pop()
            state[cd.name] = 2

        for cd in self.classes.values():
            if state.get(cd.name, 0) == 0:
                visit(cd)

        for cd in self.classes.values():
            parent = self.classes.get(cd.parent.name) if cd.parent else None
            for method in cd.methods:
                inherited = self._method_in_chain(parent, method.name)
                # Constructors may add parameters (as in the documented
                # Animal.init(name) -> Dog.init(name, breed) pattern); an
                # explicit super.init call is checked against the actual
                # parent implementation below. Other overrides preserve arity.
                if method.name != "init" and inherited is not None and \
                        len(method.params) != len(inherited.params):
                    _err(method,
                         f"override {cd.source_name}.{method.name} must take "
                         f"{len(inherited.params) - 1} arguments, got "
                         f"{len(method.params) - 1}")

    def _check_exprstmt(self, s):
        if not isinstance(s.expr, (Call, SuperCall)):
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
