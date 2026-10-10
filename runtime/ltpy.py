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
import math
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
        return {"__map": [[str(k), encode(x)] for k, x in v.items()]}
    i = _next_id[0]
    _next_id[0] += 1
    _objs[i] = v
    return {"__ref": i}


def decode(v):
    if isinstance(v, dict):
        if len(v) == 1 and "__map" in v:
            entries = v["__map"]
            if not isinstance(entries, list):
                raise ValueError("invalid Python map envelope")
            result = {}
            for pair in entries:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("invalid Python map entry")
                result[str(pair[0])] = decode(pair[1])
            return result
        if len(v) == 1 and "__ref" in v:
            return _objs[v["__ref"]]
        if len(v) == 1 and "__num" in v:
            return {"nan": float("nan"), "inf": float("inf"),
                    "-inf": float("-inf")}[v["__num"]]
        if len(v) == 1 and "__mod" in v:
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
    """Integral floats go into Python as int (e.g. numpy needs real ints),
    except negative zero, whose sign must survive. Exact-type checks
    so subclass instances (e.g. a Counter fetched back by __ref) are not
    flattened into plain containers."""
    return _pyarg_decoded(decode(v))


def _pyarg_decoded(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e18:
        if v != 0.0 or math.copysign(1.0, v) > 0:
            return int(v)
    if type(v) is list or type(v) is tuple:
        return [_pyarg_decoded(x) for x in v]
    if type(v) is dict:
        return {k: _pyarg_decoded(x) for k, x in v.items()}
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
            elif op == "setattr":
                setattr(tgt, req["attr"], _pyarg(req["value"]))
                resp = {"value": None}
            elif op == "setitem":
                try:
                    tgt[_pyarg(req["key"])] = _pyarg(req["value"])
                    resp = {"value": None}
                except (IndexError, KeyError, TypeError) as e:
                    resp = {"error": "index-assign failed: %s" % e}
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
