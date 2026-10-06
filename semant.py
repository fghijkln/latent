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
        self.module_bindings = set()
        self.top_class_types = {}

    def run(self, prog):
        if any(isinstance(s, ModuleInit) for s in prog.stmts):
            return self._run_modules(prog)
        for s in prog.stmts:
            if isinstance(s, FnDef):
                if s.name in self.functions or s.name in self.classes:
                    _err(s, f"duplicate definition {s.name!r}")
                if len(set(parameter_names(s.params))) != len(s.params):
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
                    if len(set(parameter_names(m.params))) != len(m.params):
                        _err(m, f"duplicate parameter in {s.name}.{m.name}")
                    if not m.params:
                        _err(m, f"method {s.name}.{m.name} must declare a receiver parameter")
                    if isinstance(m.params[0], DefaultParam):
                        _err(m.params[0], f"method {s.name}.{m.name} receiver parameter cannot have a default")
                    if isinstance(m.params[0], (RestParam, ExtraParam)):
                        _err(m.params[0], f"method {s.name}.{m.name} receiver must be a required positional parameter")
                self.classes[s.name] = s
        self.module_bindings = self._top_bindings(prog.stmts)
        self._seed_top_class_types(prog.stmts)
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
        self.module_bindings = set(all_globals)
        for s in defs:
            if isinstance(s, FnDef):
                if s.name in self.functions or s.name in self.classes:
                    _err(s, f"duplicate definition {s.name!r}")
                if len(set(parameter_names(s.params))) != len(s.params):
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
                    if len(set(parameter_names(method.params))) != len(method.params):
                        _err(method, f"duplicate parameter in {s.name}.{method.name}")
                    if not method.params:
                        _err(method, f"method {s.name}.{method.name} must declare a receiver parameter")
                    if isinstance(method.params[0], DefaultParam):
                        _err(method.params[0], f"method {s.name}.{method.name} receiver parameter cannot have a default")
                    if isinstance(method.params[0], (RestParam, ExtraParam)):
                        _err(method.params[0], f"method {s.name}.{method.name} receiver must be a required positional parameter")
                self.classes[s.name] = s
        self.module_bindings.update(self.functions)
        self.module_bindings.update(self.classes)
        self._validate_inheritance()
        self._seed_top_class_types([m.body for m in modules])
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
        if isinstance(s, NonlocalStmt):
            _err(s, "nonlocal declaration is only valid inside a function")
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
            cls = self._class_of(s.value, None)
            if cls is None:
                self.top_class_types.pop(s.name, None)
            else:
                self.top_class_types[s.name] = cls
        elif isinstance(s, If):
            self.top_expr(s.cond, in_loop)
            snapshot = self._snapshot_class_types(None)
            for x in s.then_body:
                self.top_stmt(x, in_loop)
            self._restore_class_types(snapshot)
            if s.else_body:
                for x in s.else_body:
                    self.top_stmt(x, in_loop)
            self._restore_class_types(snapshot)
            self._forget_assigned_class_types([s], None)
        elif isinstance(s, While):
            self.top_expr(s.cond, in_loop)
            snapshot = self._snapshot_class_types(None)
            for x in s.body:
                self.top_stmt(x, in_loop + 1)
            self._restore_class_types(snapshot)
            self._forget_assigned_class_types([s], None)
        elif isinstance(s, For):
            self.top_expr(s.iter, in_loop)
            self.globals.add(s.var)
            snapshot = self._snapshot_class_types(None)
            for x in s.body:
                self.top_stmt(x, in_loop + 1)
            self._restore_class_types(snapshot)
            self._forget_assigned_class_types([s], None)
        elif isinstance(s, ExprStmt):
            self.top_expr(s.expr, in_loop)
            self._check_exprstmt(s)
        elif isinstance(s, Try):
            snapshot = self._snapshot_class_types(None)
            for x in s.body:
                self.top_stmt(x, in_loop)
            self._restore_class_types(snapshot)
            self.globals.add(s.var)
            for x in s.handler:
                self.top_stmt(x, in_loop)
            self._restore_class_types(snapshot)
            self._forget_assigned_class_types([s], None)
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
    def _top_bindings(self, stmts):
        out = set(self.functions) | set(self.classes)

        def visit(items):
            for item in items:
                if isinstance(item, (FnDef, ClassDef, ImportStmt)):
                    continue
                if isinstance(item, Assign):
                    out.add(item.name)
                elif isinstance(item, For):
                    out.add(item.var)
                    visit(item.body)
                elif isinstance(item, If):
                    visit(item.then_body)
                    if item.else_body:
                        visit(item.else_body)
                elif isinstance(item, While):
                    visit(item.body)
                elif isinstance(item, Try):
                    out.add(item.var)
                    visit(item.body)
                    visit(item.handler)
        visit(stmts)
        return out

    def _collect_globals(self, stmts, out):
        for s in stmts:
            if isinstance(s, GlobalStmt):
                out.update(s.names)
            elif isinstance(s, FnDef):
                continue
            elif isinstance(s, If):
                self._collect_globals(s.then_body, out)
                self._collect_globals(s.else_body or [], out)
            elif isinstance(s, (For, While)):
                self._collect_globals(s.body, out)
            elif isinstance(s, Try):
                self._collect_globals(s.body, out)
                self._collect_globals(s.handler, out)

    def _collect_nonlocals(self, stmts, out):
        for s in stmts:
            if isinstance(s, NonlocalStmt):
                out.update(s.names)
            elif isinstance(s, FnDef):
                continue
            elif isinstance(s, If):
                self._collect_nonlocals(s.then_body, out)
                self._collect_nonlocals(s.else_body or [], out)
            elif isinstance(s, (For, While)):
                self._collect_nonlocals(s.body, out)
            elif isinstance(s, Try):
                self._collect_nonlocals(s.body, out)
                self._collect_nonlocals(s.handler, out)

    def _collect_nested_defs(self, stmts, out):
        for s in stmts:
            if isinstance(s, FnDef):
                out[s.name] = s
            elif isinstance(s, If):
                self._collect_nested_defs(s.then_body, out)
                self._collect_nested_defs(s.else_body or [], out)
            elif isinstance(s, (For, While)):
                self._collect_nested_defs(s.body, out)
            elif isinstance(s, Try):
                self._collect_nested_defs(s.body, out)
                self._collect_nested_defs(s.handler, out)

    def fn_body(self, fn, classdef=None, parent_ctx=None):
        assigned = set()
        self._collect_assigned(fn.body, assigned)
        globals_ = set()
        self._collect_globals(fn.body, globals_)
        nonlocals = set()
        self._collect_nonlocals(fn.body, nonlocals)
        if len(globals_) != sum(1 for s in self._global_nodes(fn.body)
                                for _ in s.names):
            _err(fn, "duplicate name in global declaration")
        if len(nonlocals) != sum(1 for s in self._nonlocal_nodes(fn.body)
                                for _ in s.names):
            _err(fn, "duplicate name in nonlocal declaration")
        params = set(parameter_names(fn.params))
        conflict = params & globals_
        if conflict:
            _err(fn, f"parameter {sorted(conflict)[0]!r} cannot be global")
        conflict = params & nonlocals
        if conflict:
            _err(fn, f"parameter {sorted(conflict)[0]!r} cannot be nonlocal")
        conflict = globals_ & nonlocals
        if conflict:
            _err(fn, f"name {sorted(conflict)[0]!r} cannot be both global and nonlocal")
        for name in globals_:
            if name not in self.module_bindings:
                _err(fn, f"global name {name!r} is not declared at module scope")
        nonlocal_bindings = {}
        for name in nonlocals:
            owner = self._enclosing_local(name, parent_ctx)
            if owner is None:
                node = next(s for s in self._nonlocal_nodes(fn.body)
                            if name in s.names)
                _err(node, f"no binding for nonlocal {name!r} found in enclosing functions")
            nonlocal_bindings[name] = owner
        assigned.difference_update(globals_ | nonlocals)
        assigned.update(params)
        nested = {}
        self._collect_nested_defs(fn.body, nested)
        ctx = {"params": params, "assigned": assigned, "done": set(),
               "globals": globals_, "nonlocals": nonlocals,
               "nonlocal_bindings": nonlocal_bindings,
               "class_types": {},
               "nested_functions": nested,
               "parent": parent_ctx, "fn": fn, "classdef": classdef}
        names = parameter_names(fn.params)
        for index, param in enumerate(fn.params):
            if not isinstance(param, DefaultParam):
                continue
            not_yet_bound = set(names[index:])
            for name_node in self._name_nodes(param.default):
                if name_node.id in not_yet_bound:
                    _err(name_node,
                         f"default for parameter {param.name!r} cannot reference "
                         f"parameter {name_node.id!r} before it is bound")
            self._expr(param.default, ctx, in_loop=0)
        for s in fn.body:
            self.fn_stmt(s, ctx, in_loop=0)

    def _name_nodes(self, value):
        if isinstance(value, Name):
            yield value
        elif isinstance(value, Node):
            for child in vars(value).values():
                yield from self._name_nodes(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                yield from self._name_nodes(child)

    def _global_nodes(self, stmts):
        found = []
        for s in stmts:
            if isinstance(s, GlobalStmt):
                found.append(s)
            elif isinstance(s, FnDef):
                continue
            elif isinstance(s, If):
                found.extend(self._global_nodes(s.then_body))
                found.extend(self._global_nodes(s.else_body or []))
            elif isinstance(s, (For, While)):
                found.extend(self._global_nodes(s.body))
            elif isinstance(s, Try):
                found.extend(self._global_nodes(s.body))
                found.extend(self._global_nodes(s.handler))
        return found

    def _nonlocal_nodes(self, stmts):
        found = []
        for s in stmts:
            if isinstance(s, NonlocalStmt):
                found.append(s)
            elif isinstance(s, FnDef):
                continue
            elif isinstance(s, If):
                found.extend(self._nonlocal_nodes(s.then_body))
                found.extend(self._nonlocal_nodes(s.else_body or []))
            elif isinstance(s, (For, While)):
                found.extend(self._nonlocal_nodes(s.body))
            elif isinstance(s, Try):
                found.extend(self._nonlocal_nodes(s.body))
                found.extend(self._nonlocal_nodes(s.handler))
        return found

    def _enclosing_local(self, name, scope):
        while scope is not None:
            if name not in scope["globals"] and name not in scope["nonlocals"] and \
                    (name in scope["params"] or name in scope["assigned"]):
                return scope
            scope = scope["parent"]
        return None

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
                out.add(s.name)

    def _class_type_maps(self, ctx):
        maps = [self.top_class_types]
        while ctx is not None:
            maps.append(ctx["class_types"])
            ctx = ctx["parent"]
        return maps

    def _snapshot_class_types(self, ctx):
        return [(mapping, dict(mapping))
                for mapping in self._class_type_maps(ctx)]

    def _restore_class_types(self, snapshot):
        for mapping, saved in snapshot:
            mapping.clear()
            mapping.update(saved)

    def _forget_assigned_class_types(self, stmts, ctx):
        names = set()
        self._collect_assigned(stmts, names)
        for mapping in self._class_type_maps(ctx):
            for name in names:
                mapping.pop(name, None)

    def fn_stmt(self, s, ctx, in_loop):
        if isinstance(s, (GlobalStmt, NonlocalStmt)):
            return
        if isinstance(s, FnDef):
            if s.name not in ctx["globals"] and s.name not in ctx["nonlocals"]:
                ctx["done"].add(s.name)
            self.fn_body(s, parent_ctx=ctx)
        elif isinstance(s, Assign):
            self._expr(s.value, ctx, in_loop)
            cls = self._class_of(s.value, ctx)
            if s.name in ctx["globals"]:
                # A global can be reassigned by another call at any time;
                # function-body analysis cannot establish its call-time type.
                self.top_class_types.pop(s.name, None)
            elif s.name in ctx["nonlocals"]:
                owner = ctx["nonlocal_bindings"][s.name]
                owner["class_types"].pop(s.name, None)
            else:
                if cls is None:
                    ctx["class_types"].pop(s.name, None)
                else:
                    ctx["class_types"][s.name] = cls
                ctx["done"].add(s.name)
        elif isinstance(s, If):
            self._expr(s.cond, ctx, in_loop)
            snapshot = self._snapshot_class_types(ctx)
            for x in s.then_body:
                self.fn_stmt(x, ctx, in_loop)
            self._restore_class_types(snapshot)
            if s.else_body:
                for x in s.else_body:
                    self.fn_stmt(x, ctx, in_loop)
            self._restore_class_types(snapshot)
            self._forget_assigned_class_types([s], ctx)
        elif isinstance(s, While):
            self._expr(s.cond, ctx, in_loop)
            snapshot = self._snapshot_class_types(ctx)
            for x in s.body:
                self.fn_stmt(x, ctx, in_loop + 1)
            self._restore_class_types(snapshot)
            self._forget_assigned_class_types([s], ctx)
        elif isinstance(s, For):
            self._expr(s.iter, ctx, in_loop)
            if s.var not in ctx["globals"] and s.var not in ctx["nonlocals"]:
                ctx["done"].add(s.var)
            snapshot = self._snapshot_class_types(ctx)
            for x in s.body:
                self.fn_stmt(x, ctx, in_loop + 1)
            self._restore_class_types(snapshot)
            self._forget_assigned_class_types([s], ctx)
        elif isinstance(s, ExprStmt):
            self._expr(s.expr, ctx, in_loop)
            self._check_exprstmt(s)
        elif isinstance(s, Try):
            snapshot = self._snapshot_class_types(ctx)
            for x in s.body:
                self.fn_stmt(x, ctx, in_loop)
            self._restore_class_types(snapshot)
            if s.var not in ctx["globals"] and s.var not in ctx["nonlocals"]:
                ctx["assigned"].add(s.var)
                ctx["done"].add(s.var)
            for x in s.handler:
                self.fn_stmt(x, ctx, in_loop)
            self._restore_class_types(snapshot)
            self._forget_assigned_class_types([s], ctx)
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
    def _expr(self, e, ctx, in_loop, as_callee=False):
        if isinstance(e, (Num, Str, Bool, Nil)):
            return
        if isinstance(e, Name):
            self._resolve(e, ctx, as_callee=as_callee)
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
                    e.args[0].id != parameter_name(ctx["fn"].params[0]):
                _err(e, "super.method() must pass the current receiver first")
            parent = self.classes[owner.parent.name]
            target = self._method_in_chain(parent, e.method)
            if target is None:
                _err(e, f"parent chain of {owner.source_name!r} has no method "
                        f"{e.method!r}")
            self._check_arguments(e, e.args[1:], target.params[1:],
                                  f"super.{e.method}")
            e.owner = owner
            for arg in e.args:
                self._arg_expr(arg, ctx, in_loop)
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
            self._expr(e.func, ctx, in_loop, as_callee=True)
            for a in e.args:
                self._arg_expr(a, ctx, in_loop)
            self._check_call(e, ctx)
            return
        _err(e, f"unexpected {type(e).__name__}")

    def _arg_expr(self, arg, ctx, in_loop):
        self._expr(arg.value if isinstance(arg, (NamedArg, StarArg, StarStarArg))
                   else arg, ctx, in_loop)

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

    def _binding(self, name, ctx):
        scope = ctx
        while scope is not None:
            if name in scope["globals"]:
                return ("global", scope)
            if name in scope["nonlocals"]:
                return ("local", scope["nonlocal_bindings"][name])
            if name in scope["params"] or name in scope["assigned"]:
                return ("local", scope)
            scope = scope["parent"]
        if name in self.functions:
            return ("function", None)
        if name in self.classes:
            return ("class", None)
        if name in self.globals:
            return ("global", None)
        return None

    def _resolve(self, e, ctx, as_callee=False):
        name = e.id
        binding = self._binding(name, ctx)
        if binding and binding[0] == "local":
            owner = binding[1]
            if owner is ctx and name not in owner["params"] and \
                    name in owner["assigned"] and \
                    name not in owner["done"]:
                _err(e, f"local {name!r} read before assignment")
            return
        if name in BUILTINS:
            if not as_callee:
                _err(e, "built-in functions are not first-class values")
            return
        if binding:
            if binding[0] == "global" and name not in self.module_bindings and \
                    name not in self.globals:
                _err(e, f"undefined global name {name!r}")
            return
        if ctx is None and name in self.globals:
            return
        _err(e, f"undefined name {name!r}")

    def _check_call(self, e, ctx):
        # Dot calls are lowered to __wcall but retain an internal marker so
        # the checker can validate a Latent signature when receiver type is known.
        if e.direct_method is not None and len(e.args) >= 2:
            cls = self._class_of(e.args[0], ctx)
            if cls is not None:
                method_name = e.direct_method
                if method_name == "new":
                    target = self._method_in_chain(cls, "init")
                    params = target.params[1:] if target else []
                    label = cls.source_name + ".new"
                    self._check_arguments(e, e.args[2:], params, label)
                else:
                    target = self._method_in_chain(cls, method_name)
                    if target is not None:
                        self._check_arguments(e, e.args[2:],
                                              target.params[1:], method_name)
            return

        n = len(e.args)
        if not isinstance(e.func, Name):
            return
        name = e.func.id
        if name in BUILTINS and not (ctx is not None and
                self._binding(name, ctx) and
                self._binding(name, ctx)[0] == "local"):
            if any(isinstance(arg, NamedArg) for arg in e.args):
                _err(e, f"built-in function {name!r} does not accept named arguments")
            if any(isinstance(arg, (StarArg, StarStarArg)) for arg in e.args):
                _err(e, f"built-in function {name!r} does not support argument unpacking")
            lo, hi = BUILTINS[name]
            if n < lo or (hi is not None and n > hi):
                _err(e, f"{name}() takes "
                        f"{lo}..{hi if hi is not None else 'many'} args, got {n}")
            return
        binding = self._binding(name, ctx)
        target = None
        if binding and binding[0] == "function":
            target = self.functions.get(name)
        elif binding and binding[0] == "local":
            target = binding[1]["nested_functions"].get(name)
        if target is not None:
            self._check_arguments(e, e.args, target.params, name)

    def _check_arguments(self, node, actuals, params, label):
        names = fixed_parameter_names(params)
        required = required_parameter_count(params)
        has_rest = rest_parameter_name(params) is not None
        has_extra = extra_parameter_name(params) is not None
        has_expansion = any(isinstance(arg, (StarArg, StarStarArg))
                            for arg in actuals)
        positional_count = sum(not isinstance(arg, (NamedArg, StarArg,
                                                      StarStarArg))
                               for arg in actuals)
        if not any(isinstance(arg, NamedArg) for arg in actuals) and \
                not has_expansion:
            if positional_count > len(names) and not has_rest:
                _err(node, f"{label}() takes {len(names)} args, got {positional_count}")
            if positional_count < required:
                if required == len(names):
                    _err(node, f"{label}() takes {len(names)} args, got {positional_count}")
                _err(node,
                     f"{label}() missing required argument {names[positional_count]!r}")
            return
        if positional_count > len(names) and not has_rest:
            _err(node, f"{label}() takes {len(names)} args, got {positional_count}")
        supplied = set(names[:min(positional_count, len(names))])
        seen_names = set()
        for arg in actuals:
            if not isinstance(arg, NamedArg):
                continue
            name = arg.name
            if name in seen_names:
                _err(arg, f"{label}() got duplicate named argument {name!r}")
            seen_names.add(name)
            if name not in names and not has_extra:
                _err(arg, f"{label}() got unexpected named argument {name!r}")
            if name in names and name in supplied:
                _err(arg, f"{label}() got multiple values for argument {name!r}")
            if name in names:
                supplied.add(name)
        missing = [name for name in names[:required] if name not in supplied]
        if missing and not has_expansion:
            _err(node, f"{label}() missing required argument {missing[0]!r}")

    def _class_of(self, expr, ctx):
        if isinstance(expr, Call) and expr.direct_method == "new" and expr.args:
            return self._class_of(expr.args[0], ctx)
        if not isinstance(expr, Name):
            return None
        name = expr.id
        scope = ctx
        while scope is not None:
            fn = scope["fn"]
            if scope["classdef"] is not None and fn.params and \
                    name == parameter_name(fn.params[0]):
                # self may be a subclass at runtime; overrides can use
                # different parameter names, so defer its direct calls.
                return None
            if name in scope["params"] or name in scope["assigned"]:
                return scope["class_types"].get(name)
            if name in scope["globals"]:
                return self.classes.get(name)
            if name in scope["nonlocals"]:
                return None
            scope = scope["parent"]
        if ctx is not None:
            return self.classes.get(name)
        return self.top_class_types.get(name) or self.classes.get(name)

    def _seed_top_class_types(self, groups):
        """Seed only unambiguous top-level assignments before function checks."""
        statements = []
        for group in groups:
            if isinstance(group, list):
                statements.extend(group)
            else:
                statements.append(group)
        pending = [stmt for stmt in statements if isinstance(stmt, Assign)]
        for _ in range(len(pending) + 1):
            changed = False
            for stmt in pending:
                cls = self._class_of(stmt.value, None)
                if cls is not None and self.top_class_types.get(stmt.name) is not cls:
                    self.top_class_types[stmt.name] = cls
                    changed = True
            if not changed:
                break


def check(prog):
    return Checker().run(prog)
