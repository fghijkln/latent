"""Latent parser: recursive descent -> AST (nodes.py)."""
from nodes import *
from lex import Tok


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0

    def peek(self):
        return self.toks[self.pos]

    def next(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def err(self, msg, t=None):
        t = t or self.peek()
        raise ParseError(f"{t.line}:{t.col}: {msg}")

    def expect(self, kind):
        t = self.peek()
        if t.kind != kind:
            self.err(f"expected {kind}, got {t.kind}", t)
        return self.next()

    def match(self, kind):
        if self.peek().kind == kind:
            return self.next()
        return None

    # ---- program ----
    def parse(self):
        stmts = []
        while self.peek().kind != "EOF":
            while self.peek().kind == "NEWLINE":
                self.next()
            if self.peek().kind == "EOF":
                break
            stmts.append(self.stmt())
        return Program(stmts)

    def block(self):
        self.expect("NEWLINE")
        self.expect("INDENT")
        stmts = []
        while self.peek().kind != "DEDENT":
            while self.peek().kind == "NEWLINE":
                self.next()
            if self.peek().kind == "DEDENT":
                break
            stmts.append(self.stmt())
        self.expect("DEDENT")
        if not stmts:
            self.err("empty block")
        return stmts

    # ---- statements ----
    def stmt(self):
        t = self.peek()
        if t.kind == "IMPORT":
            self.next()
            path = self.expect("STR")
            self.expect("AS")
            alias = self.expect("NAME")
            self.expect("NEWLINE")
            return ImportStmt(path.value, alias.value, line=t.line, col=t.col)
        if t.kind == "FN":
            return self.fndef()
        if t.kind == "IF":
            return self.ifstmt()
        if t.kind == "WHILE":
            return self.whilestmt()
        if t.kind == "FOR":
            return self.forstmt()
        if t.kind == "SAY":
            return self.saystmt()
        if t.kind == "RETURN":
            return self.returnstmt()
        if t.kind == "BREAK":
            self.next()
            self.expect("NEWLINE")
            return Break(line=t.line, col=t.col)
        if t.kind == "CONTINUE":
            self.next()
            self.expect("NEWLINE")
            return Continue(line=t.line, col=t.col)
        if t.kind == "CLASS":
            return self.classdef()
        if t.kind == "TRY":
            return self.trystmt()
        if t.kind == "THROW":
            self.next()
            v = self.expr()
            self.expect("NEWLINE")
            return Throw(v, line=t.line, col=t.col)
        e = self.expr()
        if self.peek().kind == "=":
            self.next()
            v = self.expr()
            self.expect("NEWLINE")
            if isinstance(e, Name):
                return Assign(e.id, v, line=e.line, col=e.col)
            if isinstance(e, Dot):
                return SetAttr(e.obj, e.attr, v, line=e.line, col=e.col)
            if isinstance(e, Subscript):
                return SetIndex(e.obj, e.index, v, line=e.line, col=e.col)
            raise ParseError(f"{e.line}:{e.col}: cannot assign to this expression")
        self.expect("NEWLINE")
        return ExprStmt(e, line=e.line, col=e.col)

    def classdef(self):
        t = self.expect("CLASS")
        name = self.expect("NAME")
        parent = None
        if self.match("("):
            p = self.expect("NAME")
            alias = None
            parent_name = p.value
            if self.match("."):
                alias = parent_name
                p = self.expect("NAME")
                parent_name = p.value
            parent = ClassRef(parent_name, alias, line=p.line, col=p.col)
            self.expect(")")
        self.expect(":")
        self.expect("NEWLINE")
        self.expect("INDENT")
        methods = []
        while self.peek().kind != "DEDENT":
            while self.peek().kind == "NEWLINE":
                self.next()
            if self.peek().kind == "DEDENT":
                break
            if self.peek().kind != "FN":
                raise ParseError(
                    f"{self.peek().line}:{self.peek().col}: "
                    f"only fn definitions allowed in class body")
            methods.append(self.fndef())
        self.expect("DEDENT")
        if not methods:
            raise ParseError(f"{t.line}:{t.col}: class {name.value!r} has no methods")
        return ClassDef(name.value, methods, parent=parent,
                        line=t.line, col=t.col)

    def fndef(self):
        t = self.expect("FN")
        name = self.expect("NAME")
        self.expect("(")
        params = []
        if self.peek().kind != ")":
            params.append(self.expect("NAME").value)
            while self.match(","):
                params.append(self.expect("NAME").value)
        self.expect(")")
        if self.match("=>"):
            e = self.expr()
            self.expect("NEWLINE")
            body = [Return(e, line=e.line, col=e.col)]
        else:
            self.expect(":")
            body = self.block()
        return FnDef(name.value, params, body, line=t.line, col=t.col)

    def ifstmt(self):
        t = self.expect("IF")
        cond = self.expr()
        self.expect(":")
        then_b = self.block()
        else_b = None
        elifs = []
        while self.peek().kind == "ELIF":
            et = self.next()
            ec = self.expr()
            self.expect(":")
            eb = self.block()
            elifs.append((et, ec, eb))
        if self.peek().kind == "ELSE":
            self.next()
            self.expect(":")
            else_b = self.block()
        for et, ec, eb in reversed(elifs):
            else_b = [If(ec, eb, else_b, line=et.line, col=et.col)]
        return If(cond, then_b, else_b, line=t.line, col=t.col)

    def whilestmt(self):
        t = self.expect("WHILE")
        cond = self.expr()
        self.expect(":")
        body = self.block()
        return While(cond, body, line=t.line, col=t.col)

    def trystmt(self):
        t = self.expect("TRY")
        self.expect(":")
        body = self.block()
        self.expect("CATCH")
        var = self.expect("NAME")
        self.expect(":")
        handler = self.block()
        return Try(body, var.value, handler, line=t.line, col=t.col)

    def forstmt(self):
        t = self.expect("FOR")
        var = self.expect("NAME")
        self.expect("IN")
        it = self.expr()
        self.expect(":")
        body = self.block()
        return For(var.value, it, body, line=t.line, col=t.col)

    def saystmt(self):
        t = self.expect("SAY")
        e = self.expr()
        self.expect("NEWLINE")
        return Say(e, line=t.line, col=t.col)

    def returnstmt(self):
        t = self.expect("RETURN")
        if self.peek().kind == "NEWLINE":
            self.next()
            return Return(None, line=t.line, col=t.col)
        e = self.expr()
        self.expect("NEWLINE")
        return Return(e, line=t.line, col=t.col)

    # ---- expressions ----
    def expr(self):
        return self.orexpr()

    def orexpr(self):
        e = self.andexpr()
        while self.peek().kind == "OR":
            t = self.next()
            e = BinOp("or", e, self.andexpr(), line=t.line, col=t.col)
        return e

    def andexpr(self):
        e = self.notexpr()
        while self.peek().kind == "AND":
            t = self.next()
            e = BinOp("and", e, self.notexpr(), line=t.line, col=t.col)
        return e

    def notexpr(self):
        if self.peek().kind == "NOT":
            t = self.next()
            return UnOp("not", self.notexpr(), line=t.line, col=t.col)
        return self.comparison()

    def comparison(self):
        e = self.arith()
        if self.peek().kind in ("==", "!=", "<", "<=", ">", ">="):
            t = self.next()
            e = BinOp(t.kind, e, self.arith(), line=t.line, col=t.col)
        return e

    def arith(self):
        e = self.term()
        while self.peek().kind in ("+", "-"):
            t = self.next()
            e = BinOp(t.kind, e, self.term(), line=t.line, col=t.col)
        return e

    def term(self):
        e = self.factor()
        while self.peek().kind in ("*", "/", "%"):
            t = self.next()
            e = BinOp(t.kind, e, self.factor(), line=t.line, col=t.col)
        return e

    def factor(self):
        if self.peek().kind == "-":
            t = self.next()
            return UnOp("-", self.factor(), line=t.line, col=t.col)
        return self.power()

    def power(self):
        base = self.postfix()
        if self.peek().kind == "**":
            t = self.next()
            return BinOp("**", base, self.factor(), line=t.line, col=t.col)
        return base

    def postfix(self):
        e = self.primary()
        while True:
            if self.peek().kind == "(":
                t = self.next()
                args = []
                if self.peek().kind != ")":
                    args.append(self.expr())
                    while self.match(","):
                        args.append(self.expr())
                self.expect(")")
                e = Call(e, args, line=t.line, col=t.col)
            elif self.peek().kind == ".":
                self.next()
                attr = self.expect("NAME")
                e = Dot(e, attr.value, line=attr.line, col=attr.col)
            elif self.peek().kind == "[":
                t = self.next()
                idx = self.expr()
                self.expect("]")
                e = Subscript(e, idx, line=t.line, col=t.col)
            else:
                return e

    def primary(self):
        t = self.peek()
        if t.kind == "NUM":
            self.next()
            return Num(t.value, line=t.line, col=t.col)
        if t.kind == "STR":
            self.next()
            return Str(t.value, line=t.line, col=t.col)
        if t.kind == "TRUE":
            self.next()
            return Bool(True, line=t.line, col=t.col)
        if t.kind == "FALSE":
            self.next()
            return Bool(False, line=t.line, col=t.col)
        if t.kind == "NIL":
            self.next()
            return Nil(line=t.line, col=t.col)
        if t.kind == "NAME":
            self.next()
            return Name(t.value, line=t.line, col=t.col)
        if t.kind == "SUPER":
            self.next()
            return Super(line=t.line, col=t.col)
        if t.kind == "PY":
            self.next()
            mod = self.primary()
            return PyImport(mod, line=t.line, col=t.col)
        if t.kind == "JAVA":
            self.next()
            cls = self.primary()
            return JavaImport(cls, line=t.line, col=t.col)
        if t.kind == "(":
            self.next()
            e = self.expr()
            self.expect(")")
            return e
        if t.kind == "[":
            return self.listlit()
        if t.kind == "{":
            return self.maplit()
        self.err(f"unexpected {t.kind} in expression", t)

    def listlit(self):
        t = self.expect("[")
        elts = []
        if self.peek().kind != "]":
            elts.append(self.expr())
            while self.match(","):
                if self.peek().kind == "]":
                    break
                elts.append(self.expr())
        self.expect("]")
        return List(elts, line=t.line, col=t.col)

    def maplit(self):
        t = self.expect("{")
        pairs = []
        if self.peek().kind != "}":
            pairs.append(self.mappair())
            while self.match(","):
                if self.peek().kind == "}":
                    break
                pairs.append(self.mappair())
        self.expect("}")
        return Map(pairs, line=t.line, col=t.col)

    def mappair(self):
        t = self.peek()
        if t.kind == "STR":
            self.next()
            key = t.value
        elif t.kind == "NAME":
            self.next()
            key = t.value
        else:
            self.err("map keys must be strings or bare names", t)
        self.expect(":")
        return (key, self.expr())


def parse(toks):
    return Parser(toks).parse()
