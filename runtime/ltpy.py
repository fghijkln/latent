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
import os
import threading

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
        if len(v) == 1 and "__lt_callback__" in v:
            raise ProtocolAbort("callback descriptor is only valid as a direct argument")
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
        if isinstance(method, types.BuiltinMethodType) and method.__self__ is receiver:
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


class ProtocolAbort(BaseException):
    """Fatal transport/schema failure; never reinterpret it as a host error."""


class CallbackError(Exception):
    """Carrier for a Java callback failure; host Exception handlers may inspect it."""
    def __init__(self, error_id, error_message):
        self.error_id = error_id
        self.error_message = error_message
        super().__init__(error_message)


class Scope:
    def __init__(self, request_id):
        self.id = request_id
        self.thread = threading.get_ident()
        self.active = True
        self.proxies = []
        self.invocations = set()
        self.callback_tokens = set()
        self.used_callback_tokens = set()


_scopes = []
_next_callback_invocation = 1
_last_request_id = 0


class CallbackProxy:
    def __init__(self, token, scope):
        self.token = token
        self.scope = scope
        scope.proxies.append(self)

    def __call__(self, *args, **kwargs):
        global _next_callback_invocation
        if not self.scope.active:
            raise RuntimeError("callback scope expired")
        if threading.get_ident() != self.scope.thread:
            raise RuntimeError("cross-thread callback is unsupported")
        if kwargs:
            raise TypeError("host callback keyword arguments are unsupported")
        if not _scopes or _scopes[-1] is not self.scope:
            raise RuntimeError("callback scope is not current")
        invocation = _next_callback_invocation
        _next_callback_invocation += 1
        if invocation > 9007199254740991:
            raise RuntimeError("callback invocation ID limit exceeded")
        self.scope.invocations.add(invocation)
        if self.scope.proxies:
            for value in args:
                _reject_callback_or_deferred(value)
        frame = {"kind": "callback", "parent": self.scope.id,
                 "id": invocation, "callback": self.token,
                 "args": [encode(v) for v in args]}
        _protocol_send(frame)
        while True:
            try:
                incoming = _protocol_receive()
            except BaseException as exc:
                raise ProtocolAbort("invalid callback protocol input") from exc
            if incoming.get("kind") == "request":
                try:
                    _serve(incoming)
                except BaseException as exc:
                    raise ProtocolAbort("invalid nested request frame") from exc
                continue
            if incoming.get("kind") != "callback_result":
                raise ProtocolAbort("invalid callback result direction")
            expected = {"kind", "parent", "id", "value"} if "value" in incoming else {
                "kind", "parent", "id", "error_id", "error_message"}
            if set(incoming) != expected:
                raise ProtocolAbort("invalid callback result schema")
            if (_positive_id(incoming["id"], "callback result") != invocation or
                    _positive_id(incoming["parent"], "callback result parent") != self.scope.id):
                raise ProtocolAbort("callback result ID/parent mismatch")
            if "error_id" in incoming:
                if not isinstance(incoming["error_message"], str):
                    raise ProtocolAbort("invalid callback error message")
                raise CallbackError(
                    _positive_id(incoming["error_id"], "callback error"),
                    incoming["error_message"])
            value = _pyarg(incoming["value"])
            _reject_callback_or_deferred(value, result=True)
            return value


def _positive_id(value, label, allow_zero=False):
    if type(value) is not int or value < (0 if allow_zero else 1) or value > 9007199254740991:
        raise ProtocolAbort("invalid %s ID" % label)
    return value


def _contains_callback_descriptor(value):
    if type(value) is dict:
        if len(value) == 1 and "__lt_callback__" in value:
            return True
        return any(_contains_callback_descriptor(k) or _contains_callback_descriptor(v)
                   for k, v in value.items())
    if type(value) in (list, tuple):
        return any(_contains_callback_descriptor(x) for x in value)
    return False


def _argument(value, scope):
    if type(value) is dict and set(value) == {"__lt_callback__"}:
        token = _positive_id(value["__lt_callback__"], "callback token")
        if token not in scope.callback_tokens or token in scope.used_callback_tokens:
            raise ProtocolAbort("unknown or duplicate callback token")
        scope.used_callback_tokens.add(token)
        return CallbackProxy(token, scope)
    if _contains_callback_descriptor(value):
        raise ProtocolAbort("callback descriptor must be a direct positional argument")
    return _pyarg(value)


def _reject_callback_or_deferred(value, result=False, ancestors=None, depth=0):
    """Validate callback values without introspecting arbitrary host containers."""
    if depth > 64:
        label = "callback result" if result else "host callback"
        raise TypeError("%s container nesting exceeds 64 levels" % label)
    if isinstance(value, CallbackProxy):
        raise TypeError("callbacks cannot return Latent callable values" if result
                        else "returning a Latent callback is unsupported")
    if inspect.isawaitable(value) or inspect.isgenerator(value) or inspect.isasyncgen(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError("synchronous host callbacks do not support async or deferred results: %s" %
                        type(value).__name__)
    if result and callable(value):
        raise TypeError("callbacks cannot return callable values")
    if type(value) in (list, tuple):
        if ancestors is None:
            ancestors = set()
        identity = id(value)
        if identity in ancestors:
            label = "callback result" if result else "host callback"
            raise TypeError("cyclic %s containers are unsupported" % label)
        ancestors.add(identity)
        try:
            for item in value:
                _reject_callback_or_deferred(item, result, ancestors, depth + 1)
        finally:
            ancestors.remove(identity)
    elif type(value) is dict:
        if ancestors is None:
            ancestors = set()
        identity = id(value)
        if identity in ancestors:
            label = "callback result" if result else "host callback"
            raise TypeError("cyclic %s containers are unsupported" % label)
        ancestors.add(identity)
        try:
            for key, item in value.items():
                _reject_callback_or_deferred(key, result, ancestors, depth + 1)
                _reject_callback_or_deferred(item, result, ancestors, depth + 1)
        finally:
            ancestors.remove(identity)


def _encode_result(value, scope):
    if scope.proxies:
        _reject_callback_or_deferred(value, result=True)
    return encode(value)


def _decoded_args(raw, scope):
    if not isinstance(raw, list):
        raise ValueError("invalid request args schema")
    return [_argument(arg, scope) for arg in raw]


def execute(req, scope):
    if type(req) is not dict or not isinstance(req.get("op"), str):
        raise ProtocolAbort("invalid request payload schema")
    op = req["op"]
    if op == "call_bound_method":
        method_id = req.get("id")
        method = _bound_methods.get(method_id)
        if method is None:
            raise RuntimeError("stale Python bound-method ref: %s" % method_id)
        args = _decoded_args(req.get("args", []), scope)
        return {"value": _encode_result(method(*args), scope)}
    if op == "call_module_function":
        function_id = req.get("id")
        function = _module_functions.get(function_id)
        if function is None:
            raise RuntimeError("stale Python module-function ref: %s" % function_id)
        args = _decoded_args(req.get("args", []), scope)
        return {"value": _encode_result(function(*args), scope)}
    if op == "get_module_function":
        raw_target = req["target"]
        function = (module_function(getmod(raw_target["mod"]), req["attr"])
                    if "mod" in raw_target else None)
        if function is None:
            return {"module_function": None}
        function_id = _next_id[0]
        _next_id[0] += 1
        _module_functions[function_id] = function
        return {"module_function": {"id": function_id,
                "module": getmod(raw_target["mod"]).__name__, "name": req["attr"]}}
    if op == "get_bound_method":
        raw_target = req["target"]
        tgt = target(raw_target)
        method = None if "mod" in raw_target else bound_instance_method(tgt, req["attr"])
        if method is None:
            return {"bound_method": None}
        method_id = _next_id[0]
        _next_id[0] += 1
        _bound_methods[method_id] = method
        cls = type(tgt)
        return {"bound_method": {"id": method_id,
                "type": "%s.%s" % (cls.__module__, cls.__qualname__),
                "member": req["attr"]}}
    if op == "binop":
        args = [_pyarg(a) for a in req.get("args", [])]
        return {"value": _encode_result(getattr(args[0], req["name"])(*args[1:]), scope)}

    tgt = target(req["target"])
    if op == "get":
        value = getattr(tgt, req["attr"])
        return {"value": _encode_result(value, scope)}
    if op == "call":
        fn = getattr(tgt, req["attr"])
        args = _decoded_args(req.get("args", []), scope)
        return {"value": _encode_result(fn(*args), scope)}
    if op == "repr":
        return {"value": str(tgt)}
    if op == "getitem":
        key = _pyarg(req["key"])
        try:
            return {"value": _encode_result(tgt[key], scope)}
        except (IndexError, KeyError, TypeError) as exc:
            return {"error": "index failed: %s" % exc}
    if op == "setattr":
        setattr(tgt, req["attr"], _pyarg(req["value"]))
        return {"value": None}
    if op == "setitem":
        try:
            tgt[_pyarg(req["key"])] = _pyarg(req["value"])
            return {"value": None}
        except (IndexError, KeyError, TypeError) as exc:
            return {"error": "index-assign failed: %s" % exc}
    if op == "truthy":
        return {"value": bool(tgt)}
    raise ValueError("unknown op %r" % op)


def _protocol_streams():
    # Keep the protocol on private, non-inheritable descriptors, then redirect
    # process stdio itself. This prevents ordinary Python and native fd 0/1 IO
    # from stealing or corrupting frames. This is isolation for trusted host
    # libraries, not a sandbox: deliberate fd-table introspection is out of scope.
    sys.stdout.flush()
    in_fd = out_fd = null_fd = None
    protocol_in = protocol_out = None
    try:
        in_fd = os.dup(0)
        out_fd = os.dup(1)
        os.set_inheritable(in_fd, False)
        os.set_inheritable(out_fd, False)
        protocol_in = os.fdopen(in_fd, "r", encoding="utf-8", buffering=1)
        in_fd = None
        protocol_out = os.fdopen(out_fd, "w", encoding="utf-8", buffering=1)
        out_fd = None
        null_fd = os.open(os.devnull, os.O_RDWR)
        os.dup2(null_fd, 0, inheritable=True)
        os.dup2(2, 1, inheritable=True)
        os.close(null_fd)
        null_fd = None
    except BaseException:
        if protocol_in is not None:
            protocol_in.close()
        if protocol_out is not None:
            protocol_out.close()
        raise
    finally:
        for fd in (in_fd, out_fd, null_fd):
            if fd is not None:
                os.close(fd)
    class NoHostInput:
        def read(self, *args, **kwargs):
            raise RuntimeError("host input is unavailable in the Python daemon")
        def readline(self, *args, **kwargs):
            raise RuntimeError("host input is unavailable in the Python daemon")
        def __iter__(self):
            raise RuntimeError("host input is unavailable in the Python daemon")
    # `print` and `input` must never consume or corrupt the RPC pipes.
    sys.stdin = NoHostInput()
    sys.__stdin__ = sys.stdin
    sys.stdout = sys.stderr
    sys.__stdout__ = sys.stderr
    return protocol_in, protocol_out


_protocol_in, _protocol_out = _protocol_streams()


def _protocol_send(frame):
    try:
        _protocol_out.write(json.dumps(frame, separators=(",", ":"), allow_nan=False) + "\n")
        _protocol_out.flush()
    except BaseException as exc:
        raise ProtocolAbort("Python protocol write failed") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate protocol JSON key")
        result[key] = value
    return result


def _protocol_receive():
    line = _protocol_in.readline()
    if line == "":
        raise EOFError("Java closed protocol stream")
    try:
        frame = json.loads(line, object_pairs_hook=_unique_object)
    except BaseException as exc:
        raise ProtocolAbort("invalid protocol JSON") from exc
    if type(frame) is not dict:
        raise ProtocolAbort("protocol frame must be an object")
    return frame


def _valid_request_payload(payload):
    if type(payload) is not dict or not isinstance(payload.get("op"), str):
        return False
    op = payload["op"]
    schemas = {
        "get": ({"op", "target", "attr"},),
        "call": ({"op", "target", "attr", "args"}, {"op", "target", "attr"}),
        "repr": ({"op", "target"},),
        "truthy": ({"op", "target"},),
        "getitem": ({"op", "target", "key"},),
        "setattr": ({"op", "target", "attr", "value"},),
        "setitem": ({"op", "target", "key", "value"},),
        "call_bound_method": ({"op", "id", "args"}, {"op", "id"}),
        "call_module_function": ({"op", "id", "args"}, {"op", "id"}),
        "get_module_function": ({"op", "target", "attr"},),
        "get_bound_method": ({"op", "target", "attr"},),
        "binop": ({"op", "name", "args"},),
    }
    allowed = schemas.get(op)
    if allowed is None or set(payload) not in allowed:
        return False
    if "args" in payload and type(payload["args"]) is not list:
        return False
    if "target" in payload:
        target_value = payload["target"]
        if (type(target_value) is not dict or
                (set(target_value) != {"mod"} and set(target_value) != {"id"})):
            return False
        if "mod" in target_value and not isinstance(target_value["mod"], str):
            return False
        if "id" in target_value and (type(target_value["id"]) is not int or target_value["id"] < 1):
            return False
    if "attr" in payload and not isinstance(payload["attr"], str):
        return False
    return True


def _serve(frame):
    global _last_request_id
    if set(frame) - {"kind", "id", "parent", "release_refs", "payload"} or \
            not {"kind", "id", "parent", "payload"}.issubset(frame):
        raise ProtocolAbort("invalid request frame schema")
    if frame["kind"] != "request":
        raise ProtocolAbort("invalid request direction")
    request_id = _positive_id(frame["id"], "request")
    parent = _positive_id(frame["parent"], "request parent", allow_zero=True)
    expected_parent = _scopes[-1].id if _scopes else 0
    if parent != expected_parent or request_id <= _last_request_id:
        raise ProtocolAbort("request ID/parent mismatch")
    if not _valid_request_payload(frame["payload"]):
        raise ProtocolAbort("invalid request payload schema")
    if len(_scopes) >= 16:
        raise ProtocolAbort("Python RPC depth limit exceeded")
    refs = frame.get("release_refs", [])
    if (type(refs) is not list or any(type(ref) is not int or ref < 1 for ref in refs) or
            len(set(refs)) != len(refs)):
        raise ProtocolAbort("invalid release_refs schema")
    for ref in refs:
        if _objs.pop(ref, None) is None:
            raise ProtocolAbort("unknown or expired Python object ref %s" % ref)
    _last_request_id = request_id
    scope = Scope(request_id)
    raw_args = frame["payload"].get("args", [])
    for raw_arg in raw_args:
        if type(raw_arg) is dict and set(raw_arg) == {"__lt_callback__"}:
            if frame["payload"]["op"] not in ("call", "call_bound_method", "call_module_function"):
                raise ProtocolAbort("callback descriptor is not valid for this operation")
            token = _positive_id(raw_arg["__lt_callback__"], "callback token")
            if token in scope.callback_tokens:
                raise ProtocolAbort("duplicate callback token")
            scope.callback_tokens.add(token)
        elif _contains_callback_descriptor(raw_arg):
            raise ProtocolAbort("callback descriptor must be a direct positional argument")
    _scopes.append(scope)
    try:
        try:
            payload = execute(frame["payload"], scope)
        except CallbackError as exc:
            payload = {"callback_error": _positive_id(exc.error_id, "callback error")}
        except Exception:
            payload = {"error": traceback.format_exc()}
        _protocol_send({"kind": "response", "id": request_id,
                        "parent": parent, "payload": payload})
    finally:
        scope.active = False
        scope.proxies.clear()
        scope.invocations.clear()
        scope.callback_tokens.clear()
        scope.used_callback_tokens.clear()
        if _scopes and _scopes[-1] is scope:
            _scopes.pop()


def _legacy_request(req):
    if req == {"op": "quit"}:
        return None
    # Compatibility for old non-callback raw requests only.
    if _contains_callback_descriptor(req):
        return {"error": "legacy protocol does not support callbacks"}
    scope = Scope(0)
    try:
        return execute(req, scope)
    except CallbackError:
        return {"error": "legacy protocol cannot transport callback failures"}
    except Exception:
        return {"error": traceback.format_exc()}
    finally:
        scope.active = False
        scope.proxies.clear()


def main():
    while True:
        try:
            incoming = _protocol_receive()
        except EOFError:
            return
        if incoming.get("kind") == "request":
            _serve(incoming)
            continue
        if "kind" in incoming:
            raise ValueError("invalid protocol request direction")
        response = _legacy_request(incoming)
        if response is None:
            return
        _protocol_send(response)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)
