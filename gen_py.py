"""Latent -> Python backend."""
import json
from nodes import *

PRELUDE = r'''
import importlib as _importlib
import atexit as _atexit
import json as _json
import math as _math
import os as _os
import shutil as _shutil
import subprocess as _subprocess


class _LazyMod:
    """Lazily imported Python module. import happens on first attribute use."""
    def __init__(self, name):
        self.__dict__["_name"] = name
        self.__dict__["_mod"] = None

    def _load(self):
        if self.__dict__["_mod"] is None:
            self.__dict__["_mod"] = _importlib.import_module(self.__dict__["_name"])
        return self.__dict__["_mod"]

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        return getattr(self._load(), attr)


def _wv_repr(v):
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float):
        if _math.isinf(v):
            return "inf" if v > 0 else "-inf"
        if _math.isnan(v):
            return "nan"
        if v.is_integer():
            return str(int(v))
        return repr(v)
    if isinstance(v, str):
        return v
    if isinstance(v, _LazyMod):
        return "<module %s>" % v.__dict__["_name"]
    if isinstance(v, _JClass):
        return "<class %s>" % v._name
    if isinstance(v, _JHandle):
        jvm = _JVM.inst()
        return jvm.req({"op": "repr", "target": jvm.target(v)})
    if isinstance(v, list):
        return "[" + ", ".join(_wv_repr_q(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(_wv_repr_q(k) + ": " + _wv_repr_q(x)
                               for k, x in v.items()) + "}"
    try:
        return str(v)
    except Exception:
        return repr(v)


def _wv_repr_q(v):
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') \
                      .replace("\n", "\\n").replace("\t", "\\t") + '"'
    return _wv_repr(v)


def _wv_say(v):
    print(_wv_repr(v))


def _wv_num(v):
    if not isinstance(v, float):
        raise TypeError("number expected, got " + _wv_repr(v))
    return v


def _wv_add(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return a + b
    if isinstance(a, str) and isinstance(b, str):
        return a + b
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    try:  # remote objects (py handles): let Python do it
        return a + b
    except Exception:
        raise TypeError("bad + operands: %s, %s" % (_wv_repr(a), _wv_repr(b)))


def _wv_sub(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return a - b
    try:
        return a - b
    except Exception:
        raise TypeError("bad - operands: %s, %s" % (_wv_repr(a), _wv_repr(b)))


def _wv_mul(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return a * b
    try:
        return a * b
    except Exception:
        raise TypeError("bad * operands: %s, %s" % (_wv_repr(a), _wv_repr(b)))


def _wv_div(a, b):
    if isinstance(a, float) and isinstance(b, float):
        if b == 0:
            raise ZeroDivisionError("division by zero")
        return a / b
    try:
        return a / b
    except Exception:
        raise TypeError("bad / operands: %s, %s" % (_wv_repr(a), _wv_repr(b)))


def _wv_mod(a, b):
    if isinstance(a, float) and isinstance(b, float):
        if b == 0:
            raise ZeroDivisionError("division by zero")
        return a - b * _math.floor(a / b)
    try:
        return a % b
    except Exception:
        raise TypeError("bad %% operands: %s, %s" % (_wv_repr(a), _wv_repr(b)))


def _wv_pow(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return a ** b
    try:
        return a ** b
    except Exception:
        raise TypeError("bad ** operands: %s, %s" % (_wv_repr(a), _wv_repr(b)))


def _wv_neg(a):
    if isinstance(a, float):
        return -a
    try:
        return -a
    except Exception:
        raise TypeError("bad unary - operand: " + _wv_repr(a))


def _wv_eq(a, b):
    if isinstance(a, float) and isinstance(b, float):
        if _math.isnan(a) or _math.isnan(b):
            return False
        return a == b
    # remote objects (py handles): identity comparison, matches Java backend
    if not isinstance(a, (bool, float, str, list, dict)) or \
       not isinstance(b, (bool, float, str, list, dict)):
        return a is b
    if type(a) is not type(b):
        return False
    return a == b


def _wv_cmp(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return (a > b) - (a < b)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    raise TypeError("bad comparison: %s, %s" % (_wv_repr(a), _wv_repr(b)))


def _wv_len(x):
    if isinstance(x, (list, dict, str)):
        return float(len(x))
    raise TypeError("len() of " + _wv_repr(x))


def _wv_range(a, b=None):
    if b is None:
        a, b = 0.0, a
    return [float(i) for i in range(int(_math.trunc(_wv_num(a))),
                                   int(_math.trunc(_wv_num(b))))]


def _wv_str(x):
    return _wv_repr(x)


def _wv_int(x):
    if isinstance(x, bool):
        return float(int(x))
    if isinstance(x, float):
        return float(_math.trunc(x))
    if isinstance(x, str):
        return float(int(x.strip()))
    raise TypeError("int() of " + _wv_repr(x))


def _wv_push(xs, x):
    if not isinstance(xs, list):
        raise TypeError("push() target must be a list")
    xs.append(x)
    return None


def _wv_keys(m):
    if not isinstance(m, dict):
        raise TypeError("keys() of non-map")
    return list(m.keys())


def _wv_iter(x):
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        return list(x)
    if isinstance(x, dict):
        return list(x.keys())
    if isinstance(x, _JHandle):  # java List / array -> materialize via daemon
        jvm = _JVM.inst()
        return jvm.req({"op": "tolist", "target": jvm.target(x)})
    raise TypeError("cannot iterate " + _wv_repr(x))


def _wv_pyarg(v):
    """Interop rule (mirrors ltpy daemon): integral floats enter
    Python as int; everything else passes through."""
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e18:
        return int(v)
    if isinstance(v, list):
        return [_wv_pyarg(x) for x in v]
    if isinstance(v, dict):
        return {k: _wv_pyarg(x) for k, x in v.items()}
    return v


def _wv_pymod(name):
    if not isinstance(name, str):
        raise TypeError("py module name must be a string")
    return _LazyMod(name)


# ---------------- java interop (via a lazily-started JVM daemon) ----------------

class _JClass:
    """Lazy Java class handle. The JVM starts only on first real use."""
    def __init__(self, name):
        self._name = name


class _JHandle:
    """Opaque handle to a live Java object in the daemon."""
    def __init__(self, id):
        self._id = id


def _jvm_encode(v):
    if isinstance(v, _JHandle):
        return {"__jref": v._id}
    if isinstance(v, _JClass):
        return {"__jclass": v._name}
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, str):
        return v
    if v is None:
        return None
    if isinstance(v, list):
        return [_jvm_encode(x) for x in v]
    if isinstance(v, dict):
        return {k: _jvm_encode(x) for k, x in v.items()}
    raise TypeError("cannot send to JVM: " + _wv_repr(v))


def _jvm_decode(v):
    if isinstance(v, dict):
        if "__jref" in v:
            return _JHandle(int(v["__jref"]))
        if "__jclass" in v:
            return _JClass(v["__jclass"])
        return {k: _jvm_decode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jvm_decode(x) for x in v]
    if isinstance(v, int) and not isinstance(v, bool):
        return float(v)  # the language has one number type: float64
    return v


class _JVM:
    _inst = None

    @classmethod
    def inst(cls):
        if cls._inst is None:
            jvm = _JVM()
            jvm.start()
            cls._inst = jvm
        return cls._inst

    def start(self):
        cp = _os.environ.get("LATENT_JAVAD")
        if not cp:
            cp = _os.path.dirname(_os.path.abspath(__file__))
        self._ensure_compiled(cp)
        try:
            self._proc = _subprocess.Popen(
                ["java", "-cp", cp, "LtJavaDaemon"],
                stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
                stderr=None, text=True, bufsize=1, encoding="utf-8")
        except OSError as e:
            raise RuntimeError("cannot start java (needed for java ...): " + str(e))

    @staticmethod
    def _ensure_compiled(cp):
        if _os.path.exists(_os.path.join(cp, "LtJavaDaemon.class")):
            return
        javac = _shutil.which("javac")
        srcs = [s for s in ("LtJavaDaemon.java", "JReflect.java", "LtRt.java")
                if _os.path.exists(_os.path.join(cp, s))]
        if javac and srcs:
            r = _subprocess.run(
                [javac, "-d", cp, "-cp", cp] +
                [_os.path.join(cp, s) for s in srcs],
                capture_output=True, text=True)
            if r.returncode == 0 and \
               _os.path.exists(_os.path.join(cp, "LtJavaDaemon.class")):
                return
            raise RuntimeError("javac failed:\n" + r.stdout + r.stderr)
        raise RuntimeError(
            "LtJavaDaemon.class not found next to " + cp +
            " and javac is unavailable; compile the runtime once with:\n"
            "  javac -d <dir> <dir>/LtJavaDaemon.java"
            " <dir>/JReflect.java <dir>/LtRt.java")

    def req(self, obj):
        self._proc.stdin.write(_json.dumps(obj) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("JVM daemon died")
        resp = _json.loads(line)
        if "error" in resp:
            raise RuntimeError("java error: " + resp["error"])
        return _jvm_decode(resp["value"])

    def target(self, h):
        if isinstance(h, _JClass):
            return {"class": h._name}
        if isinstance(h, _JHandle):
            return {"id": h._id}
        raise TypeError("not a java handle: " + _wv_repr(h))

    def close(self):
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


def _jvm_shutdown():
    if _JVM._inst is not None:
        try:
            _JVM._inst.close()
        except Exception:
            pass


_atexit.register(_jvm_shutdown)


def _wv_jclass(name):
    if not isinstance(name, str):
        raise TypeError("java class name must be a string")
    return _JClass(name)


# ---------------- unified handle access (py + java) ----------------

def _wv_wgetattr(h, attr):
    if isinstance(h, (_JClass, _JHandle)):
        jvm = _JVM.inst()
        return jvm.req({"op": "get", "target": jvm.target(h), "field": attr})
    return getattr(h, attr)


def _wv_wcall(h, attr, *args):
    if isinstance(h, (_JClass, _JHandle)):
        jvm = _JVM.inst()
        jargs = [_jvm_encode(a) for a in args]
        if isinstance(h, _JClass) and attr == "new":
            return jvm.req({"op": "new", "class": h._name, "args": jargs})
        return jvm.req({"op": "call", "target": jvm.target(h),
                        "method": attr, "args": jargs})
    return getattr(h, attr)(*[_wv_pyarg(a) for a in args])

'''

BUILTIN_PY = {
    "__say": "_wv_say", "len": "_wv_len", "range": "_wv_range",
    "str": "_wv_str", "int": "_wv_int", "push": "_wv_push",
    "keys": "_wv_keys", "__wgetattr": "_wv_wgetattr", "__wcall": "_wv_wcall",
}


class Gen:
    def __init__(self):
        self.out = []
        self.indent = 0

    def w(self, s=""):
        self.out.append("    " * self.indent + s)

    def generate(self, prog):
        self.w(PRELUDE.strip("\n"))
        fns = [s for s in prog.stmts if isinstance(s, FnDef)]
        rest = [s for s in prog.stmts if not isinstance(s, FnDef)]
        for fn in fns:
            self.w("")
            self.fndef(fn)
        if rest:
            self.w("")
            self.w('if __name__ == "__main__":')
            self.indent += 1
            for s in rest:
                self.stmt(s)
            self.indent -= 1
        return "\n".join(self.out) + "\n"

    def fndef(self, fn):
        # locals default to nil (matches Java backend)
        assigned = set()
        self._collect(fn.body, assigned)
        assigned -= set(fn.params)
        self.w(f"def {fn.name}({', '.join(fn.params)}):")
        self.indent += 1
        for name in sorted(assigned):
            self.w(f"{name} = None")
        for s in fn.body:
            self.stmt(s)
        if not any(isinstance(s, Return) for s in fn.body):
            pass
        self.indent -= 1

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
            self.w(f"{s.name} = {self.expr(s.value)}")
        elif isinstance(s, ExprStmt):
            self.w(self.expr(s.expr))
        elif isinstance(s, If):
            self.w(f"if {self.expr(s.cond)}:")
            self.suite(s.then_body)
            if s.else_body:
                self.w("else:")
                self.suite(s.else_body)
        elif isinstance(s, While):
            self.w(f"while {self.expr(s.cond)}:")
            self.suite(s.body)
        elif isinstance(s, For):
            self.w(f"for {s.var} in _wv_iter({self.expr(s.iter)}):")
            self.suite(s.body)
        elif isinstance(s, Return):
            self.w(f"return {self.expr(s.value)}" if s.value is not None
                   else "return None")
        elif isinstance(s, Break):
            self.w("break")
        elif isinstance(s, Continue):
            self.w("continue")
        else:
            raise Exception(f"py backend: unexpected {type(s).__name__}")

    def suite(self, stmts):
        self.indent += 1
        for s in stmts:
            self.stmt(s)
        self.indent -= 1

    def expr(self, e):
        if isinstance(e, Num):
            return self.num(e.value)
        if isinstance(e, Str):
            return json.dumps(e.value, ensure_ascii=False)
        if isinstance(e, Bool):
            return "True" if e.value else "False"
        if isinstance(e, Nil):
            return "None"
        if isinstance(e, Name):
            return e.id
        if isinstance(e, List):
            return "[" + ", ".join(self.expr(x) for x in e.elts) + "]"
        if isinstance(e, Map):
            return "{" + ", ".join(f"{json.dumps(k)}: {self.expr(v)}"
                                   for k, v in e.pairs) + "}"
        if isinstance(e, BinOp):
            return self.binop(e)
        if isinstance(e, UnOp):
            a = self.expr(e.operand)
            return f"(-{a})" if e.op == "-" else f"(not {a})"
        if isinstance(e, Call):
            args = ", ".join(self.expr(a) for a in e.args)
            if isinstance(e.func, Name) and e.func.id in BUILTIN_PY:
                return f"{BUILTIN_PY[e.func.id]}({args})"
            return f"{self.expr(e.func)}({args})"
        if isinstance(e, PyImport):
            return f"_wv_pymod({self.expr(e.module_expr)})"
        if isinstance(e, JavaImport):
            return f"_wv_jclass({self.expr(e.class_expr)})"
        raise Exception(f"py backend: unexpected {type(e).__name__}")

    def num(self, v):
        if v != v:
            return 'float("nan")'
        if v == float("inf"):
            return 'float("inf")'
        if v == float("-inf"):
            return 'float("-inf")'
        return repr(v)

    def binop(self, e):
        l, r = self.expr(e.left), self.expr(e.right)
        op = e.op
        if op == "+":
            return f"_wv_add({l}, {r})"
        if op == "-":
            return f"_wv_sub({l}, {r})"
        if op == "*":
            return f"_wv_mul({l}, {r})"
        if op == "/":
            return f"_wv_div({l}, {r})"
        if op == "%":
            return f"_wv_mod({l}, {r})"
        if op == "**":
            return f"_wv_pow({l}, {r})"
        if op == "==":
            return f"_wv_eq({l}, {r})"
        if op == "!=":
            return f"(not _wv_eq({l}, {r}))"
        if op in ("<", "<=", ">", ">="):
            return f"(_wv_cmp({l}, {r}) {op} 0)"
        if op == "and":
            return f"({l} and {r})"
        if op == "or":
            return f"({l} or {r})"
        raise Exception(f"py backend: bad op {op}")


def generate(prog):
    return Gen().generate(prog)
