"""Latent -> C11 backend for the dynamic-value runtime."""
import math
from nodes import *


def ident(name):
    """Injective, C-identifier-safe encoding for all source names."""
    return "lt_v_" + name.encode("utf-8").hex()


def c_string(value):
    """Emit UTF-8 bytes with fixed-width octal escapes (never greedy hex)."""
    out = ['"']
    for byte in value.encode("utf-8"):
        if byte == 0x22:
            out.append('\\"')
        elif byte == 0x5c:
            out.append("\\\\")
        elif 0x20 <= byte <= 0x7e and byte != 0x3f:
            out.append(chr(byte))
        else:
            out.append("\\%03o" % byte)
    out.append('"')
    return "".join(out)


def c_num(value):
    if type(value) is int:
        if value == -(1 << 63):
            return "lt_int((-INT64_C(9223372036854775807) - INT64_C(1)))"
        return f"lt_int(INT64_C({value}))"
    if math.isnan(value):
        return "lt_float(NAN)"
    if math.isinf(value):
        return "lt_float(-INFINITY)" if value < 0 else "lt_float(INFINITY)"
    rendered = repr(value)
    if "." not in rendered and "e" not in rendered.lower():
        rendered += ".0"
    return f"lt_float({rendered})"


def _nullable_c_string(value):
    return "NULL" if value is None else c_string(value)


class CBackendError(Exception):
    """Raised when an AST construct cannot be represented by the C target."""


class Gen:
    def __init__(self, source_path="<src>"):
        self.out = []
        self.ind = 0
        self.tmp = 0
        self.source_path = source_path
        self.scope = None
        self.current_function = "main"
        self.fn_helpers = {}
        self.fn_parents = {}
        self.function_nodes = []
        self.function_contexts = {}
        self.method_helpers = {}
        self.class_specs = {}
        self.module_inits = {}
        self.module_by_id = {}
        self.globals = []
        self.active_handlers = []
        self.loop_handler_bases = []

    def w(self, text=""):
        self.out.append("    " * self.ind + text)

    def _fresh(self, prefix):
        name = f"{prefix}_{self.tmp}"
        self.tmp += 1
        return name

    def _nested_functions(self, stmts):
        for stmt in stmts:
            if isinstance(stmt, FnDef):
                yield stmt
            elif isinstance(stmt, If):
                yield from self._nested_functions(stmt.then_body)
                yield from self._nested_functions(stmt.else_body or [])
            elif isinstance(stmt, (For, While)):
                yield from self._nested_functions(stmt.body)
            elif isinstance(stmt, Try):
                yield from self._nested_functions(stmt.body)
                yield from self._nested_functions(stmt.handler)

    def _walk_nodes(self, value, seen=None):
        if seen is None:
            seen = set()
        if isinstance(value, Node):
            if id(value) in seen:
                return
            seen.add(id(value))
            yield value
            for child in vars(value).values():
                if isinstance(child, (Node, list, tuple)):
                    yield from self._walk_nodes(child, seen)
        elif isinstance(value, (list, tuple)):
            for child in value:
                yield from self._walk_nodes(child, seen)

    def _prepare_functions(self, prog):
        top = [stmt for stmt in prog.stmts if isinstance(stmt, FnDef)]
        methods = [method for stmt in prog.stmts if isinstance(stmt, ClassDef)
                   for method in stmt.methods]
        module_functions = []
        for stmt in prog.stmts:
            if isinstance(stmt, ModuleInit):
                module_functions.extend(self._nested_functions(stmt.body))
            elif not isinstance(stmt, (FnDef, ClassDef)):
                module_functions.extend(self._nested_functions([stmt]))

        def register(fn, parent):
            helper = f"lt_c_fn_{len(self.function_nodes)}"
            self.fn_helpers[id(fn)] = helper
            self.fn_parents[id(fn)] = parent
            self.function_nodes.append(fn)
            for child in self._nested_functions(fn.body):
                register(child, fn)

        for fn in top + methods + module_functions:
            register(fn, None)
        for index, method in enumerate(methods):
            self.method_helpers[id(method)] = f"lt_c_method_{index}"

    def _scope_globals(self, stmts):
        found = set()
        for stmt in stmts:
            if isinstance(stmt, GlobalStmt):
                found.update(stmt.names)
            elif isinstance(stmt, FnDef):
                continue
            elif isinstance(stmt, If):
                found.update(self._scope_globals(stmt.then_body))
                found.update(self._scope_globals(stmt.else_body or []))
            elif isinstance(stmt, (For, While)):
                found.update(self._scope_globals(stmt.body))
            elif isinstance(stmt, Try):
                found.update(self._scope_globals(stmt.body))
                found.update(self._scope_globals(stmt.handler))
        return found

    def _scope_nonlocals(self, stmts):
        found = set()
        for stmt in stmts:
            if isinstance(stmt, NonlocalStmt):
                found.update(stmt.names)
            elif isinstance(stmt, FnDef):
                continue
            elif isinstance(stmt, If):
                found.update(self._scope_nonlocals(stmt.then_body))
                found.update(self._scope_nonlocals(stmt.else_body or []))
            elif isinstance(stmt, (For, While)):
                found.update(self._scope_nonlocals(stmt.body))
            elif isinstance(stmt, Try):
                found.update(self._scope_nonlocals(stmt.body))
                found.update(self._scope_nonlocals(stmt.handler))
        return found

    def _collect(self, stmts, names):
        for stmt in stmts:
            if isinstance(stmt, Assign):
                names.add(stmt.name)
            elif isinstance(stmt, For):
                names.add(stmt.var)
                self._collect(stmt.body, names)
            elif isinstance(stmt, If):
                self._collect(stmt.then_body, names)
                self._collect(stmt.else_body or [], names)
            elif isinstance(stmt, While):
                self._collect(stmt.body, names)
            elif isinstance(stmt, Try):
                names.add(stmt.var)
                self._collect(stmt.body, names)
                self._collect(stmt.handler, names)
            elif isinstance(stmt, FnDef):
                names.add(stmt.name)

    def _fn_context(self, fn):
        key = id(fn)
        if key in self.function_contexts:
            return self.function_contexts[key]
        parent_fn = self.fn_parents.get(key)
        parent = self._fn_context(parent_fn) if parent_fn is not None else None
        locals_ = set(parameter_names(fn.params))
        self._collect(fn.body, locals_)
        globals_ = self._scope_globals(fn.body)
        nonlocals = self._scope_nonlocals(fn.body)
        locals_.difference_update(globals_ | nonlocals)
        context = {"fn": fn, "locals": locals_, "globals": globals_,
                   "nonlocals": nonlocals, "parent": parent}
        self.function_contexts[key] = context
        return context

    def _is_global(self, name):
        scope = self.scope
        while scope is not None:
            if name in scope["globals"]:
                return True
            if name in scope["locals"]:
                return False
            scope = scope["parent"]
        return False

    def _read_name(self, name):
        scope = self.scope
        while scope is not None:
            if name in scope["globals"]:
                return ident(name)
            if name in scope["locals"] or name in scope["nonlocals"]:
                return f"lt_env_get(lt_c_env, {c_string(name)})"
            scope = scope["parent"]
        return ident(name)

    def _assign(self, name, value):
        if self.scope is not None and name in self.scope["nonlocals"]:
            self.w(f"lt_env_set_enclosing(lt_c_env, {c_string(name)}, {value});")
        elif self.scope is not None and not self._is_global(name):
            self.w(f"lt_env_set_local(lt_c_env, {c_string(name)}, {value});")
        else:
            self.w(f"{ident(name)} = {value};")

    def _source(self, node):
        return getattr(node, "source_path", None) or self.source_path

    @staticmethod
    def _module_init(module):
        return "lt_c_modinit_" + module.init_name.encode("utf-8").hex()

    def _location(self, node):
        self.w(f"lt_set_location({c_string(self._source(node))}, {int(node.line)}, "
               f"{c_string(self.current_function)});")

    def _temp(self, expr):
        name = self._fresh("lt_c_value")
        self.w(f"LtValue {name} = {expr};")
        return name

    def _arg_array(self, args, start=0):
        """Evaluate actuals in source order and build a sequenced LtArg vector."""
        values = []
        for arg in args[start:]:
            if isinstance(arg, (NamedArg, StarArg, StarStarArg)):
                value = self.expr(arg.value)
                if isinstance(arg, NamedArg):
                    values.append(f"lt_named({c_string(arg.name)}, {value})")
                elif isinstance(arg, StarArg):
                    values.append(f"lt_star({value})")
                else:
                    values.append(f"lt_starstar({value})")
            else:
                values.append(f"lt_pos({self.expr(arg)})")
        if not values:
            return "NULL", 0
        name = self._fresh("lt_c_args")
        self.w(f"LtArg {name}[{len(values)}] = {{{', '.join(values)}}};")
        return name, len(values)

    def expr(self, node):
        if isinstance(node, Num):
            return self._temp(c_num(node.value))
        if isinstance(node, Str):
            return self._temp(f"lt_str({c_string(node.value)})")
        if isinstance(node, Bool):
            return self._temp(f"lt_bool({1 if node.value else 0})")
        if isinstance(node, Nil):
            return self._temp("lt_nil()")
        if isinstance(node, Name):
            return self._temp(self._read_name(node.id))
        if isinstance(node, ClassRef):
            return self._temp(ident(node.name))
        if isinstance(node, List):
            vals = [self.expr(item) for item in node.elts]
            if not vals:
                return self._temp("lt_list(NULL, 0)")
            arr = self._fresh("lt_c_items")
            self.w(f"LtValue {arr}[{len(vals)}] = {{{', '.join(vals)}}};")
            return self._temp(f"lt_list({arr}, {len(vals)})")
        if isinstance(node, Map):
            vals = [self.expr(value) for _, value in node.pairs]
            if not vals:
                return self._temp("lt_map(NULL, NULL, 0)")
            keys = self._fresh("lt_c_keys")
            values = self._fresh("lt_c_values")
            key_values = ", ".join(c_string(key) for key, _ in node.pairs)
            self.w(f"const char *{keys}[{len(vals)}] = {{{key_values}}};")
            self.w(f"LtValue {values}[{len(vals)}] = {{{', '.join(vals)}}};")
            return self._temp(f"lt_map({keys}, {values}, {len(vals)})")
        if isinstance(node, BinOp):
            left = self.expr(node.left)
            if node.op in ("and", "or"):
                result = self._fresh("lt_c_value")
                self.w(f"LtValue {result} = {left};")
                condition = f"lt_truthy({left})"
                self.w(f"if ({condition if node.op == 'and' else '!' + condition}) {{")
                self.ind += 1
                right = self.expr(node.right)
                self.w(f"{result} = {right};")
                self.ind -= 1
                self.w("}")
                return result
            right = self.expr(node.right)
            funcs = {"+": "lt_add", "-": "lt_sub", "*": "lt_mul",
                     "/": "lt_div", "%": "lt_mod", "**": "lt_pow",
                     "==": "lt_eq", "!=": "lt_ne", "<": "lt_lt",
                     "<=": "lt_lte", ">": "lt_gt", ">=": "lt_gte"}
            if node.op not in funcs:
                raise CBackendError(f"unsupported C binary operator {node.op!r}")
            return self._temp(f"{funcs[node.op]}({left}, {right})")
        if isinstance(node, UnOp):
            value = self.expr(node.operand)
            if node.op == "-":
                return self._temp(f"lt_neg({value})")
            return self._temp(f"lt_bool(!lt_truthy({value}))")
        if isinstance(node, Call):
            return self.call(node)
        if isinstance(node, SuperCall):
            if not node.args:
                raise CBackendError("invalid SuperCall without receiver")
            receiver = self.expr(node.args[0])
            args, count = self._arg_array(node.args, 1)
            owner = ident(node.owner.name) if node.owner is not None else "lt_nil()"
            return self._temp(f"lt_super_call({receiver}, {owner}, "
                              f"{c_string(node.method)}, {args}, {count})")
        if isinstance(node, PyImport):
            module = self.expr(node.module_expr)
            return self._temp(f"lt_pymod({module})")
        if isinstance(node, JavaImport):
            class_name = self.expr(node.class_expr)
            return self._temp(f"lt_jclass({class_name})")
        if isinstance(node, Dot):
            raise CBackendError("unexpected unlowered Dot in C backend")
        if isinstance(node, Subscript):
            obj = self.expr(node.obj)
            index = self.expr(node.index)
            return self._temp(f"lt_index({obj}, {index})")
        if isinstance(node, Super):
            raise CBackendError("unexpected standalone super in C backend")
        raise CBackendError(f"unsupported C expression {type(node).__name__}")

    def call(self, node):
        name = node.func.id if isinstance(node.func, Name) else None
        builtin_names = {"__say", "len", "range", "str", "int", "push",
                         "keys", "__wgetattr", "__wcall", "__index",
                         "__wsetattr", "__wsetindex"}
        if name in builtin_names:
            if name == "__wcall":
                if len(node.args) < 2:
                    raise CBackendError("malformed __wcall")
                receiver = self.expr(node.args[0])
                attr = node.args[1]
                if not isinstance(attr, Str):
                    raise CBackendError("__wcall requires a literal method name")
                args, count = self._arg_array(node.args, 2)
                return self._temp(f"lt_wcall({receiver}, {c_string(attr.value)}, "
                                  f"{args}, {count})")
            if name == "__wgetattr":
                if len(node.args) != 2 or not isinstance(node.args[1], Str):
                    raise CBackendError("__wgetattr requires a literal attribute name")
                obj = self.expr(node.args[0])
                return self._temp(f"lt_wgetattr({obj}, {c_string(node.args[1].value)})")
            if name == "__wsetattr":
                if len(node.args) != 3 or not isinstance(node.args[1], Str):
                    raise CBackendError("__wsetattr requires a literal attribute name")
                obj = self.expr(node.args[0])
                value = self.expr(node.args[2])
                return self._temp(f"(lt_wsetattr({obj}, {c_string(node.args[1].value)}, "
                                  f"{value}), lt_nil())")
            if name == "__wsetindex":
                if len(node.args) != 3:
                    raise CBackendError("malformed __wsetindex")
                obj = self.expr(node.args[0])
                index = self.expr(node.args[1])
                value = self.expr(node.args[2])
                return self._temp(f"(lt_wsetindex({obj}, {index}, {value}), lt_nil())")
            values = [self.expr(arg) for arg in node.args]
            if name == "__say":
                func = "lt_say"
            elif name == "len":
                func = "lt_len"
            elif name == "str":
                func = "lt_str_of"
            elif name == "int":
                func = "lt_to_int"
            elif name == "push":
                func = "lt_push"
            elif name == "keys":
                func = "lt_keys"
            elif name == "range":
                func = "lt_range1" if len(values) == 1 else "lt_range2"
            else:
                func = None
            if func:
                return self._temp(f"{func}({', '.join(values)})")
            # __index has an ABI alias with the builtin's exact two-argument form.
            if name == "__index":
                return self._temp(f"lt_index({', '.join(values)})")
        callee = self.expr(node.func)
        args, count = self._arg_array(node.args)
        return self._temp(f"lt_call({callee}, {args}, {count})")

    def stmt(self, stmt):
        self._location(stmt)
        if isinstance(stmt, (GlobalStmt, NonlocalStmt)):
            return
        if isinstance(stmt, FnDef):
            env = "lt_c_env" if self.scope is not None else "NULL"
            value = f"lt_function(&{self.fn_helpers[id(stmt)]}_spec, {env})"
            self._assign(stmt.name, value)
        elif isinstance(stmt, Assign):
            value = self.expr(stmt.value)
            self._assign(stmt.name, value)
        elif isinstance(stmt, ExprStmt):
            value = self.expr(stmt.expr)
            self.w(f"(void){value};")
        elif isinstance(stmt, If):
            cond = self.expr(stmt.cond)
            self.w(f"if (lt_truthy({cond})) {{")
            self.ind += 1
            self.stmtlist(stmt.then_body)
            self.ind -= 1
            if stmt.else_body:
                self.w("} else {")
                self.ind += 1
                self.stmtlist(stmt.else_body)
                self.ind -= 1
            self.w("}")
        elif isinstance(stmt, While):
            self.w("for (;;) {")
            self.ind += 1
            self._location(stmt)
            cond = self.expr(stmt.cond)
            self.w(f"if (!lt_truthy({cond})) break;")
            self.loop_handler_bases.append(len(self.active_handlers))
            self.stmtlist(stmt.body)
            self.loop_handler_bases.pop()
            self.ind -= 1
            self.w("}")
        elif isinstance(stmt, For):
            iterable = self.expr(stmt.iter)
            iterator = self._fresh("lt_c_iterator")
            item = self._fresh("lt_c_item")
            self.w(f"LtIterator {iterator} = lt_iter({iterable});")
            self.w("for (;;) {")
            self.ind += 1
            self.w(f"LtValue {item};")
            self.w(f"if (!lt_iter_next(&{iterator}, &{item})) break;")
            self._assign(stmt.var, item)
            self.loop_handler_bases.append(len(self.active_handlers))
            self.stmtlist(stmt.body)
            self.loop_handler_bases.pop()
            self.ind -= 1
            self.w("}")
        elif isinstance(stmt, Return):
            value = self.expr(stmt.value) if stmt.value is not None else "lt_nil()"
            for handler in reversed(self.active_handlers):
                self.w(f"lt_handler_pop(&{handler});")
            self.w(f"return {value};")
        elif isinstance(stmt, Break):
            base = self.loop_handler_bases[-1] if self.loop_handler_bases else 0
            for handler in reversed(self.active_handlers[base:]):
                self.w(f"lt_handler_pop(&{handler});")
            self.w("break;")
        elif isinstance(stmt, Continue):
            base = self.loop_handler_bases[-1] if self.loop_handler_bases else 0
            for handler in reversed(self.active_handlers[base:]):
                self.w(f"lt_handler_pop(&{handler});")
            self.w("continue;")
        elif isinstance(stmt, Throw):
            value = self.expr(stmt.value)
            self.w(f"lt_throw(lt_str_of({value}));")
        elif isinstance(stmt, Try):
            handler = self._fresh("lt_c_handler")
            self.w("{")
            self.ind += 1
            self.w(f"LtHandler {handler};")
            self.w(f"if (LT_TRY({handler}) == 0) {{")
            self.ind += 1
            self.active_handlers.append(handler)
            self.stmtlist(stmt.body)
            self.active_handlers.pop()
            self.w(f"lt_handler_pop(&{handler});")
            self.ind -= 1
            self.w("} else {")
            self.ind += 1
            self.w(f"const char *lt_c_message = lt_handler_message(&{handler});")
            self.w(f"lt_handler_pop(&{handler});")
            self._assign(stmt.var, "lt_str(lt_c_message ? lt_c_message : \"\")")
            self.stmtlist(stmt.handler)
            self.ind -= 1
            self.w("}")
            self.ind -= 1
            self.w("}")
        elif isinstance(stmt, (ImportStmt, ModuleInit, ClassDef)):
            raise CBackendError(f"unexpected {type(stmt).__name__} in C statement body")
        else:
            raise CBackendError(f"unsupported C statement {type(stmt).__name__}")

    def stmtlist(self, stmts):
        for stmt in stmts:
            self.stmt(stmt)

    def _spec_parts(self, fn, method=False):
        params = fn.params[1:] if method else fn.params
        fixed = fixed_parameter_names(params)
        bound = [parameter_name(p) for p in bound_parameter_order(params)]
        required_kw = required_keyword_only_names(params)
        return params, fixed, bound, required_kw

    def _emit_array(self, name, strings):
        if strings:
            self.w(f"static const char *const {name}[{len(strings)}] = "
                   f"{{{', '.join(c_string(value) for value in strings)}}};")

    def _emit_function_metadata(self, fn, method=False):
        helper = self.fn_helpers[id(fn)]
        params, fixed, bound, required_kw = self._spec_parts(fn, method)
        self._emit_array(f"{helper}_params", fixed)
        self._emit_array(f"{helper}_required_kw", required_kw)
        body = self.method_helpers[id(fn)] if method else helper
        spec = f"{helper}_spec"
        self.w(f"static const LtFunctionSpec {spec} = {{")
        self.ind += 1
        self.w(f"{c_string(fn.source_name)}, {body},")
        self.w(f"{helper}_params" if fixed else "NULL")
        self.w(f", {len(fixed)}, {required_parameter_count(params)}, "
               f"{positional_parameter_count(params)},")
        self.w(f"{helper}_required_kw" if required_kw else "NULL")
        self.w(f", {len(required_kw)}, {_nullable_c_string(rest_parameter_name(params))},")
        self.w(f"{_nullable_c_string(extra_parameter_name(params))}, "
               f"{c_string(self._source(fn))}, {int(fn.line)}")
        self.ind -= 1
        self.w("};")

    def _emit_function_body(self, fn):
        helper = self.fn_helpers[id(fn)]
        prior_scope, prior_fn = self.scope, self.current_function
        self.scope = self._fn_context(fn)
        self.current_function = fn.source_name
        self.w(f"static LtValue {helper}(LtEnv *lt_c_closure, size_t lt_c_argc, "
               "const LtValue *lt_c_argv) {")
        self.ind += 1
        self.w("(void)lt_c_argc;")
        self.w("(void)lt_c_argv;")
        names = sorted(self.scope["locals"])
        if names:
            arr = f"{helper}_locals"
            self.w(f"const char *const {arr}[{len(names)}] = "
                   f"{{{', '.join(c_string(name) for name in names)}}};")
            self.w(f"LtEnv *lt_c_env = lt_env_new(lt_c_closure, {arr}, {len(names)});")
        else:
            self.w("LtEnv *lt_c_env = lt_env_new(lt_c_closure, NULL, 0);")
        for index, param in enumerate(bound_parameter_order(fn.params)):
            name = parameter_name(param)
            self.w(f"lt_env_set_local(lt_c_env, {c_string(name)}, "
                   f"lt_c_argc > {index} ? lt_c_argv[{index}] : lt_nil());")
        for param in fn.params:
            if parameter_has_default(param):
                value = self.expr(Name(param.name, line=param.line, col=param.col))
                self.w(f"if (lt_is_missing({value})) {{")
                self.ind += 1
                default = self.expr(param.default)
                self.w(f"lt_env_set_local(lt_c_env, {c_string(param.name)}, {default});")
                self.ind -= 1
                self.w("}")
        self.stmtlist(fn.body)
        self.w("return lt_nil();")
        self.ind -= 1
        self.w("}")
        self.scope, self.current_function = prior_scope, prior_fn

    def _emit_method_wrapper(self, fn):
        helper = self.method_helpers[id(fn)]
        impl = self.fn_helpers[id(fn)]
        self.w(f"static LtValue {helper}(LtEnv *lt_c_env, size_t lt_c_argc, "
               "const LtValue *lt_c_argv) {")
        self.ind += 1
        self.w("LtValue lt_c_method_args[lt_c_argc + 1];")
        self.w('lt_c_method_args[0] = lt_env_get(lt_c_env, "self");')
        self.w("for (size_t lt_c_i = 0; lt_c_i < lt_c_argc; ++lt_c_i) "
               "lt_c_method_args[lt_c_i + 1] = lt_c_argv[lt_c_i];")
        self.w(f"return {impl}(lt_c_env, lt_c_argc + 1, lt_c_method_args);")
        self.ind -= 1
        self.w("}")

    def _collect_globals(self, prog, modules):
        result = []
        if modules:
            for module in modules:
                for name in module.globals:
                    if name not in result:
                        result.append(name)
        else:
            self._collect_top(prog.stmts, result)
        for stmt in prog.stmts:
            if isinstance(stmt, FnDef) and stmt.name not in result:
                result.append(stmt.name)
            elif isinstance(stmt, ClassDef) and stmt.name not in result:
                result.append(stmt.name)
        return result

    def _collect_top(self, stmts, out):
        def add(name):
            if name not in out:
                out.append(name)
        for stmt in stmts:
            if isinstance(stmt, FnDef):
                add(stmt.name)
            elif isinstance(stmt, Assign):
                add(stmt.name)
            elif isinstance(stmt, For):
                add(stmt.var)
                self._collect_top(stmt.body, out)
            elif isinstance(stmt, If):
                self._collect_top(stmt.then_body, out)
                self._collect_top(stmt.else_body or [], out)
            elif isinstance(stmt, While):
                self._collect_top(stmt.body, out)
            elif isinstance(stmt, Try):
                add(stmt.var)
                self._collect_top(stmt.body, out)
                self._collect_top(stmt.handler, out)

    def _emit_class_metadata(self, classes):
        for cd in classes:
            specs = []
            for method in cd.methods:
                self._emit_function_metadata(method, method=True)
                specs.append(f"{self.fn_helpers[id(method)]}_spec")
            if specs:
                self.w(f"static const LtFunctionSpec lt_c_class_{len(self.class_specs)}_methods[] = "
                       f"{{{', '.join(specs)}}};")
            self.class_specs[id(cd)] = (len(self.class_specs), specs)

    def _emit_prologue(self, prog, modules, classes):
        self.w("#include <math.h>")
        self.w("#include <stdint.h>")
        self.w("#include <stddef.h>")
        self.w("#include <stdlib.h>")
        self.w("#include \"runtime/lt_runtime.h\"")
        self.w("")
        self.globals = self._collect_globals(prog, modules)
        for name in self.globals:
            self.w(f"static LtValue {ident(name)};")
        for module in modules:
            self.w(f"static int {ident(module.state_name)};")
            self.module_by_id[module.module_id] = module
        for fn in self.function_nodes:
            self.w(f"static LtValue {self.fn_helpers[id(fn)]}(LtEnv *, size_t, const LtValue *);")
        for method in self.method_helpers:
            fn = next(fn for fn in self.function_nodes if id(fn) == method)
            self.w(f"static LtValue {self.method_helpers[method]}(LtEnv *, size_t, const LtValue *);")
        for module in modules:
            self.w(f"static void {self._module_init(module)}(void);")
        self.w("")
        for fn in self.function_nodes:
            self._emit_function_metadata(fn, method=id(fn) in self.method_helpers)
        for cd in classes:
            specs = [f"{self.fn_helpers[id(method)]}_spec" for method in cd.methods]
            index, _ = self.class_specs[id(cd)]
            if specs:
                self.w(f"static const LtFunctionSpec lt_c_class_{index}_methods[] = "
                       f"{{{', '.join(specs)}}};")
        self.w("")

    def _emit_class_values(self, classes):
        for cd in classes:
            self.w(f"lt_set_location({c_string(self._source(cd))}, {int(cd.line)}, "
                   f"{c_string('class ' + cd.source_name)});")
            index, specs = self.class_specs[id(cd)]
            parent = ident(cd.parent.name) if cd.parent is not None else "lt_nil()"
            arr = f"lt_c_class_{index}_methods" if specs else "NULL"
            self.w(f"{ident(cd.name)} = lt_class({c_string(cd.source_name)}, {parent}, "
                   f"{arr}, {len(specs)});")

    def _emit_module_init(self, module):
        prior_fn, prior_scope = self.current_function, self.scope
        self.current_function, self.scope = module.init_name, None
        state = ident(module.state_name)
        init_name = self._module_init(module)
        self.w(f"static void {init_name}(void) {{")
        self.ind += 1
        self.w(f"lt_set_location({c_string(self._source(module))}, {int(module.line)}, "
               f"{c_string(module.init_name)});")
        self.w(f"if ({state} == 2) return;")
        self.w(f"if ({state} == 1) lt_throw(lt_str(\"cyclic module initialization\"));")
        self.w(f"{state} = 1;")
        for dep in module.deps:
            self.w(f"{self._module_init(self.module_by_id[dep])}();")
        self.stmtlist(module.body)
        self.w(f"{state} = 2;")
        self.ind -= 1
        self.w("}")
        self.scope, self.current_function = prior_scope, prior_fn

    def generate(self, prog):
        self._prepare_functions(prog)
        modules = [stmt for stmt in prog.stmts if isinstance(stmt, ModuleInit)]
        classes = class_order([stmt for stmt in prog.stmts if isinstance(stmt, ClassDef)])
        self._collect_globals(prog, modules)
        for cd in classes:
            self.class_specs[id(cd)] = (len(self.class_specs),
                                        [f"{self.fn_helpers[id(method)]}_spec"
                                         for method in cd.methods])
        self._emit_prologue(prog, modules, classes)
        self.w("int main(int argc, char **argv) {")
        self.ind += 1
        self.w("(void)argc;")
        self.w("lt_runtime_init(argv[0]);")
        self.w("LtHandler lt_c_top_handler;")
        self.w("if (LT_TRY(lt_c_top_handler) == 0) {")
        self.ind += 1
        self.w(f"lt_set_location({c_string(self.source_path)}, 0, \"main\");")
        for fn in [stmt for stmt in prog.stmts if isinstance(stmt, FnDef)]:
            self.w(f"lt_set_location({c_string(self._source(fn))}, {int(fn.line)}, "
                   f"{c_string(fn.source_name)});")
            self.w(f"{ident(fn.name)} = lt_function(&{self.fn_helpers[id(fn)]}_spec, NULL);")
        self._emit_class_values(classes)
        if modules:
            root = next((module for module in modules if module.root), modules[-1])
            self.w(f"{self._module_init(root)}();")
        else:
            self.scope = None
            rest = [stmt for stmt in prog.stmts if not isinstance(stmt, (FnDef, ClassDef))]
            self.stmtlist(rest)
        self.w("lt_handler_pop(&lt_c_top_handler);")
        self.ind -= 1
        self.w("} else {")
        self.ind += 1
        self.w("lt_handler_pop(&lt_c_top_handler);")
        self.w("lt_report_uncaught();")
        self.w("return 1;")
        self.ind -= 1
        self.w("}")
        self.w("lt_runtime_shutdown();")
        self.w("return 0;")
        self.ind -= 1
        self.w("}")
        for fn in self.function_nodes:
            self.w("")
            self._emit_function_body(fn)
        for method in self.method_helpers:
            fn = next(fn for fn in self.function_nodes if id(fn) == method)
            self.w("")
            self._emit_method_wrapper(fn)
        for module in modules:
            self.w("")
            self._emit_module_init(module)
        return "\n".join(self.out) + "\n"


def generate(prog, source_path="<src>"):
    """Generate C11 source from a semantically analyzed ``nodes.Program``."""
    return Gen(source_path=source_path).generate(prog)
