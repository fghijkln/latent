"""ltpy.py - lazy Python daemon for the Latent Java backend.

The Java side starts `python3 -u ltpy.py` on the FIRST actual py-handle
use (never before). Modules are imported lazily inside the daemon and cached.
Line-delimited JSON protocol, one request -> one response:

  {"op":"get",   "target":{"mod":"numpy"}|{"id":7}, "attr":"pi"}
  {"op":"call",  "target":..., "attr":"array", "args":[...]}
  {"op":"repr",  "target":...}
  {"op":"truthy","target":...}
  {"op":"quit"}

Values: JSON natives pass through; anything else becomes an opaque
{"__ref": id} handle the Java side can keep calling into.
"""
import sys
import json
import importlib
import traceback

_objs = {}
_next_id = [1]
_mods = {}


def encode(v):
    # Interop rule: only exact-type JSON natives cross the boundary.
    # Subclass instances (Counter, defaultdict, namedtuple, ...) stay
    # opaque handles so their methods keep working.
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, float):
        if v != v:
            return {"__num": "nan"}
        if v == float("inf"):
            return {"__num": "inf"}
        if v == float("-inf"):
            return {"__num": "-inf"}
        return v
    if type(v) is list or type(v) is tuple:
        return [encode(x) for x in v]
    if type(v) is dict:
        return {str(k): encode(x) for k, x in v.items()}
    i = _next_id[0]
    _next_id[0] += 1
    _objs[i] = v
    return {"__ref": i}


def decode(v):
    if isinstance(v, dict):
        if "__ref" in v:
            return _objs[v["__ref"]]
        if "__num" in v:
            return {"nan": float("nan"), "inf": float("inf"),
                    "-inf": float("-inf")}[v["__num"]]
        if "__mod" in v:
            return getmod(v["__mod"])
        return {k: decode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [decode(x) for x in v]
    return v


def getmod(name):
    if name not in _mods:
        _mods[name] = importlib.import_module(name)
    return _mods[name]


def _pyarg(v):
    """Interop rule: integral floats go into Python as int (e.g. numpy
    needs real ints); everything else passes through. Exact-type checks
    so subclass instances (e.g. a Counter fetched back by __ref) are not
    flattened into plain containers."""
    v = decode(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e18:
        return int(v)
    if type(v) is list or type(v) is tuple:
        return [_pyarg(x) for x in v]
    if type(v) is dict:
        return {k: _pyarg(x) for k, x in v.items()}
    return v


def target(t):
    if "mod" in t:
        return getmod(t["mod"])
    return _objs[t["id"]]


def main():
    inp = sys.stdin
    out = sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            op = req["op"]
            if op == "quit":
                break
            if op == "binop":
                # remote arithmetic: getattr(obj, "__add__")(*rest)
                fargs = [_pyarg(a) for a in req.get("args", [])]
                resp = {"value": encode(getattr(fargs[0], req["name"])(*fargs[1:]))}
                out.write(json.dumps(resp) + "\n")
                out.flush()
                continue
            tgt = target(req["target"])
            if op == "get":
                resp = {"value": encode(getattr(tgt, req["attr"]))}
            elif op == "call":
                fn = getattr(tgt, req["attr"])
                resp = {"value": encode(fn(*[_pyarg(a) for a in req.get("args", [])]))}
            elif op == "repr":
                resp = {"value": str(tgt)}
            elif op == "getitem":
                # xs[i] / m[k] from the Java backend; integral floats -> int
                # so list indices work, string keys pass through as-is
                k = _pyarg(req["key"])
                try:
                    resp = {"value": encode(tgt[k])}
                except (IndexError, KeyError, TypeError) as e:
                    resp = {"error": "index failed: %s" % e}
            elif op == "truthy":
                resp = {"value": bool(tgt)}
            else:
                resp = {"error": "unknown op %r" % op}
        except Exception:
            resp = {"error": traceback.format_exc()}
        out.write(json.dumps(resp) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
