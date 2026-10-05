"""Latent AST nodes."""


class Node:
    def __init__(self, line=0, col=0):
        self.line = line
        self.col = col


# ---- statements ----
class Program(Node):
    def __init__(self, stmts, **kw):
        super().__init__(**kw)
        self.stmts = stmts


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


class ClassDef(Node):
    """Class definition. methods is a list of FnDef; the first parameter
    of each method receives the instance (self, by convention)."""
    def __init__(self, name, methods, **kw):
        super().__init__(**kw)
        self.name = name
        self.methods = methods


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
