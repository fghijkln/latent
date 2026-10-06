"""Latent -> Java backend. All values are Object; see runtime/LtRt.java."""
import math
from nodes import *

JAVA_KW = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch",
    "char", "class", "const", "continue", "default", "do", "double",
    "else", "enum", "extends", "final", "finally", "float", "for",
    "goto", "if", "implements", "import", "instanceof", "int",
    "interface", "long", "native", "new", "package", "private",
    "protected", "public", "return", "short", "static", "strictfp",
    "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while", "true", "false",
    "null", "var",
}

BUILTIN_JAVA = {
    "__say": "LtRt.say", "len": "LtRt.len", "range": "LtRt.range",
    "str": "LtRt.strOf", "int": "LtRt.toInt", "push": "LtRt.push",
    "keys": "LtRt.keys", "__wgetattr": None, "__wcall": None,  # special-cased
    "__index": "LtRt.index", "__wsetattr": "LtRt.wsetattr",
    "__wsetindex": "LtRt.wsetindex",
}


def ident(name):
    return name + "_" if name in JAVA_KW else name


def cls_name(stem):
    s = "".join(c if (c.isalnum() or c == "_") else "_" for c in stem)
    if not s or s[0].isdigit():
        s = "_" + s
    if s in JAVA_KW or s == "LtRt":
        s += "_"
    return s[0].upper() + s[1:] if s else "_"


def java_num(v):
    if math.isnan(v):
        return "Double.NaN"
    if math.isinf(v):
        return "Double.POSITIVE_INFINITY" if v > 0 else "Double.NEGATIVE_INFINITY"
    if v == math.floor(v) and abs(v) < 1e16 and v == v:
        # integral: emit with .0 so javac sees a double literal
        return str(int(v)) + ".0"
    r = repr(v)
    return r


def java_str(s):
    out = []
    for c in s:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif c == "\r":
            out.append("\\r")
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append("\\u%04x" % ord(c))
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


class Gen:
    def __init__(self, cls, source_path="<src>"):
        self.cls = cls
        self.out = []
        self.ind = 0
        self.tmp = 0
        self.source_path = source_path
        self.source_map = {}
        self.fn_helpers = {}
        self.fn_parents = {}
        self.function_nodes = []
        self.scope = None
        self.used_java_names = set()

    def _fresh_java(self, base):
        name = base
        while name in self.used_java_names or name in JAVA_KW:
            name = "_" + name
        self.used_java_names.add(name)
        return name

    def _nested_functions(self, stmts):
        for s in stmts:
            if isinstance(s, FnDef):
                yield s
            elif isinstance(s, If):
                yield from self._nested_functions(s.then_body)
                yield from self._nested_functions(s.else_body or [])
            elif isinstance(s, (For, While)):
                yield from self._nested_functions(s.body)
            elif isinstance(s, Try):
                yield from self._nested_functions(s.body)
                yield from self._nested_functions(s.handler)

    def _prepare_functions(self, prog):
        for node in self._walk_nodes(prog):
            if isinstance(node, (Name, FnDef, ClassDef, Assign, For, Try)):
                if isinstance(node, Name):
                    self.used_java_names.add(node.id)
                elif isinstance(node, (FnDef, ClassDef, Assign)):
                    self.used_java_names.add(node.name)
                elif isinstance(node, (For, Try)):
                    self.used_java_names.add(node.var)
        top = [s for s in prog.stmts if isinstance(s, FnDef)]
        methods = [m for s in prog.stmts if isinstance(s, ClassDef)
                   for m in s.methods]

        def register(fn, parent):
            self.fn_helpers[id(fn)] = self._fresh_java("_lt_fn_body_" +
                                                       str(len(self.function_nodes)))
            self.fn_parents[id(fn)] = parent
            self.function_nodes.append(fn)
            for child in self._nested_functions(fn.body):
                register(child, fn)

        for fn in top + methods:
            register(fn, None)

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

    def w(self, s="", source_line=None, source_path=None):
        generated_line = len(self.out) + 1
        self.out.append("    " * self.ind + s)
        if source_line:
            self.source_map[generated_line] = (source_path or self.source_path,
                                               source_line)

    def string_array(self, values):
        return "new String[]{" + ", ".join(java_str(v) for v in values) + "}"

    def generate(self, prog):
        self._prepare_functions(prog)
        modules = [s for s in prog.stmts if isinstance(s, ModuleInit)]
        fns = [s for s in prog.stmts if isinstance(s, FnDef)]
        clss = class_order([s for s in prog.stmts if isinstance(s, ClassDef)])
        rest = [s for s in prog.stmts
                if not isinstance(s, (FnDef, ClassDef, ModuleInit))]
        gnames = []
        if modules:
            for module in modules:
                for name in module.globals:
                    if name not in gnames:
                        gnames.append(name)
        else:
            self._collect_top(rest, gnames)
        for fn in fns:
            if fn.name not in gnames:
                gnames.append(fn.name)
        self.w(f"public class {self.cls} " + "{")
        self.ind += 1
        top_functions = {fn.name: fn for fn in fns}
        for g in gnames:
            fn = top_functions.get(g)
            if fn is None:
                self.w(f"static Object {ident(g)};")
            else:
                self.w(f"static Object {ident(g)} = LtRt.function(" +
                       f"{self.string_array(fn.params)}, "
                       f"{java_str(fn.source_name)}, " +
                       f"args -> {self.fn_helpers[id(fn)]}(null, args));")
        for module in modules:
            self.w(f"private static int {module.state_name};")
        if gnames:
            self.w("")
        for cd in clss:
            self.classfield(cd)
        self.w("public static void main(String[] args) {")
        self.ind += 1
        self.w("try {")
        self.ind += 1
        if modules:
            root = next(m for m in modules if m.root)
            self.w(f"{root.init_name}();")
        else:
            for s in rest:
                self.stmt(s)
        self.ind -= 1
        self.w("} catch (Exception _lt_error) {")
        self.ind += 1
        self.w("_lt_reportError(_lt_error);")
        self.w("LtRt.shutdown();")
        self.w("System.exit(1);")
        self.w("return;")
        self.ind -= 1
        self.w("} finally {")
        self.ind += 1
        self.w("LtRt.shutdown();")
        self.ind -= 1
        self.w("}")
        self.ind -= 1
        self.w("}")
        for cd in clss:
            for m in cd.methods:
                self.w("")
                self.methoddef(cd, m)
        for fn in self.function_nodes:
            self.w("")
            self.fndef(fn)
        for module in modules:
            self.w("")
            self.module_init(module, modules)
        self.w("")
        self.w("private static final String _LT_SOURCE_FILE = " +
               java_str(self.source_path) + ";")
        self.w("private static int _lt_sourceLine(int generatedLine) {")
        self.ind += 1
        self.w("switch (generatedLine) {")
        self.ind += 1
        for generated_line, source_loc in sorted(self.source_map.items()):
            self.w(f"case {generated_line}: return {source_loc[1]};")
        self.w("default: return 0;")
        self.ind -= 1
        self.w("}")
        self.ind -= 1
        self.w("}")
        self.w("private static String _lt_sourceFile(int generatedLine) {")
        self.ind += 1
        self.w("switch (generatedLine) {")
        self.ind += 1
        for generated_line, source_loc in sorted(self.source_map.items()):
            self.w(f"case {generated_line}: return {java_str(source_loc[0])};")
        self.w(f"default: return {java_str(self.source_path)};")
        self.ind -= 1
        self.w("}")
        self.ind -= 1
        self.w("}")
        self.w("private static void _lt_reportError(Exception error) {")
        self.ind += 1
        self.w("try {")
        self.ind += 1
        self.w("int mappedFrames = 0;")
        self.w("StackTraceElement[] frames = error.getStackTrace();")
        self.w("for (int i = frames.length - 1; i >= 0; i--) {")
        self.ind += 1
        self.w("StackTraceElement frame = frames[i];")
        self.w(f"if (!frame.getClassName().equals({java_str(self.cls)})) continue;")
        self.w("int sourceLine = _lt_sourceLine(frame.getLineNumber());")
        self.w("if (sourceLine <= 0) continue;")
        self.w("if (mappedFrames == 0) {")
        self.ind += 1
        self.w("String detail = error.getMessage();")
        self.w('String errorName = error instanceof LtRt.ArgumentError ? '
               '"ArgumentError" : error.getClass().getSimpleName();')
        self.w("System.err.println(\"Latent runtime error: \" + "
               "errorName + "
               "(detail == null || detail.isEmpty() ? \"\" : \": \" + detail));")
        self.w("System.err.println(\"Latent traceback (most recent call last):\");")
        self.ind -= 1
        self.w("}")
        self.w("System.err.println(\"  at \" + _lt_sourceFile(frame.getLineNumber()) + \":\" + "
               "sourceLine + \" in \" + frame.getMethodName());")
        self.w("mappedFrames++;")
        self.ind -= 1
        self.w("}")
        self.w("if (mappedFrames == 0) error.printStackTrace(System.err);")
        self.ind -= 1
        self.w("} catch (Throwable diagnosticError) {")
        self.ind += 1
        self.w("error.printStackTrace(System.err);")
        self.ind -= 1
        self.w("}")
        self.ind -= 1
        self.w("}")
        self.ind -= 1
        self.w("}")
        return "\n".join(self.out) + "\n"

    def module_init(self, module, modules):
        self.w(f"private static void {module.init_name}() throws Exception " + "{")
        self.ind += 1
        self.w(f"if ({module.state_name} == 2) return;")
        self.w(f"if ({module.state_name} == 1) "
               "throw new RuntimeException(\"cyclic module initialization\");")
        self.w(f"{module.state_name} = 1;")
        self.w("try {")
        self.ind += 1
        by_id = {m.module_id: m for m in modules}
        for dep_id in module.deps:
            self.w(f"{by_id[dep_id].init_name}();")
        for stmt in module.body:
            self.stmt(stmt)
        self.w(f"{module.state_name} = 2;")
        self.ind -= 1
        self.w("} catch (Exception _lt_module_error) {")
        self.ind += 1
        self.w(f"{module.state_name} = 0;")
        self.w("throw _lt_module_error;")
        self.ind -= 1
        self.w("}")
        self.ind -= 1
        self.w("}")

    def classfield(self, cd):
        names = ", ".join(java_str(m.name) for m in cd.methods)
        lambdas = ", ".join(
            f"(s, a) -> {cd.name}_{m.name}(s, a)" for m in cd.methods)
        arities = ", ".join(str(max(0, len(m.params) - 1))
                             for m in cd.methods)
        parameters = ", ".join(self.string_array(m.params[1:])
                                for m in cd.methods)
        parent = ident(cd.parent.name) if cd.parent else "null"
        self.w(f"static LtRt.LtClass {ident(cd.name)} = LtRt.makeClass(")
        self.ind += 1
        self.w(f"{java_str(cd.source_name)},")
        self.w(f"{parent},")
        self.w(f"new String[]{{{names}}},")
        self.w(f"new LtRt.LtMethod[]{{{lambdas}}},")
        self.w(f"new int[]{{{arities}}},")
        self.w(f"new String[][]{{{parameters}}});")
        self.ind -= 1

    def methoddef(self, cd, m):
        params = m.params
        restp = params[1:] if params else []
        self.w(f"static Object {cd.name}_{m.name}(Object self, Object[] args) " + "{",
               m.line, m.source_path)
        self.ind += 1
        self.w(f"if (args.length != {len(restp)})")
        self.ind += 1
        self.w(f'throw new RuntimeException("{m.name}() takes {len(restp)} '
               f'args, got " + args.length);')
        self.ind -= 1
        self.w(f"Object[] _all_args = new Object[{len(restp) + 1}];")
        self.w("_all_args[0] = self;")
        self.w("System.arraycopy(args, 0, _all_args, 1, args.length);")
        self.w(f"return {self.fn_helpers[id(m)]}(null, _all_args);")
        self.ind -= 1
        self.w("}")

    def fndef(self, fn):
        previous_scope = self.scope
        self.scope = self._fn_context(fn)
        helper = self.fn_helpers[id(fn)]
        self.w(f"static Object {helper}(LtRt.Env _closure, Object[] _args) " + "{",
               fn.line, fn.source_path)
        self.ind += 1
        names = sorted(self.scope["locals"])
        self.w("LtRt.Env _env = new LtRt.Env(_closure, new String[]{" +
               ", ".join(java_str(name) for name in names) + "});")
        for i, param in enumerate(fn.params):
            self.w(f"_env.setLocal({java_str(param)}, _args[{i}]);")
        for s in fn.body:
            self.stmt(s)
        if not self._always_returns(fn.body):
            self.w("return null;")
        self.ind -= 1
        self.w("}")
        self.scope = previous_scope

    def _scope_globals(self, stmts):
        out = set()
        for s in stmts:
            if isinstance(s, GlobalStmt):
                out.update(s.names)
            elif isinstance(s, FnDef):
                continue
            elif isinstance(s, If):
                out.update(self._scope_globals(s.then_body))
                out.update(self._scope_globals(s.else_body or []))
            elif isinstance(s, (For, While)):
                out.update(self._scope_globals(s.body))
            elif isinstance(s, Try):
                out.update(self._scope_globals(s.body))
                out.update(self._scope_globals(s.handler))
        return out

    def _scope_nonlocals(self, stmts):
        out = set()
        for s in stmts:
            if isinstance(s, NonlocalStmt):
                out.update(s.names)
            elif isinstance(s, FnDef):
                continue
            elif isinstance(s, If):
                out.update(self._scope_nonlocals(s.then_body))
                out.update(self._scope_nonlocals(s.else_body or []))
            elif isinstance(s, (For, While)):
                out.update(self._scope_nonlocals(s.body))
            elif isinstance(s, Try):
                out.update(self._scope_nonlocals(s.body))
                out.update(self._scope_nonlocals(s.handler))
        return out

    def _fn_context(self, fn):
        cached = getattr(self, "fn_contexts", {})
        if id(fn) in cached:
            return cached[id(fn)]
        parent_fn = self.fn_parents.get(id(fn))
        parent = self._fn_context(parent_fn) if parent_fn is not None else None
        names = set(fn.params)
        self._collect(fn.body, names)
        globals_ = self._scope_globals(fn.body)
        nonlocals = self._scope_nonlocals(fn.body)
        names.difference_update(globals_ | nonlocals)
        context = {"fn": fn, "locals": names, "globals": globals_,
                   "nonlocals": nonlocals,
                   "parent": parent}
        if not hasattr(self, "fn_contexts"):
            self.fn_contexts = {}
        self.fn_contexts[id(fn)] = context
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

    def _assign(self, name, value):
        if self.scope is not None and name in self.scope["nonlocals"]:
            return f"_env.setEnclosing({java_str(name)}, {value});"
        if self.scope is not None and not self._is_global(name):
            return f"_env.setLocal({java_str(name)}, {value});"
        return f"{ident(name)} = {value};"

    def _always_returns(self, stmts):
        if not stmts:
            return False
        last = stmts[-1]
        if isinstance(last, Return):
            return True
        if isinstance(last, If) and last.else_body:
            return (self._always_returns(last.then_body) and
                    self._always_returns(last.else_body))
        if isinstance(last, Try):
            return (self._always_returns(last.body) and
                    self._always_returns(last.handler))
        return False

    def _collect_top(self, stmts, out):
        """Ordered collection of top-level assigned names, descending into
        blocks (if/while/for/try). FnDef/ClassDef bodies are separate
        scopes and never appear here."""
        for s in stmts:
            if isinstance(s, Assign):
                if s.name not in out:
                    out.append(s.name)
            elif isinstance(s, For):
                if s.var not in out:
                    out.append(s.var)
                self._collect_top(s.body, out)
            elif isinstance(s, If):
                self._collect_top(s.then_body, out)
                if s.else_body:
                    self._collect_top(s.else_body, out)
            elif isinstance(s, While):
                self._collect_top(s.body, out)
            elif isinstance(s, Try):
                if s.var not in out:
                    out.append(s.var)
                self._collect_top(s.body, out)
                self._collect_top(s.handler, out)

    def _collect(self, stmts, out):
        for s in stmts:
            if isinstance(s, Assign):
                out.add(s.name)
            elif isinstance(s, For):
                out.add(s.var)
                self._collect(s.body, out)
            elif isinstance(s, If):
                self._collect(s.then_body, out)
                if s.else_body:
                    self._collect(s.else_body, out)
            elif isinstance(s, While):
                self._collect(s.body, out)
            elif isinstance(s, Try):
                out.add(s.var)
                self._collect(s.body, out)
                self._collect(s.handler, out)
            elif isinstance(s, FnDef):
                out.add(s.name)

    def stmt(self, s):
        if isinstance(s, (GlobalStmt, NonlocalStmt)):
            return
        if isinstance(s, FnDef):
            closure = f"LtRt.function({self.string_array(s.params)}, " \
                      f"{java_str(s.source_name)}, " \
                      f"args -> {self.fn_helpers[id(s)]}(_env, args))"
            self.w(self._assign(s.name, closure), s.line, s.source_path)
        elif isinstance(s, Assign):
            self.w(self._assign(s.name, self.expr(s.value)), s.line,
                   s.source_path)
        elif isinstance(s, ExprStmt):
            self.w(f"{self.expr(s.expr)};", s.line, s.source_path)
        elif isinstance(s, If):
            self.w(f"if (LtRt.truthy({self.expr(s.cond)})) " + "{", s.line,
                   s.source_path)
            self.suite(s.then_body)
            if s.else_body:
                self.w("} else {")
                self.suite(s.else_body)
            self.w("}")
        elif isinstance(s, While):
            self.w(f"while (LtRt.truthy({self.expr(s.cond)})) " + "{", s.line,
                   s.source_path)
            self.suite(s.body)
            self.w("}")
        elif isinstance(s, For):
            t = f"wv$it{self.tmp}"
            self.tmp += 1
            self.w(f"for (Object {t} : LtRt.iter({self.expr(s.iter)})) " + "{",
                   s.line, s.source_path)
            self.ind += 1
            self.w(self._assign(s.var, t))
            for x in s.body:
                self.stmt(x)
            self.ind -= 1
            self.w("}")
        elif isinstance(s, Return):
            self.w(f"return {self.expr(s.value)};" if s.value is not None
                   else "return null;", s.line, s.source_path)
        elif isinstance(s, Break):
            self.w("break;", s.line, s.source_path)
        elif isinstance(s, Continue):
            self.w("continue;", s.line, s.source_path)
        elif isinstance(s, Try):
            self.w("try {", s.line, s.source_path)
            self.suite(s.body)
            self.w("} catch (Exception _lt_e) {")
            self.ind += 1
            self.w("String _lt_m = _lt_e.getMessage();")
            self.w("if (_lt_m == null) _lt_m = _lt_e.toString();")
            self.w(self._assign(s.var, "_lt_m"))
            for x in s.handler:
                self.stmt(x)
            self.ind -= 1
            self.w("}")
        elif isinstance(s, Throw):
            self.w(f"throw new RuntimeException("
                   f"(String) LtRt.strOf({self.expr(s.value)}));", s.line,
                   s.source_path)
        else:
            raise Exception(f"java backend: unexpected {type(s).__name__}")

    def suite(self, stmts):
        self.ind += 1
        for s in stmts:
            self.stmt(s)
        self.ind -= 1

    def expr(self, e):
        if isinstance(e, Num):
            return java_num(e.value)
        if isinstance(e, Str):
            return java_str(e.value)
        if isinstance(e, Bool):
            return "true" if e.value else "false"
        if isinstance(e, Nil):
            return "null"
        if isinstance(e, Name):
            scope = self.scope
            while scope is not None:
                if e.id in scope["globals"]:
                    return ident(e.id)
                if e.id in scope["nonlocals"]:
                    return f"_env.get({java_str(e.id)})"
                if e.id in scope["locals"]:
                    return f"_env.get({java_str(e.id)})"
                scope = scope["parent"]
            return ident(e.id)
        if isinstance(e, List):
            return "LtRt.listOf(" + ", ".join(self.expr(x) for x in e.elts) + ")"
        if isinstance(e, Map):
            parts = []
            for k, v in e.pairs:
                parts.append(java_str(k))
                parts.append(self.expr(v))
            return "LtRt.mapOf(" + ", ".join(parts) + ")"
        if isinstance(e, BinOp):
            return self.binop(e)
        if isinstance(e, UnOp):
            a = self.expr(e.operand)
            return f"LtRt.neg({a})" if e.op == "-" else f"(!LtRt.truthy({a}))"
        if isinstance(e, Call):
            return self.call(e)
        if isinstance(e, SuperCall):
            receiver = self.expr(e.args[0])
            args = [self.argument(a) for a in e.args[1:]]
            rendered = ", ".join([receiver, ident(e.owner.name),
                                    java_str(e.method)] + args)
            return f"LtRt.superCall({rendered})"
        if isinstance(e, PyImport):
            return f"LtRt.pymod({self.expr(e.module_expr)})"
        if isinstance(e, JavaImport):
            return f"LtRt.jclass({self.expr(e.class_expr)})"
        raise Exception(f"java backend: unexpected {type(e).__name__}")

    def binop(self, e):
        l, r = self.expr(e.left), self.expr(e.right)
        op = e.op
        if op == "+":
            return f"LtRt.add({l}, {r})"
        if op == "-":
            return f"LtRt.sub({l}, {r})"
        if op == "*":
            return f"LtRt.mul({l}, {r})"
        if op == "/":
            return f"LtRt.div({l}, {r})"
        if op == "%":
            return f"LtRt.mod({l}, {r})"
        if op == "**":
            return f"LtRt.pow({l}, {r})"
        if op == "==":
            return f"LtRt.eq({l}, {r})"
        if op == "!=":
            return f"(!LtRt.eq({l}, {r}))"
        if op == "<":
            return f"LtRt.lt({l}, {r})"
        if op == "<=":
            return f"LtRt.lte({l}, {r})"
        if op == ">":
            return f"LtRt.gt({l}, {r})"
        if op == ">=":
            return f"LtRt.gte({l}, {r})"
        if op == "and":
            return f"(LtRt.truthy({l}) ? {r} : {l})"
        if op == "or":
            return f"(LtRt.truthy({l}) ? {l} : {r})"
        raise Exception(f"java backend: bad op {op}")

    def argument(self, arg):
        if isinstance(arg, NamedArg):
            return f"LtRt.named({java_str(arg.name)}, {self.expr(arg.value)})"
        return self.expr(arg)

    def call(self, e):
        args = [self.argument(a) for a in e.args]
        name = e.func.id if isinstance(e.func, Name) else None
        if name == "__wgetattr":
            return f"LtRt.wgetattr({args[0]}, {args[1]})"
        if name == "__wcall":
            return f"LtRt.wcall({args[0]}, {args[1]}" + \
                ("".join(", " + a for a in args[2:])) + ")"
        if name in BUILTIN_JAVA:
            return f"{BUILTIN_JAVA[name]}({', '.join(args)})"
        callee = self.expr(e.func)
        return f"LtRt.callValue({callee}" + \
            ("".join(", " + a for a in args)) + ")"


def generate(prog, cls, source_path="<src>"):
    return Gen(cls, source_path=source_path).generate(prog)
