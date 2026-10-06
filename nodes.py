"""Latent AST nodes."""


class Node:
    def __init__(self, line=0, col=0):
        self.line = line
        self.col = col
        self.source_path = None


# ---- statements ----
class Program(Node):
    def __init__(self, stmts, **kw):
        super().__init__(**kw)
        self.stmts = stmts


class ImportStmt(Node):
    """Compile-time import of a local .lt module under a namespace alias."""
    def __init__(self, module_path, alias, **kw):
        super().__init__(**kw)
        self.module_path = module_path
        self.alias = alias


class ModuleInit(Node):
    """One namespaced module body in a bundled multi-module program."""
    def __init__(self, module_id, deps, body, globals_, init_name,
                 state_name, root=False, **kw):
        super().__init__(**kw)
        self.module_id = module_id
        self.deps = deps
        self.body = body
        self.globals = globals_
        self.init_name = init_name
        self.state_name = state_name
        self.root = root


class Assign(Node):
    def __init__(self, name, value, **kw):
        super().__init__(**kw)
        self.name = name
        self.value = value


class SetAttr(Node):
    """Attribute write statement: obj.attr = value. Desugar rewrites to
    __wsetattr(obj, "attr", value)."""
    def __init__(self, obj, attr, value, **kw):
        super().__init__(**kw)
        self.obj = obj
        self.attr = attr
        self.value = value


class SetIndex(Node):
    """Index write statement: obj[key] = value. Desugar rewrites to
    __wsetindex(obj, key, value)."""
    def __init__(self, obj, index, value, **kw):
        super().__init__(**kw)
        self.obj = obj
        self.index = index
        self.value = value


class ClassRef(Node):
    """Static Latent class reference, optionally qualified by a module alias."""
    def __init__(self, name, alias=None, **kw):
        super().__init__(**kw)
        self.name = name
        self.alias = alias
        self.display = f"{alias}.{name}" if alias else name


class ClassDef(Node):
    """Class definition. methods is a list of FnDef; the first parameter
    of each method receives the instance (self, by convention)."""
    def __init__(self, name, methods, parent=None, **kw):
        super().__init__(**kw)
        self.name = name
        self.methods = methods
        self.parent = parent
        self.source_name = name


class FnDef(Node):
    def __init__(self, name, params, body, **kw):
        super().__init__(**kw)
        self.name = name
        self.params = params
        self.body = body  # list of stmts


class If(Node):
    def __init__(self, cond, then_body, else_body, **kw):
        super().__init__(**kw)
        self.cond = cond
        self.then_body = then_body
        self.else_body = else_body  # list of stmts or None


class While(Node):
    def __init__(self, cond, body, **kw):
        super().__init__(**kw)
        self.cond = cond
        self.body = body


class For(Node):
    def __init__(self, var, iter, body, **kw):
        super().__init__(**kw)
        self.var = var
        self.iter = iter
        self.body = body


class Say(Node):
    def __init__(self, value, **kw):
        super().__init__(**kw)
        self.value = value


class Throw(Node):
    """Throw statement: throw <expr>. The value is stringified; catch
    receives the message string."""
    def __init__(self, value, **kw):
        super().__init__(**kw)
        self.value = value


class Try(Node):
    """Try/catch statement: try: body catch var: handler. var receives
    the error message string."""
    def __init__(self, body, var, handler, **kw):
        super().__init__(**kw)
        self.body = body      # list of stmts
        self.var = var        # str: catch variable name
        self.handler = handler  # list of stmts


class Return(Node):
    def __init__(self, value, **kw):
        super().__init__(**kw)
        self.value = value  # may be None


class Break(Node):
    pass


class Continue(Node):
    pass


class ExprStmt(Node):
    def __init__(self, expr, **kw):
        super().__init__(**kw)
        self.expr = expr


# ---- expressions ----
class Num(Node):
    def __init__(self, value, **kw):
        super().__init__(**kw)
        self.value = value  # float


class Str(Node):
    def __init__(self, value, **kw):
        super().__init__(**kw)
        self.value = value


class Bool(Node):
    def __init__(self, value, **kw):
        super().__init__(**kw)
        self.value = value


class Nil(Node):
    pass


class Name(Node):
    def __init__(self, id, **kw):
        super().__init__(**kw)
        self.id = id


class Super(Node):
    """The restricted super receiver; never a first-class value."""


class SuperCall(Node):
    """A call to a method starting at the current class's direct parent."""
    def __init__(self, method, args, **kw):
        super().__init__(**kw)
        self.method = method
        self.args = args
        self.owner = None  # ClassDef attached by semantic analysis


class List(Node):
    def __init__(self, elts, **kw):
        super().__init__(**kw)
        self.elts = elts


class Map(Node):
    def __init__(self, pairs, **kw):
        super().__init__(**kw)
        self.pairs = pairs  # list of (key_str, expr)


class BinOp(Node):
    def __init__(self, op, left, right, **kw):
        super().__init__(**kw)
        self.op = op
        self.left = left
        self.right = right


class UnOp(Node):
    def __init__(self, op, operand, **kw):
        super().__init__(**kw)
        self.op = op  # '-' or 'not'
        self.operand = operand


class Call(Node):
    def __init__(self, func, args, **kw):
        super().__init__(**kw)
        self.func = func  # expr (Name normally)
        self.args = args


class PyImport(Node):
    def __init__(self, module_expr, **kw):
        super().__init__(**kw)
        self.module_expr = module_expr


class Dot(Node):
    """Attribute access. v0.2: meaningful on py handles AND java handles;
    desugar rewrites to __wgetattr/__wcall, runtime dispatches on handle type."""
    def __init__(self, obj, attr, **kw):
        super().__init__(**kw)
        self.obj = obj
        self.attr = attr


class Subscript(Node):
    """Indexing. xs[i] / m[k] / s[i]. Desugar rewrites to __index(obj, key);
    the runtime dispatches on the value type (list/map/string/handles)."""
    def __init__(self, obj, index, **kw):
        super().__init__(**kw)
        self.obj = obj
        self.index = index


class JavaImport(Node):
    def __init__(self, class_expr, **kw):
        super().__init__(**kw)
        self.class_expr = class_expr


def class_order(classes):
    """Stable topological order for class metadata initialization."""
    by_name = {cd.name: cd for cd in classes}
    result = []
    seen = set()

    def visit(cd):
        if cd.name in seen:
            return
        seen.add(cd.name)
        if cd.parent is not None and cd.parent.name in by_name:
            visit(by_name[cd.parent.name])
        result.append(cd)

    for cd in classes:
        visit(cd)
    return result
