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
import inspect
import math
import traceback
import types

_objs = {}
_bound_methods = {}
_module_functions = {}
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


def bound_instance_method(receiver, attr):
    """Capture only native Python/C instance-method descriptors, never fields or properties."""
    try:
        descriptor = inspect.getattr_static(receiver, attr)
    except AttributeError:
        return None
    if type(descriptor) is types.FunctionType:
        method = getattr(receiver, attr)
        if type(method) is types.MethodType and method.__self__ is receiver:
            return method
        return None
    supported = tuple(t for t in (
        getattr(types, "MethodDescriptorType", None),
        getattr(types, "WrapperDescriptorType", None)) if t is not None)
    if supported and type(descriptor) in supported:
        method = getattr(receiver, attr)
        if type(method) is types.BuiltinMethodType and method.__self__ is receiver:
            return method
    return None


def module_function(module, attr):
    """Capture only a function explicitly exported in a real module namespace."""
    if type(module) is not types.ModuleType:
        return None
    namespace = vars(module)
    if attr not in namespace:
        return None
    value = getattr(module, attr)
    if namespace[attr] is not value:
        return None
    if type(value) is types.FunctionType:
        return value
    if (type(value) is types.BuiltinFunctionType and
            module.__name__ != "builtins" and
            value.__self__ is module):
        return value
    return None


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
            if op == "call_bound_method":
                method_id = req.get("id")
                method = _bound_methods.get(method_id)
                if method is None:
                    raise RuntimeError("stale Python bound-method ref: %s" % method_id)
                args = [_pyarg(a) for a in req.get("args", [])]
                resp = {"value": encode(method(*args))}
                out.write(json.dumps(resp) + "\n")
                out.flush()
                continue
            if op == "call_module_function":
                function_id = req.get("id")
                function = _module_functions.get(function_id)
                if function is None:
                    raise RuntimeError("stale Python module-function ref: %s" % function_id)
                args = [_pyarg(a) for a in req.get("args", [])]
                resp = {"value": encode(function(*args))}
                out.write(json.dumps(resp) + "\n")
                out.flush()
                continue
            if op == "get_module_function":
                raw_target = req["target"]
                function = (module_function(getmod(raw_target["mod"]), req["attr"])
                            if "mod" in raw_target else None)
                if function is None:
                    resp = {"module_function": None}
                else:
                    function_id = _next_id[0]
                    _next_id[0] += 1
                    _module_functions[function_id] = function
                    resp = {"module_function": {
                        "id": function_id,
                        "module": getmod(raw_target["mod"]).__name__,
                        "name": req["attr"]}}
                out.write(json.dumps(resp) + "\n")
                out.flush()
                continue
            if op == "get_bound_method":
                tgt = target(req["target"])
                method = (None if "mod" in req["target"] else
                          bound_instance_method(tgt, req["attr"]))
                if method is None:
                    resp = {"bound_method": None}
                else:
                    method_id = _next_id[0]
                    _next_id[0] += 1
                    _bound_methods[method_id] = method
                    cls = type(tgt)
                    resp = {"bound_method": {
                        "id": method_id,
                        "type": "%s.%s" % (cls.__module__, cls.__qualname__),
                        "member": req["attr"]}}
                out.write(json.dumps(resp) + "\n")
                out.flush()
                continue
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
