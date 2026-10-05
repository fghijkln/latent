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
    def __init__(self, cls):
        self.cls = cls
        self.out = []
        self.ind = 0
        self.tmp = 0

    def w(self, s=""):
        self.out.append("    " * self.ind + s)

    def generate(self, prog):
        fns = [s for s in prog.stmts if isinstance(s, FnDef)]
        rest = [s for s in prog.stmts if not isinstance(s, FnDef)]
        gnames = []
        for s in rest:
            if isinstance(s, Assign) and s.name not in gnames:
                gnames.append(s.name)
            elif isinstance(s, For) and s.var not in gnames:
                gnames.append(s.var)
        self.w(f"public class {self.cls} " + "{")
        self.ind += 1
        for g in gnames:
            self.w(f"static Object {ident(g)};")
        if gnames:
            self.w("")
        self.w("public static void main(String[] args) {")
        self.ind += 1
        for s in rest:
            self.stmt(s)
        self.w("LtRt.shutdown();")
        self.ind -= 1
        self.w("}")
        for fn in fns:
            self.w("")
            self.fndef(fn)
        self.ind -= 1
        self.w("}")
        return "\n".join(self.out) + "\n"

    def fndef(self, fn):
        params = ", ".join(f"Object {ident(p)}" for p in fn.params)
        self.w(f"static Object {ident(fn.name)}({params}) " + "{")
        self.ind += 1
        assigned = set()
        self._collect(fn.body, assigned)
        assigned -= set(fn.params)
        for name in sorted(assigned):
            self.w(f"Object {ident(name)} = null; // local defaults to nil")
        for s in fn.body:
            self.stmt(s)
        if not self._always_returns(fn.body):
            self.w("return null;")
        self.ind -= 1
        self.w("}")

    def _always_returns(self, stmts):
        if not stmts:
            return False
        last = stmts[-1]
        if isinstance(last, Return):
            return True
        if isinstance(last, If) and last.else_body:
            return (self._always_returns(last.then_body) and
                    self._always_returns(last.else_body))
        return False

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

    def stmt(self, s):
        if isinstance(s, Assign):
            self.w(f"{ident(s.name)} = {self.expr(s.value)};")
        elif isinstance(s, ExprStmt):
            self.w(f"{self.expr(s.expr)};")
        elif isinstance(s, If):
            self.w(f"if (LtRt.truthy({self.expr(s.cond)})) " + "{")
            self.suite(s.then_body)
            if s.else_body:
                self.w("} else {")
                self.suite(s.else_body)
            self.w("}")
        elif isinstance(s, While):
            self.w(f"while (LtRt.truthy({self.expr(s.cond)})) " + "{")
            self.suite(s.body)
            self.w("}")
        elif isinstance(s, For):
            t = f"wv$it{self.tmp}"
            self.tmp += 1
            self.w(f"for (Object {t} : LtRt.iter({self.expr(s.iter)})) " + "{")
            self.ind += 1
            self.w(f"{ident(s.var)} = {t};")
            for x in s.body:
                self.stmt(x)
            self.ind -= 1
            self.w("}")
        elif isinstance(s, Return):
            self.w(f"return {self.expr(s.value)};" if s.value is not None
                   else "return null;")
        elif isinstance(s, Break):
            self.w("break;")
        elif isinstance(s, Continue):
            self.w("continue;")
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

    def call(self, e):
        assert isinstance(e.func, Name)
        name = e.func.id
        args = [self.expr(a) for a in e.args]
        if name == "__wgetattr":
            return f"LtRt.wgetattr({args[0]}, {args[1]})"
        if name == "__wcall":
            return f"LtRt.wcall({args[0]}, {args[1]}" + \
                ("".join(", " + a for a in args[2:])) + ")"
        if name in BUILTIN_JAVA:
            return f"{BUILTIN_JAVA[name]}({', '.join(args)})"
        return f"{ident(name)}({', '.join(args)})"


def generate(prog, cls):
    return Gen(cls).generate(prog)
