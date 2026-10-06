"""Latent desugar: interpolation, Dot->pyget/pycall, say->builtin,
implicit return of last expression."""
from nodes import *
import lex
import parse as parse_mod


class DesugarError(Exception):
    pass


def _parse_expr(src, line, col):
    try:
        toks = lex.lex(src)
    except lex.LexError as e:
        raise DesugarError(f"{line}:{col}: bad interpolation: {e}")
    p = parse_mod.Parser(toks)
    e = p.expr()
    if p.peek().kind not in ("NEWLINE", "EOF"):
        raise DesugarError(f"{line}:{col}: bad interpolation: trailing tokens")
    e.line, e.col = line, col
    return e


def _split_interp(s, line, col):
    """Split string into [('str', text) | ('expr', src)] parts."""
    parts = []
    i, n = 0, len(s)
    buf = []
    while i < n:
        c = s[i]
        if c == "$" and i + 1 < n:
            d = s[i + 1]
            if d == "$":
                buf.append("$")
                i += 2
                continue
            if d == "{":
                depth = 1
                j = i + 2
                while j < n and depth:
                    if s[j] == "{":
                        depth += 1
                    elif s[j] == "}":
                        depth -= 1
                    j += 1
                if depth:
                    raise DesugarError(f"{line}:{col}: unterminated ${{ in string")
                if buf:
                    parts.append(("str", "".join(buf)))
                    buf = []
                parts.append(("expr", s[i + 2:j - 1]))
                i = j
                continue
            if d.isalpha() or d == "_":
                j = i + 1
                while j < n and (s[j].isalnum() or s[j] == "_"):
                    j += 1
                if buf:
                    parts.append(("str", "".join(buf)))
                    buf = []
                parts.append(("expr", s[i + 1:j]))
                i = j
                continue
        buf.append(c)
        i += 1
    if buf:
        parts.append(("str", "".join(buf)))
    return parts


class Desugar:
    def _desugar_str(self, node):
        parts = _split_interp(node.value, node.line, node.col)
        if not parts or (len(parts) == 1 and parts[0][0] == "str"):
            return node
        expr = None
        for kind, text in parts:
            if kind == "str":
                piece = Str(text, line=node.line, col=node.col)
            else:
                piece = Call(Name("str", line=node.line, col=node.col),
                             [self.expr(_parse_expr(text, node.line, node.col))],
                             line=node.line, col=node.col)
            expr = piece if expr is None else BinOp("+", expr, piece,
                                                   line=node.line, col=node.col)
        return expr

    def run(self, prog):
        return Program([self.stmt(s) for s in prog.stmts],
                       line=0, col=0)

    # ---- statements ----
    def _implicit_return(self, stmts):
        """Last expression statement (incl. inside if/else branches) -> return."""
        if not stmts:
            return stmts
        last = stmts[-1]
        if isinstance(last, ExprStmt):
            stmts[-1] = Return(last.expr, line=last.line, col=last.col)
        elif isinstance(last, If) and last.else_body:
            last.then_body = self._implicit_return(last.then_body)
            last.else_body = self._implicit_return(last.else_body)
        return stmts

    def stmt(self, s):
        if isinstance(s, ImportStmt):
            return ImportStmt(s.module_path, s.alias,
                              line=s.line, col=s.col)
        if isinstance(s, Assign):
            return Assign(s.name, self.expr(s.value), line=s.line, col=s.col)
        if isinstance(s, SetAttr):
            return ExprStmt(
                Call(Name("__wsetattr", line=s.line, col=s.col),
                     [self.expr(s.obj), Str(s.attr, line=s.line, col=s.col),
                      self.expr(s.value)], line=s.line, col=s.col),
                line=s.line, col=s.col)
        if isinstance(s, SetIndex):
            return ExprStmt(
                Call(Name("__wsetindex", line=s.line, col=s.col),
                     [self.expr(s.obj), self.expr(s.index),
                      self.expr(s.value)], line=s.line, col=s.col),
                line=s.line, col=s.col)
        if isinstance(s, ClassDef):
            methods = []
            for m in s.methods:
                dm = self.stmt(m)
                assert isinstance(dm, FnDef)
                methods.append(dm)
            return ClassDef(s.name, methods, parent=s.parent,
                            line=s.line, col=s.col)
        if isinstance(s, FnDef):
            body = self._implicit_return([self.stmt(x) for x in s.body])
            return FnDef(s.name, s.params, body, line=s.line, col=s.col)
        if isinstance(s, Try):
            return Try([self.stmt(x) for x in s.body], s.var,
                       [self.stmt(x) for x in s.handler],
                       line=s.line, col=s.col)
        if isinstance(s, Throw):
            return Throw(self.expr(s.value), line=s.line, col=s.col)
        if isinstance(s, If):
            return If(self.expr(s.cond),
                      [self.stmt(x) for x in s.then_body],
                      [self.stmt(x) for x in s.else_body] if s.else_body else None,
                      line=s.line, col=s.col)
        if isinstance(s, While):
            return While(self.expr(s.cond), [self.stmt(x) for x in s.body],
                         line=s.line, col=s.col)
        if isinstance(s, For):
            return For(s.var, self.expr(s.iter), [self.stmt(x) for x in s.body],
                       line=s.line, col=s.col)
        if isinstance(s, Say):
            return ExprStmt(Call(Name("__say", line=s.line, col=s.col),
                                [self.expr(s.value)], line=s.line, col=s.col),
                          line=s.line, col=s.col)
        if isinstance(s, Return):
            return Return(self.expr(s.value) if s.value else None,
                          line=s.line, col=s.col)
        if isinstance(s, (Break, Continue)):
            return s
        if isinstance(s, ExprStmt):
            return ExprStmt(self.expr(s.expr), line=s.line, col=s.col)
        raise DesugarError(f"{s.line}:{s.col}: unknown stmt {type(s).__name__}")

    # ---- expressions ----
    def expr(self, e):
        if isinstance(e, (Num, Bool, Nil, Name)):
            return e
        if isinstance(e, Super):
            raise DesugarError(
                f"{e.line}:{e.col}: super must be called as super.method(self, ...)")
        if isinstance(e, Str):
            return self._desugar_str(e)
        if isinstance(e, List):
            return List([self.expr(x) for x in e.elts], line=e.line, col=e.col)
        if isinstance(e, Map):
            return Map([(k, self.expr(v)) for k, v in e.pairs],
                       line=e.line, col=e.col)
        if isinstance(e, BinOp):
            return BinOp(e.op, self.expr(e.left), self.expr(e.right),
                         line=e.line, col=e.col)
        if isinstance(e, UnOp):
            return UnOp(e.op, self.expr(e.operand), line=e.line, col=e.col)
        if isinstance(e, Dot):
            if isinstance(e.obj, Super):
                raise DesugarError(
                    f"{e.line}:{e.col}: super must be called as super.method(self, ...)")
            obj = self.expr(e.obj)
            return Call(Name("__wgetattr", line=e.line, col=e.col),
                        [obj, Str(e.attr, line=e.line, col=e.col)],
                        line=e.line, col=e.col)
        if isinstance(e, Subscript):
            return Call(Name("__index", line=e.line, col=e.col),
                        [self.expr(e.obj), self.expr(e.index)],
                        line=e.line, col=e.col)
        if isinstance(e, Call):
            if isinstance(e.func, Dot) and isinstance(e.func.obj, Super):
                return SuperCall(e.func.attr,
                                 [self.expr(a) for a in e.args],
                                 line=e.line, col=e.col)
            func = self.expr(e.func)
            args = [self.expr(a) for a in e.args]
            if isinstance(e.func, Dot):
                d = e.func
                return Call(Name("__wcall", line=e.line, col=e.col),
                            [self.expr(d.obj),
                             Str(d.attr, line=e.line, col=e.col)] + args,
                            line=e.line, col=e.col)
            return Call(func, args, line=e.line, col=e.col)
        if isinstance(e, PyImport):
            return PyImport(self.expr(e.module_expr), line=e.line, col=e.col)
        if isinstance(e, JavaImport):
            return JavaImport(self.expr(e.class_expr), line=e.line, col=e.col)
        raise DesugarError(f"{e.line}:{e.col}: unknown expr {type(e).__name__}")


def desugar(prog):
    return Desugar().run(prog)
