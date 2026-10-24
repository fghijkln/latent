"""Latent -> Python backend."""
import json
from nodes import *

PRELUDE = r'''
import builtins as _builtins
import importlib as _importlib
import atexit as _atexit
import json as _json
import math as _math
import os as _os
import shutil as _shutil
import subprocess as _subprocess
import inspect as _inspect
import threading as _threading
import sys as _sys
import traceback as _traceback
import types as _types


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
        # Match LtRt.numStr: only render exactly integral values as decimal
        # integers below 1e16. Larger values use shortest float notation.
        if v.is_integer() and abs(v) < 1e16:
            return str(int(v))
        # NumPy 2.x reprs scalars as ``np.float64(...)``; normalize to the
        # language's float64 spelling used by the Java backend.
        return repr(float(v))
    if isinstance(v, str):
        return v
    if isinstance(v, _LazyMod):
        return "<module %s>" % v.__dict__["_name"]
    if isinstance(v, _LtClass):
        return "<class %s>" % v._name
    if isinstance(v, _LtFunction):
        if v._display is not None:
            return v._display
        return "<function %s>" % v._name
    if isinstance(v, _InteropBoundMethod):
        return v._display
    if isinstance(v, (_InteropModuleFunction, _InteropStaticMethod)):
        return v._display
    if isinstance(v, _LtObj):
        return "<%s object>" % v._lt_class._name
    if isinstance(v, _JClass):
        return "<class %s>" % v._name
    if isinstance(v, _JHandle):
        jvm = _JVM.inst()
        return jvm.req({"op": "repr", "target": jvm.target(v)})
    if type(v) is list or type(v) is tuple:
        # tuple prints as a list: over the interop boundary a tuple
        # arrives as a list anyway, so both backends agree.
        return "[" + ", ".join(_wv_repr_q(x) for x in v) + "]"
    if type(v) is dict:
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
    _builtins.print(_wv_repr(v))


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
        if a < 0 and _math.isfinite(b) and not b.is_integer():
            return _math.nan
        # Java Math.pow defines unit bases raised to either infinity as NaN;
        # Python math.pow instead returns 1.0 for (-1.0) ** infinity.
        if abs(a) == 1.0 and _math.isinf(b):
            return _math.nan
        # Java Math.pow returns infinity for zero to a negative power and on
        # overflow; Python's float power raises instead.
        if a == 0 and b < 0:
            odd_negative = (_math.copysign(1.0, a) < 0 and b.is_integer() and
                            int(abs(b)) % 2 == 1)
            return -_math.inf if odd_negative else _math.inf
        try:
            return _math.pow(a, b)
        except OverflowError:
            odd_negative = (a < 0 and b.is_integer() and
                            int(abs(b)) % 2 == 1)
            return -_math.inf if odd_negative else _math.inf
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
    if isinstance(a, list):
        return _builtins.len(a) == _builtins.len(b) and all(
            _wv_eq(a[i], b[i]) for i in range(_builtins.len(a)))
    if isinstance(a, dict):
        if _builtins.len(a) != _builtins.len(b) or any(k not in b for k in a):
            return False
        return all(_wv_eq(a[k], b[k]) for k in a)
    return a == b


def _wv_cmp(a, b):
    if isinstance(a, float) and isinstance(b, float):
        if _math.isnan(a) or _math.isnan(b):
            return _math.nan
        return (a > b) - (a < b)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    raise TypeError("bad comparison: %s, %s" % (_wv_repr(a), _wv_repr(b)))


def _wv_len(x):
    if isinstance(x, (list, dict, str)):
        return _builtins.float(_builtins.len(x))
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


def _wv_idx(k, n):
    if isinstance(k, bool) or not isinstance(k, float) or not k.is_integer():
        raise TypeError("index must be an integer")
    i = int(k)
    if i < 0:
        i += n
    if i < 0 or i >= n:
        raise IndexError("index out of range: " + str(int(k)))
    return i


def _wv_index(v, k):
    # xs[i] / m[k] / s[i]; negative indices count from the end.
    if isinstance(v, _JHandle):
        jvm = _JVM.inst()
        return jvm.req({"op": "getitem", "target": jvm.target(v),
                        "key": _jvm_encode(k)})
    if isinstance(v, list):
        return v[_wv_idx(k, _builtins.len(v))]
    if isinstance(v, dict):
        if k in v:
            return v[k]
        raise KeyError("key not found: " + _wv_repr(k))
    if isinstance(v, str):
        return v[_wv_idx(k, _builtins.len(v))]
    # exotic natives (e.g. a tuple straight from a py call): best effort
    try:
        return v[int(k)] if isinstance(k, float) else v[k]
    except (IndexError, KeyError, TypeError):
        raise TypeError("cannot index " + _wv_repr(v))


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
    """Convert integral floats for Python APIs without erasing negative zero."""
    if isinstance(v, (_LtFunction, _InteropBoundMethod,
                      _InteropModuleFunction, _InteropStaticMethod)):
        raise TypeError("Latent callable values cannot be passed to Python/Java interop calls")
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e18:
        if v != 0.0 or _math.copysign(1.0, v) > 0:
            return int(v)
    if isinstance(v, list):
        return [_wv_pyarg(x) for x in v]
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if isinstance(k, (_LtFunction, _InteropBoundMethod,
                              _InteropModuleFunction, _InteropStaticMethod)):
                raise TypeError("Latent callable values cannot be passed to Python/Java interop calls")
            out[k] = _wv_pyarg(x)
        return out
    return v


def _wv_pyresult(v):
    """Normalize Python integer results to Latent's single numeric type."""
    if _builtins.isinstance(v, _builtins.int) and not _builtins.isinstance(
            v, _builtins.bool):
        return _builtins.float(v)
    return v


_WV_CALLBACK_MAX_DEPTH = 16
_WV_CALLBACK_MAX_CONTAINER_DEPTH = 64
_wv_callback_local = _threading.local()


class _WVCallbackScope:
    """A synchronous, same-thread lifetime for adapters passed to one host call."""
    def __init__(self):
        self.owner = _threading.get_ident()
        self.active = True
        self.adapters = []

    def close(self):
        self.active = False
        for adapter in self.adapters:
            adapter.function = None
        self.adapters.clear()


def _wv_is_latent_callable(value):
    return isinstance(value, (_LtFunction, _InteropBoundMethod,
                              _InteropModuleFunction, _InteropStaticMethod,
                              _WVCallback))


def _wv_callback_is_deferred(value):
    return (_inspect.isawaitable(value) or _inspect.isgenerator(value) or
            _inspect.isasyncgen(value))


def _wv_callback_reject_deferred(value):
    if _wv_callback_is_deferred(value):
        if _inspect.iscoroutine(value):
            value.close()
        raise TypeError("synchronous host callbacks do not support async or deferred results")
    return value


def _wv_callback_to_latent(value, ancestors=None, depth=0):
    """Normalize host callback values; preserve non-container opaque objects."""
    if depth > _WV_CALLBACK_MAX_CONTAINER_DEPTH:
        raise TypeError("host callback container nesting exceeds 64 levels")
    if _wv_is_latent_callable(value):
        raise TypeError("nested or returned Latent callbacks are unsupported")
    if isinstance(value, bool) or value is None or isinstance(value, (float, str)):
        return value
    if isinstance(value, int):
        return float(value)
    if type(value) not in (list, tuple, dict):
        return value
    if ancestors is None:
        ancestors = set()
    identity = id(value)
    if identity in ancestors:
        raise TypeError("cyclic host callback containers are unsupported")
    ancestors.add(identity)
    try:
        if type(value) in (list, tuple):
            return [_wv_callback_to_latent(item, ancestors, depth + 1)
                    for item in value]
        converted = {}
        for key, item in value.items():
            _wv_callback_to_latent(key, ancestors, depth + 1)
            converted[key] = _wv_callback_to_latent(item, ancestors, depth + 1)
        return converted
    finally:
        ancestors.remove(identity)


def _wv_callback_to_host(value, ancestors=None, depth=0):
    """Apply _wv_pyarg numeric rules with bounded, cycle-safe container walking."""
    if depth > _WV_CALLBACK_MAX_CONTAINER_DEPTH:
        raise TypeError("callback result container nesting exceeds 64 levels")
    _wv_callback_reject_deferred(value)
    if _wv_is_latent_callable(value):
        raise TypeError("callbacks cannot return Latent callable values")
    if callable(value):
        raise TypeError("callbacks cannot return callable values")
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e18:
        if value != 0.0 or _math.copysign(1.0, value) > 0:
            return int(value)
    if type(value) not in (list, tuple, dict):
        return value
    if ancestors is None:
        ancestors = set()
    identity = id(value)
    if identity in ancestors:
        raise TypeError("cyclic callback result containers are unsupported")
    ancestors.add(identity)
    try:
        if type(value) in (list, tuple):
            return [_wv_callback_to_host(item, ancestors, depth + 1)
                    for item in value]
        converted = {}
        for key, item in value.items():
            _wv_callback_to_host(key, ancestors, depth + 1)
            converted[key] = _wv_callback_to_host(item, ancestors, depth + 1)
        return converted
    finally:
        ancestors.remove(identity)


class _WVCallback:
    """Short-lived Python callable that enters Latent through its normal binder."""
    def __init__(self, function, scope):
        self.function = function
        self.scope = scope
        scope.adapters.append(self)

    def __call__(self, *args, **kwargs):
        scope = self.scope
        if not scope.active:
            raise TypeError("host callback scope expired; retained callbacks are unsupported")
        if _threading.get_ident() != scope.owner:
            raise TypeError("cross-thread host callbacks are unsupported")
        if kwargs:
            raise _LtArgumentError("host callback keyword arguments are unsupported")
        depth = getattr(_wv_callback_local, "depth", 0)
        if depth >= _WV_CALLBACK_MAX_DEPTH:
            raise TypeError("host callback nesting exceeds 16 levels")
        function = self.function
        if function is None:
            raise TypeError("host callback scope expired; retained callbacks are unsupported")
        _wv_callback_local.depth = depth + 1
        try:
            latent_args = [_wv_callback_to_latent(arg) for arg in args]
            result = _wv_call(function, *latent_args)
            _wv_callback_reject_deferred(result)
            return _wv_callback_to_host(result)
        finally:
            _wv_callback_local.depth = depth


def _wv_pyhost_call(function, args):
    """Invoke a Python host callable with ordered Latent positional/named args."""
    _wv_reject_interop_args(tuple(arg for arg in args
                                  if isinstance(arg, (_LtStarArg,
                                                      _LtStarStarArg))))
    seen_names = set()
    has_callback = False
    for arg in args:
        if isinstance(arg, _LtNamedArg):
            if arg.name in seen_names:
                raise _LtArgumentError("got duplicate named argument %r" %
                                       arg.name)
            seen_names.add(arg.name)
            value = arg.value
        else:
            value = arg
        if isinstance(value, _LtFunction):
            has_callback = True

    if not has_callback:
        converted_args = []
        converted_kwargs = {}
        for arg in args:
            if isinstance(arg, _LtNamedArg):
                converted_kwargs[arg.name] = _wv_pyarg(arg.value)
            else:
                converted_args.append(_wv_pyarg(arg))
        return _wv_pyresult(function(*converted_args, **converted_kwargs))
    # Python callables cannot reveal when a host stores or schedules them. The
    # adapters are therefore made unusable at scope exit, not at storage time.
    scope = _WVCallbackScope()
    try:
        converted_args = []
        converted_kwargs = {}
        for arg in args:
            named = isinstance(arg, _LtNamedArg)
            value = arg.value if named else arg
            if isinstance(value, _LtFunction):
                converted = _WVCallback(value, scope)
            else:
                converted = _wv_callback_to_host(value)
            if named:
                converted_kwargs[arg.name] = converted
            else:
                converted_args.append(converted)
        result = function(*converted_args, **converted_kwargs)
        _wv_callback_reject_deferred(result)
        _wv_callback_to_host(result)  # Reject returned adapters, nested callables, cycles, and excessive depth.
        return _wv_pyresult(result)
    finally:
        scope.close()


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


class _LtObj:
    """Latent instance with one shared field store and explicit class metadata."""
    __slots__ = ("_lt_class", "_lt_fields")

    def __init__(self, cls):
        self._lt_class = cls
        self._lt_fields = {}


class _LtClass:
    """A Latent class with its own method table and an optional parent."""
    def __init__(self, name, parent, methods):
        self._name = name
        self._parent = parent
        self._methods = methods

    def _find_method(self, name):
        cls = self
        while cls is not None:
            method = cls._methods.get(name)
            if method is not None:
                return method
            cls = cls._parent
        return None

    def _new(self, *args):
        o = _LtObj(self)
        init = self._find_method("init")
        if init is not None:
            # Latent-level dispatch: values stay Latent (no _wv_pyarg;
            # that int conversion is only for calling real Python functions)
                init(o, *_wv_bind_args(
                    _wv_method_params(init), _wv_method_required(init), args,
                    self._name + ".new", _LtFunction.MISSING,
                    _wv_method_rest(init), _wv_method_extra(init),
                    _wv_method_position_count(init),
                    _wv_method_required_keyword_only(init)))
        elif args:
            _wv_bind_args((), 0, args, self._name + ".new",
                          _LtFunction.MISSING)
        return o


class _LtArgumentError(RuntimeError):
    pass


class _LtJavaInteropError(RuntimeError):
    pass


class _LtNamedArg:
    __slots__ = ("name", "value")

    def __init__(self, name, value):
        self.name = name
        self.value = value


def _wv_named(name, value):
    return _LtNamedArg(name, value)


class _LtStarArg:
    __slots__ = ("values",)

    def __init__(self, values):
        self.values = tuple(values)


class _LtStarStarArg:
    __slots__ = ("items",)

    def __init__(self, items):
        self.items = tuple(items)


def _wv_star(value):
    if not isinstance(value, list):
        raise _LtArgumentError("* unpacking requires a Latent list")
    return _LtStarArg(value)


def _wv_starstar(value):
    if not isinstance(value, dict):
        raise _LtArgumentError("** unpacking requires a Latent map")
    items = list(value.items())
    for key, _ in items:
        if not isinstance(key, str):
            raise _LtArgumentError("** unpacking requires string keys")
    return _LtStarStarArg(items)


def _wv_expand_args(args):
    expanded = []
    for arg in args:
        if isinstance(arg, _LtStarArg):
            expanded.extend(arg.values)
        elif isinstance(arg, _LtStarStarArg):
            expanded.extend(_LtNamedArg(key, value)
                            for key, value in arg.items)
        else:
            expanded.append(arg)
    return expanded


def _wv_has_named(args):
    return any(isinstance(arg, _LtNamedArg) for arg in args)


def _wv_has_unpack(args):
    return any(isinstance(arg, (_LtStarArg, _LtStarStarArg)) for arg in args)


def _wv_reject_interop_args(args):
    if _wv_has_named(args):
        raise _LtArgumentError(
            "named arguments are not supported for Python/Java interop calls")
    if _wv_has_unpack(args):
        raise _LtArgumentError(
            "argument unpacking is not supported for Python/Java interop calls")


def _wv_bind_args(params, required_count, args, name, missing,
                  rest_name=None, extra_name=None, positional_count=None,
                  required_keyword_only=()):
    params = tuple(params)
    positional_count = (_builtins.len(params) if positional_count is None
                        else positional_count)
    required_keyword_only = tuple(required_keyword_only)
    args = _wv_expand_args(args)
    if not _wv_has_named(args):
        if _builtins.len(args) > positional_count and rest_name is None:
            raise _LtArgumentError("%s() takes %d args, got %d" %
                                   (name, positional_count, _builtins.len(args)))
        if _builtins.len(args) < required_count:
            if required_count == positional_count:
                raise _LtArgumentError("%s() takes %d args, got %d" %
                                       (name, positional_count,
                                        _builtins.len(args)))
            raise _LtArgumentError("%s() missing required argument %r" %
                                   (name, params[_builtins.len(args)]))
        values = [missing] * _builtins.len(params)
        for index, value in enumerate(args[:positional_count]):
            values[index] = value
        for parameter in required_keyword_only:
            raise _LtArgumentError(
                "%s() missing required keyword-only argument %r" %
                (name, parameter))
        if rest_name is not None:
            values.append(list(args[positional_count:]))
        if extra_name is not None:
            values.append({})
        return values
    values = [missing] * _builtins.len(params)
    supplied = [False] * _builtins.len(params)
    rest_values = []
    extra_values = {}
    pos = 0
    seen_names = set()
    for arg in args:
        if isinstance(arg, _LtNamedArg):
            if arg.name in seen_names:
                raise _LtArgumentError("%s() got duplicate named argument %r" %
                                       (name, arg.name))
            seen_names.add(arg.name)
            if arg.name not in params:
                if extra_name is None:
                    raise _LtArgumentError(
                        "%s() got unexpected named argument %r" %
                        (name, arg.name))
                extra_values[arg.name] = arg.value
            else:
                index = params.index(arg.name)
                if supplied[index]:
                    raise _LtArgumentError(
                        "%s() got multiple values for argument %r" %
                        (name, arg.name))
                values[index] = arg.value
                supplied[index] = True
        else:
            if pos >= positional_count:
                if rest_name is None:
                    raise _LtArgumentError("%s() takes %d args, got %d" %
                                           (name, positional_count, pos + 1))
                rest_values.append(arg)
            else:
                values[pos] = arg
                supplied[pos] = True
            pos += 1
    for index in range(required_count):
        if not supplied[index]:
            raise _LtArgumentError("%s() missing required argument %r" %
                                   (name, params[index]))
    for parameter in required_keyword_only:
        index = params.index(parameter)
        if not supplied[index]:
            raise _LtArgumentError(
                "%s() missing required keyword-only argument %r" %
                (name, parameter))
    if rest_name is not None:
        values.append(rest_values)
    if extra_name is not None:
        values.append(extra_values)
    return values


def _wv_method_params(method):
    return tuple(getattr(method, "_lt_params",
                         method.__code__.co_varnames[1:method.__code__.co_argcount]))


def _wv_method_required(method):
    return getattr(method, "_lt_required_count",
                    _builtins.len(_wv_method_params(method)))


def _wv_method_position_count(method):
    return getattr(method, "_lt_positional_count",
                    _builtins.len(_wv_method_params(method)))


def _wv_method_required_keyword_only(method):
    return tuple(getattr(method, "_lt_required_keyword_only", ()))


def _wv_method_rest(method):
    return getattr(method, "_lt_rest_name", None)


def _wv_method_extra(method):
    return getattr(method, "_lt_extra_name", None)


class _LtFunction:
    """A first-class Latent function with backend-independent arity checks."""
    MISSING = object()
    __slots__ = ("_fn", "_params", "_required", "_positional",
                 "_required_keyword_only", "_arity", "_name", "_display",
                 "_rest", "_extra")

    def __init__(self, fn, params, name, display=None, required_count=None,
                 rest_name=None, extra_name=None, positional_count=None,
                 required_keyword_only=()):
        self._fn = fn
        self._params = tuple(params)
        self._required = (_builtins.len(self._params) if required_count is None
                          else required_count)
        self._positional = (_builtins.len(self._params) if positional_count is None
                            else positional_count)
        self._required_keyword_only = tuple(required_keyword_only)
        self._arity = _builtins.len(self._params)
        self._name = name
        self._display = display
        self._rest = rest_name
        self._extra = extra_name

    def __call__(self, *args):
        return self._fn(*_wv_bind_args(self._params, self._required, args,
                                        self._name, self.MISSING,
                                        self._rest, self._extra,
                                        self._positional,
                                        self._required_keyword_only))


class _InteropBoundMethod:
    """A supported host instance method captured as a Latent callable."""
    __slots__ = ("_kind", "_target", "_method", "_display")

    def __init__(self, kind, target, method, display):
        self._kind = kind
        self._target = target
        self._method = method
        self._display = display


class _InteropModuleFunction:
    """A captured Python module-owned function exposed as a Latent callable."""
    __slots__ = ("_function", "_display")

    def __init__(self, function, display):
        self._function = function
        self._display = display


class _InteropStaticMethod:
    """A remote Java static method family captured as a Latent callable."""
    __slots__ = ("_id", "_display")

    def __init__(self, ref_id, display):
        self._id = ref_id
        self._display = display


def _wv_python_bound_method(receiver, attr, value):
    """Return a stable display for a genuine built-in/Python instance method."""
    try:
        descriptor = _inspect.getattr_static(receiver, attr)
    except AttributeError:
        return None
    if type(descriptor) is _types.FunctionType:
        if type(value) is not _types.MethodType or value.__self__ is not receiver:
            return None
    elif type(descriptor) in (getattr(_types, "MethodDescriptorType", object),
                              getattr(_types, "WrapperDescriptorType", object)):
        if not isinstance(value, _types.BuiltinMethodType) or value.__self__ is not receiver:
            return None
    else:
        return None
    cls = type(receiver)
    qualified_type = "%s.%s" % (cls.__module__, cls.__qualname__)
    return qualified_type + "." + attr


def _wv_call(fn, *args):
    if isinstance(fn, _LtFunction):
        return fn(*args)
    if isinstance(fn, _InteropBoundMethod):
        if fn._kind == "python":
            return _wv_pyhost_call(fn._target, args)
        _wv_reject_interop_args(args)
        if fn._kind == "java":
            jvm = _JVM.inst()
            return jvm.req({"op": "call", "target": jvm.target(fn._target),
                            "method": fn._method,
                            "args": [_jvm_encode(a) for a in args]})
        raise TypeError("invalid interop bound method")
    if isinstance(fn, _InteropModuleFunction):
        return _wv_pyhost_call(fn._function, args)
    if isinstance(fn, _InteropStaticMethod):
        _wv_reject_interop_args(args)
        jvm = _JVM.inst()
        return jvm.req({"op": "call_static_method", "id": fn._id,
                        "args": [_jvm_encode(a) for a in args]})
    raise TypeError("call on non-function value")


def _jvm_encode(v):
    if isinstance(v, (_LtFunction, _InteropBoundMethod,
                      _InteropModuleFunction, _InteropStaticMethod)):
        raise TypeError("Latent callable values cannot be passed to Python/Java interop calls")
    if isinstance(v, _JHandle):
        return {"__jref": v._id}
    if isinstance(v, _JClass):
        return {"__jclass": v._name}
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        if _math.isnan(v):
            return {"__num": "nan"}
        if _math.isinf(v):
            return {"__num": "inf" if v > 0 else "-inf"}
        return v
    if isinstance(v, str):
        return v
    if v is None:
        return None
    if isinstance(v, list):
        return [_jvm_encode(x) for x in v]
    if isinstance(v, dict):
        entries = []
        for k, x in v.items():
            if isinstance(k, (_LtFunction, _InteropBoundMethod,
                              _InteropModuleFunction, _InteropStaticMethod)):
                raise TypeError("Latent callable values cannot be passed to Python/Java interop calls")
            entries.append([str(k), _jvm_encode(x)])
        return {"__map": entries}
    raise TypeError("cannot send to JVM: " + _wv_repr(v))


def _jvm_decode(v):
    if isinstance(v, dict):
        if _builtins.len(v) == 1 and "__map" in v:
            entries = v["__map"]
            if not isinstance(entries, list):
                raise ValueError("invalid JVM map envelope")
            result = {}
            for pair in entries:
                if not isinstance(pair, list) or _builtins.len(pair) != 2:
                    raise ValueError("invalid JVM map entry")
                result[str(pair[0])] = _jvm_decode(pair[1])
            return result
        if _builtins.len(v) == 1 and "__jref" in v:
            return _JHandle(int(v["__jref"]))
        if _builtins.len(v) == 1 and "__jclass" in v:
            return _JClass(v["__jclass"])
        if _builtins.len(v) == 1 and "__num" in v:
            return {"nan": _math.nan, "inf": _math.inf,
                    "-inf": -_math.inf}[v["__num"]]
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
        cls = _os.path.join(cp, "LtJavaDaemon.class")
        srcs = [s for s in ("LtJavaDaemon.java", "JReflect.java", "LtRt.java")
                if _os.path.exists(_os.path.join(cp, s))]
        if not srcs:
            raise RuntimeError(
                "Java runtime sidecar sources not found next to " + cp)
        if _os.path.exists(cls) and srcs:
            # recompile when any runtime source is newer than the class
            # (stale classes silently ignore runtime fixes otherwise)
            cls_mtime = _os.path.getmtime(cls)
            if all(_os.path.getmtime(_os.path.join(cp, s)) <= cls_mtime
                   for s in srcs):
                return
        javac = _shutil.which("javac")
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
            error = resp["error"]
            first_line = error.splitlines()[0]
            if first_line.startswith(
                    "java.lang.RuntimeException: java: ambiguous "):
                message = first_line.partition(": ")[2]
                raise _LtJavaInteropError(message)
            raise RuntimeError("java error: " + resp["error"])
        if "bound_method" in resp:
            method = resp["bound_method"]
            return _InteropBoundMethod(
                "java", _JHandle(int(method["id"])), method["method"],
                "<bound method %s.%s>" % (method["type"], method["method"]))
        if "static_method" in resp:
            method = resp["static_method"]
            return _InteropStaticMethod(
                int(method["id"]),
                "<static method %s.%s>" %
                (method["type"], method["method"]))
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

def _wv_non_handle_type(h):
    """Return Latent's display type for values without host handles."""
    native_types = {type(None): "nil", bool: "bool", float: "num",
                    str: "str", list: "list", dict: "map"}
    kind = native_types.get(type(h))
    if kind is not None:
        return kind
    if isinstance(h, (_LtFunction, _InteropBoundMethod,
                      _InteropModuleFunction, _InteropStaticMethod,
                      _WVCallback)):
        return "function"
    return None


def _wv_wgetattr(h, attr):
    if isinstance(h, _JClass):
        jvm = _JVM.inst()
        return jvm.req({"op": "get", "target": jvm.target(h), "field": attr})
    if isinstance(h, _JHandle):
        jvm = _JVM.inst()
        return jvm.req({"op": "get", "target": jvm.target(h), "field": attr})
    if isinstance(h, _LazyMod):
        module = h._load()
        value = getattr(module, attr)
        namespace = vars(module)
        if (type(module) is _types.ModuleType and attr in namespace and
                namespace[attr] is value and
                (type(value) is _types.FunctionType or
                 (type(value) is _types.BuiltinFunctionType and
                  module.__name__ != "builtins" and
                  value.__self__ is module))):
            return _InteropModuleFunction(
                value, "<module function %s.%s>" %
                (module.__name__, attr))
        return _wv_pyresult(value)
    if isinstance(h, _LtObj):
        if attr in h._lt_fields:
            return h._lt_fields[attr]
        method = h._lt_class._find_method(attr)
        if method is not None:
            params = _wv_method_params(method)
            return _LtFunction(
                lambda *args: method(h, *args), params, attr,
                "<bound method %s.%s>" % (h._lt_class._name, attr),
                required_count=_wv_method_required(method),
                rest_name=_wv_method_rest(method),
                extra_name=_wv_method_extra(method),
                positional_count=_wv_method_position_count(method),
                required_keyword_only=
                    _wv_method_required_keyword_only(method))
        raise AttributeError("no field %r" % attr)
    if isinstance(h, _LtClass):
        raise AttributeError("class %s has no fields" % h._name)
    # Latent's native scalars and containers are not Python objects to inspect.
    # Keep opaque Python/Java handles above on their explicit interop paths.
    kind = _wv_non_handle_type(h)
    if kind is not None:
        raise TypeError("attribute access on non-handle value: " + kind)
    value = getattr(h, attr)
    display_member = _wv_python_bound_method(h, attr, value)
    if display_member is not None:
        return _InteropBoundMethod(
            "python", value, attr,
            "<bound method %s>" % display_member)
    return _wv_pyresult(value)


def _wv_wcall(h, attr, *args):
    if isinstance(h, (_JClass, _JHandle)):
        _wv_reject_interop_args(args)
        jvm = _JVM.inst()
        jargs = [_jvm_encode(a) for a in args]
        if isinstance(h, _JClass) and attr == "new":
            return jvm.req({"op": "new", "class": h._name, "args": jargs})
        return jvm.req({"op": "call", "target": jvm.target(h),
                        "method": attr, "args": jargs})
    if isinstance(h, _LtClass):
        if attr == "new":
            return h._new(*args)
        raise AttributeError("no class-level method %r" % attr)
    if isinstance(h, _LtObj):
        m = h._lt_class._find_method(attr)
        if m is None:
            raise AttributeError("no method %r" % attr)
        return _LtFunction(lambda *values: m(h, *values),
                           _wv_method_params(m), attr,
                           required_count=_wv_method_required(m),
                           rest_name=_wv_method_rest(m),
                           extra_name=_wv_method_extra(m),
                           positional_count=_wv_method_position_count(m),
                           required_keyword_only=
                               _wv_method_required_keyword_only(m))(*args)
    if isinstance(h, _LazyMod):
        return _wv_pyhost_call(getattr(h, attr), args)
    # Method-call syntax is lowered directly to __wcall (not __wgetattr), so
    # guard native Latent values and Latent callables here as well.
    kind = _wv_non_handle_type(h)
    if kind is not None:
        raise TypeError("call on non-handle value: " + kind)
    return _wv_pyhost_call(getattr(h, attr), args)


def _wv_supercall(receiver, owner, attr, *args):
    if not isinstance(receiver, _LtObj) or not isinstance(owner, _LtClass):
        raise TypeError("super call requires a Latent instance and class")
    cls = receiver._lt_class
    while cls is not None and cls is not owner:
        cls = cls._parent
    if cls is None or owner._parent is None:
        raise TypeError("super call owner is not in the instance inheritance chain")
    method = owner._parent._find_method(attr)
    if method is None:
        raise AttributeError("no parent method %r on class %s" %
                             (attr, owner._name))
    return _LtFunction(lambda *values: method(receiver, *values),
                       _wv_method_params(method), attr,
                       required_count=_wv_method_required(method),
                       rest_name=_wv_method_rest(method),
                       extra_name=_wv_method_extra(method),
                       positional_count=_wv_method_position_count(method),
                       required_keyword_only=
                           _wv_method_required_keyword_only(method))(*args)


def _wv_wsetattr(h, attr, v):
    if isinstance(h, _LtObj):
        h._lt_fields[attr] = v
        return None
    if isinstance(h, _LtClass):
        raise AttributeError("cannot set attribute on a class")
    if isinstance(h, (_JClass, _JHandle)):
        jvm = _JVM.inst()
        return jvm.req({"op": "set", "target": jvm.target(h),
                        "field": attr, "value": _jvm_encode(v)})
    if isinstance(h, _LazyMod):
        raise AttributeError("cannot set attribute on a module")
    kind = _wv_non_handle_type(h)
    if kind is not None:
        raise TypeError("cannot set attribute on " + kind)
    setattr(h, attr, _wv_pyarg(v))
    return None


def _wv_wsetindex(h, k, v):
    if isinstance(h, _JHandle):
        jvm = _JVM.inst()
        return jvm.req({"op": "setitem", "target": jvm.target(h),
                        "key": _jvm_encode(k), "value": _jvm_encode(v)})
    if isinstance(h, _LtObj):
        raise TypeError("cannot index-assign an object; set a field instead")
    if isinstance(h, list):
        h[_wv_idx(k, _builtins.len(h))] = v
        return None
    if isinstance(h, dict):
        h[k] = v
        return None
    # exotic natives (e.g. an ndarray straight from a py call): best effort,
    # mirroring _wv_index's read fallback so both backends agree
    try:
        h[int(k) if isinstance(k, float) else k] = _wv_pyarg(v)
        return None
    except (IndexError, KeyError, TypeError, AttributeError):
        pass
    raise TypeError("cannot index-assign " + _wv_repr(h))


def _wv_report_uncaught(exc, source_map, source_file, generated_file):
    """Render concise Latent diagnostics without exposing host tracebacks."""
    def _summary():
        try:
            error_type = type(exc).__name__
        except Exception:
            error_type = "Exception"
        try:
            message = str(exc)
        except Exception:
            message = "<message unavailable>"
        return error_type + (": " + message if message else "")

    def _fallback():
        _builtins.print("Latent runtime error: " + _summary(), file=_sys.stderr)

    try:
        generated_abs = _os.path.abspath(generated_file)
        mapped = []
        tb = exc.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            filename = frame.f_code.co_filename
            if (filename == generated_file or
                    _os.path.abspath(filename) == generated_abs):
                source_loc = source_map.get(tb.tb_lineno)
                if source_loc:
                    if isinstance(source_loc, tuple):
                        source_file, source_line = source_loc
                    else:  # compatibility with single-file generated programs
                        source_file, source_line = source_file, source_loc
                    name = frame.f_code.co_name
                    if name.startswith("_lt_user_"):
                        try:
                            name = bytes.fromhex(name[len("_lt_user_"):]).decode("utf-8")
                        except (ValueError, UnicodeDecodeError):
                            pass
                    mapped.append((source_file, source_line,
                                   "main" if name == "<module>" else name))
            tb = tb.tb_next
        if not mapped:
            _fallback()
            return

        if isinstance(exc, _LtArgumentError):
            error_type = "ArgumentError"
        elif isinstance(exc, _LtJavaInteropError):
            error_type = "RuntimeException"
        else:
            error_type = type(exc).__name__
        try:
            message = str(exc)
        except Exception:
            message = "<message unavailable>"
        detail = error_type + (": " + message if message else "")
        _builtins.print("Latent runtime error: " + detail, file=_sys.stderr)
        _builtins.print("Latent traceback (most recent call last):", file=_sys.stderr)
        for filename, line, name in mapped:
            _builtins.print(f"  at {filename}:{line} in {name}", file=_sys.stderr)
    except Exception:
        # Diagnostics must never replace the original failure.
        try:
            _fallback()
        except Exception:
            try:
                _builtins.print("Latent runtime error (diagnostic formatting failed)",
                                file=_sys.stderr)
            except Exception:
                pass

'''

BUILTIN_PY = {
    "__say": "_wv_say", "len": "_wv_len", "range": "_wv_range",
    "str": "_wv_str", "int": "_wv_int", "push": "_wv_push",
    "keys": "_wv_keys", "__wgetattr": "_wv_wgetattr", "__wcall": "_wv_wcall",
    "__index": "_wv_index", "__wsetattr": "_wv_wsetattr",
    "__wsetindex": "_wv_wsetindex",
}


class Gen:
    def __init__(self, source_path="<src>"):
        self.out = []
        self.indent = 0
        self._physical_line_count = 0
        self.source_path = source_path
        self.source_map = {}
        self._used_internal_names = set()
        self._python_name_map = {}
        self._reserved_nodes = set()
        self._missing_name = None
        self._builtins_name = None
        self._runtime_name = None
        self._runtime_error_name = None
        self._source_file_name = None
        self._source_map_name = None

    def _reserve_source_names(self, value):
        def reserve(name):
            self._used_internal_names.add(self._encoded_source_ident(name))

        if isinstance(value, Node):
            if id(value) in self._reserved_nodes:
                return
            self._reserved_nodes.add(id(value))
            if isinstance(value, Name):
                reserve(value.id)
            elif isinstance(value, (FnDef, ClassDef, Assign)):
                reserve(value.name)
                if isinstance(value, ClassDef):
                    reserve(value.source_name)
                if isinstance(value, FnDef):
                    for name in parameter_names(value.params):
                        reserve(name)
            if isinstance(value, ClassRef):
                reserve(value.name)
                if value.alias:
                    reserve(value.alias)
            if isinstance(value, (For, Try)):
                reserve(value.var)
            if isinstance(value, ImportStmt):
                reserve(value.alias)
            if isinstance(value, (GlobalStmt, NonlocalStmt)):
                for name in value.names:
                    reserve(name)
            if isinstance(value, ModuleInit):
                for name in value.globals:
                    reserve(name)
            for child in vars(value).values():
                self._reserve_source_names(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                self._reserve_source_names(child)

    def _fresh_internal(self, base):
        name = base
        while name in self._used_internal_names:
            name = "_" + name
        self._used_internal_names.add(name)
        return name

    def _fresh_internal_suffix(self, base):
        name = base
        suffix = 1
        while name in self._used_internal_names:
            name = f"{base}_{suffix}"
            suffix += 1
        self._used_internal_names.add(name)
        return name

    def _python_ident(self, name):
        """Encode source identifiers as injective ASCII Python identifiers."""
        if name not in self._python_name_map:
            candidate = self._encoded_source_ident(name)
            self._used_internal_names.add(candidate)
            self._python_name_map[name] = candidate
        return self._python_name_map[name]

    @staticmethod
    def _encoded_source_ident(name):
        return "_lt_user_" + name.encode("utf-8").hex()

    def _prepare_missing_alias(self):
        self._builtins_name = None
        self._runtime_name = None
        self._runtime_error_name = None
        self._source_file_name = None
        self._source_map_name = None
        self._missing_name = self._fresh_internal("_lt_missing")

    def _runtime(self, name):
        if self._runtime_name is None:
            return name
        return f"{self._runtime_name}[{json.dumps(name)}]"

    def _host_builtins(self):
        return self._builtins_name or "_builtins"

    def _emit_missing_alias(self):
        if self._runtime_name is None:
            self.w(f"{self._missing_name} = _LtFunction.MISSING")
        else:
            self.w(f"{self._missing_name} = {self._runtime('_LtFunction')}.MISSING")

    def _emit_prelude(self):
        self._builtins_name = "_builtins"
        self._runtime_name = self._fresh_internal("_lt_runtime")
        self._runtime_error_name = self._fresh_internal("_lt_runtime_error")
        self._source_file_name = self._fresh_internal("_lt_source_file")
        self._source_map_name = self._fresh_internal("_lt_source_map")
        self.w(PRELUDE.strip("\n"))
        self.w(f"{self._runtime_name} = globals().copy()")

    def _render_output(self):
        """Return the ordinary Python module emitted by this backend."""
        return "\n".join(self.out) + "\n"

    def w(self, s="", source_line=None, source_path=None):
        line = "    " * self.indent + s
        physical_line = self._physical_line_count + 1
        self.out.append(line)
        self._physical_line_count += line.count("\n") + 1
        if source_line:
            self.source_map[physical_line] = (source_path or self.source_path,
                                              source_line)

    def generate(self, prog):
        self._reserve_source_names(prog)
        self._prepare_missing_alias()
        if any(isinstance(s, ModuleInit) for s in prog.stmts):
            return self.generate_modules(prog)
        self._emit_prelude()
        self._emit_missing_alias()
        fns = [s for s in prog.stmts if isinstance(s, FnDef)]
        clss = class_order([s for s in prog.stmts if isinstance(s, ClassDef)])
        rest = [s for s in prog.stmts
                if not isinstance(s, (FnDef, ClassDef))]
        declared = {s.name for s in fns} | {s.name for s in clss}
        module_names = set()
        self._collect(rest, module_names)
        for name in sorted(module_names - declared):
            self.w(f"{self._python_ident(name)} = None")
        for fn in fns:
            self.w("")
            self.fndef(fn)
        for cd in clss:
            self.w("")
            self.classdef(cd)
        self.w("")
        self.w(f"{self._source_file_name} = {json.dumps(self.source_path, ensure_ascii=False)}")
        source_map_line = len(self.out)
        self.w(f"{self._source_map_name} = {{}}")
        if rest:
            self.w("")
            self.w('if __name__ == "__main__":')
            self.indent += 1
            self.w("try:")
            self.indent += 1
            for s in rest:
                self.stmt(s)
            self.indent -= 1
            self.w(f"except {self._host_builtins()}.Exception as {self._runtime_error_name}:")
            self.indent += 1
            self.w(f"{self._runtime('_wv_report_uncaught')}({self._runtime_error_name}, "
                   f"{self._source_map_name}, {self._source_file_name}, __file__)")
            self.w(f"raise {self._host_builtins()}.SystemExit(1)")
            self.indent -= 1
            self.indent -= 1
        self.out[source_map_line] = f"{self._source_map_name} = {self.source_map!r}"
        return self._render_output()

    def generate_modules(self, prog):
        self._emit_prelude()
        self._emit_missing_alias()
        modules = [s for s in prog.stmts if isinstance(s, ModuleInit)]
        fns = [s for s in prog.stmts if isinstance(s, FnDef)]
        clss = class_order([s for s in prog.stmts if isinstance(s, ClassDef)])
        root = next(m for m in modules if m.root)
        root_path = prog.module_sources[root.module_id]
        all_globals = []
        for module in modules:
            for name in module.globals:
                if name not in all_globals:
                    all_globals.append(name)
        for name in all_globals:
            self.w(f"{self._python_ident(name)} = None")
        for fn in fns:
            self.w("")
            self.fndef(fn)
        for cd in clss:
            self.w("")
            self.classdef(cd)
        for module in modules:
            self.w("")
            self.w(f"{module.state_name} = 0")
            self.w(f"def {module.init_name}():")
            self.indent += 1
            global_names = [module.state_name] + [
                self._python_ident(name) for name in module.globals]
            self.w("global " + ", ".join(global_names))
            self.w(f"if {module.state_name} == 2:")
            self.indent += 1
            self.w("return")
            self.indent -= 1
            self.w(f"if {module.state_name} == 1:")
            self.indent += 1
            self.w(f'raise {self._host_builtins()}.RuntimeError("cyclic module initialization")')
            self.indent -= 1
            self.w(f"{module.state_name} = 1")
            self.w("try:")
            self.indent += 1
            for dep_id in module.deps:
                dep = next(m for m in modules if m.module_id == dep_id)
                self.w(f"{dep.init_name}()")
            for stmt in module.body:
                self.stmt(stmt)
            self.w(f"{module.state_name} = 2")
            self.indent -= 1
            self.w(f"except {self._host_builtins()}.Exception:")
            self.indent += 1
            self.w(f"{module.state_name} = 0")
            self.w("raise")
            self.indent -= 1
            self.indent -= 1
        self.w("")
        self.w(f"{self._source_file_name} = {json.dumps(root_path, ensure_ascii=False)}")
        source_map_line = len(self.out)
        self.w(f"{self._source_map_name} = {{}}")
        self.w("")
        self.w('if __name__ == "__main__":')
        self.indent += 1
        self.w("try:")
        self.indent += 1
        self.w(f"{root.init_name}()")
        self.indent -= 1
        self.w(f"except {self._host_builtins()}.Exception as {self._runtime_error_name}:")
        self.indent += 1
        self.w(f"{self._runtime('_wv_report_uncaught')}({self._runtime_error_name}, "
               f"{self._source_map_name}, {self._source_file_name}, __file__)")
        self.w(f"raise {self._host_builtins()}.SystemExit(1)")
        self.indent -= 1
        self.indent -= 1
        self.out[source_map_line] = f"{self._source_map_name} = {self.source_map!r}"
        return self._render_output()

    def classdef(self, cd):
        factory = self._fresh_internal("_lt_make_class")
        self.w(f"def {factory}():")
        self.indent += 1
        self.w("class _LtMethods:")
        self.indent += 1
        for m in cd.methods:
            self.fndef(m, method=True)
        self.indent -= 1
        parent = self._python_ident(cd.parent.name) if cd.parent else "None"
        methods = ", ".join(
            f"{json.dumps(m.name)}: _LtMethods.{self._python_ident(m.name)}"
            for m in cd.methods)
        for method in cd.methods:
            py_method_name = self._python_ident(method.name)
            required = required_parameter_count(method.params[1:])
            fixed = fixed_parameter_names(method.params[1:])
            positional = positional_parameter_count(method.params[1:])
            required_keyword_only = required_keyword_only_names(
                method.params[1:])
            rest = rest_parameter_name(method.params[1:])
            extra = extra_parameter_name(method.params[1:])
            self.w(f"_LtMethods.{py_method_name}._lt_params = "
                   f"{json.dumps(fixed, ensure_ascii=False)}")
            self.w(f"_LtMethods.{py_method_name}._lt_required_count = {required}")
            self.w(f"_LtMethods.{py_method_name}._lt_positional_count = {positional}")
            self.w(f"_LtMethods.{py_method_name}._lt_required_keyword_only = "
                   f"{json.dumps(required_keyword_only, ensure_ascii=False)}")
            self.w(f"_LtMethods.{py_method_name}._lt_rest_name = "
                   f"{rest!r}")
            self.w(f"_LtMethods.{py_method_name}._lt_extra_name = "
                   f"{extra!r}")
        self.w(f"return {self._runtime('_LtClass')}({json.dumps(cd.source_name, ensure_ascii=False)}, "
               f"{parent}, {{{methods}}})")
        self.indent -= 1
        self.w(f"{self._python_ident(cd.name)} = {factory}()")

    def _scope_globals(self, stmts):
        out = set()
        for s in stmts:
            if isinstance(s, GlobalStmt):
                out.update(s.names)
            elif isinstance(s, FnDef):
                continue
            elif isinstance(s, If):
                out.update(self._scope_globals(s.then_body))
                out.update(self._scope_globals(s.else_body or []))
            elif isinstance(s, (For, While)):
                out.update(self._scope_globals(s.body))
            elif isinstance(s, Try):
                out.update(self._scope_globals(s.body))
                out.update(self._scope_globals(s.handler))
        return out

    def _scope_nonlocals(self, stmts):
        out = set()
        for s in stmts:
            if isinstance(s, NonlocalStmt):
                out.update(s.names)
            elif isinstance(s, FnDef):
                continue
            elif isinstance(s, If):
                out.update(self._scope_nonlocals(s.then_body))
                out.update(self._scope_nonlocals(s.else_body or []))
            elif isinstance(s, (For, While)):
                out.update(self._scope_nonlocals(s.body))
            elif isinstance(s, Try):
                out.update(self._scope_nonlocals(s.body))
                out.update(self._scope_nonlocals(s.handler))
        return out

    def fndef(self, fn, method=False):
        # locals default to nil (matches Java backend)
        assigned = set()
        self._collect(fn.body, assigned)
        global_names = self._scope_globals(fn.body)
        nonlocal_names = self._scope_nonlocals(fn.body)
        source_names = [parameter_name(param)
                        for param in bound_parameter_order(fn.params)]
        names = [self._python_ident(name) for name in source_names]
        assigned -= set(source_names) | global_names | nonlocal_names
        self.w(f"def {self._python_ident(fn.name)}({', '.join(names)}):", fn.line,
               fn.source_path)
        self.indent += 1
        if global_names:
            self.w("global " + ", ".join(
                self._python_ident(name) for name in sorted(global_names)))
        if nonlocal_names:
            self.w("nonlocal " + ", ".join(
                self._python_ident(name) for name in sorted(nonlocal_names)))
        for name in sorted(assigned):
            self.w(f"{self._python_ident(name)} = None")
        for param in fn.params:
            if parameter_has_default(param):
                py_param_name = self._python_ident(param.name)
                self.w(f"if {py_param_name} is {self._missing_name}:",
                       param.line, param.source_path)
                self.indent += 1
                self.w(f"{py_param_name} = {self.expr(param.default)}",
                       param.line, param.source_path)
                self.indent -= 1
        for s in fn.body:
            self.stmt(s)
        if not any(isinstance(s, Return) for s in fn.body):
            pass
        self.indent -= 1
        if not method:
            py_fn_name = self._python_ident(fn.name)
            self.w(f"{py_fn_name} = {self._runtime('_LtFunction')}({py_fn_name}, "
                   f"{json.dumps(fixed_parameter_names(fn.params), ensure_ascii=False)}, "
                   f"{json.dumps(fn.source_name, ensure_ascii=False)}, "
                   f"required_count={required_parameter_count(fn.params)}, "
                   f"positional_count={positional_parameter_count(fn.params)}, "
                   f"required_keyword_only="
                   f"{json.dumps(required_keyword_only_names(fn.params), ensure_ascii=False)}, "
                   f"rest_name={rest_parameter_name(fn.params)!r}, "
                   f"extra_name={extra_parameter_name(fn.params)!r})")

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
            elif isinstance(s, Try):
                out.add(s.var)
                self._collect(s.body, out)
                self._collect(s.handler, out)
            elif isinstance(s, FnDef):
                out.add(s.name)

    def stmt(self, s):
        if isinstance(s, (GlobalStmt, NonlocalStmt)):
            return
        if isinstance(s, FnDef):
            self.fndef(s)
        elif isinstance(s, Assign):
            self.w(f"{self._python_ident(s.name)} = {self.expr(s.value)}",
                   s.line, s.source_path)
        elif isinstance(s, ExprStmt):
            self.w(self.expr(s.expr), s.line, s.source_path)
        elif isinstance(s, If):
            self.w(f"if {self.expr(s.cond)}:", s.line, s.source_path)
            self.suite(s.then_body)
            if s.else_body:
                self.w("else:")
                self.suite(s.else_body)
        elif isinstance(s, While):
            self.w(f"while {self.expr(s.cond)}:", s.line, s.source_path)
            self.suite(s.body)
        elif isinstance(s, For):
            self.w(f"for {self._python_ident(s.var)} in {self._runtime('_wv_iter')}({self.expr(s.iter)}):", s.line,
                   s.source_path)
            self.suite(s.body)
        elif isinstance(s, Return):
            self.w(f"return {self.expr(s.value)}" if s.value is not None
                   else "return None", s.line, s.source_path)
        elif isinstance(s, Break):
            self.w("break", s.line, s.source_path)
        elif isinstance(s, Continue):
            self.w("continue", s.line, s.source_path)
        elif isinstance(s, Try):
            # catch var defaults to nil so reading it outside the handler
            # is nil on both backends (not NameError vs javac error)
            error_name = self._fresh_internal("_lt_e")
            message_name = self._fresh_internal("_lt_m")
            py_var = self._python_ident(s.var)
            self.w(f"{py_var} = None")
            self.w("try:", s.line, s.source_path)
            self.suite(s.body)
            self.w(f"except {self._host_builtins()}.Exception as {error_name}:")
            self.indent += 1
            # KeyError str() adds quotes; args[0] is the clean message
            self.w(f"{message_name} = {error_name}.args[0] if "
                   f"{error_name}.args and {self._host_builtins()}.isinstance({error_name}.args[0], "
                   f"{self._host_builtins()}.str) else {self._host_builtins()}.str({error_name})")
            self.w(f"{py_var} = {message_name}")
            for x in s.handler:
                self.stmt(x)
            self.indent -= 1
        elif isinstance(s, Throw):
            self.w(f"raise {self._host_builtins()}.Exception({self._runtime('_wv_repr')}({self.expr(s.value)}))", s.line,
                   s.source_path)
        else:
            raise Exception(f"py backend: unexpected {type(s).__name__}")

    def suite(self, stmts):
        self.indent += 1
        for s in stmts:
            self.stmt(s)
        self.indent -= 1

    def argument(self, arg):
        if isinstance(arg, NamedArg):
            return f"{self._runtime('_wv_named')}({json.dumps(arg.name, ensure_ascii=False)}, " \
                   f"{self.expr(arg.value)})"
        if isinstance(arg, StarArg):
            return f"{self._runtime('_wv_star')}({self.expr(arg.value)})"
        if isinstance(arg, StarStarArg):
            return f"{self._runtime('_wv_starstar')}({self.expr(arg.value)})"
        return self.expr(arg)

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
            return self._python_ident(e.id)
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
            args = ", ".join(self.argument(a) for a in e.args)
            if isinstance(e.func, Name) and e.func.id in BUILTIN_PY:
                return f"{self._runtime(BUILTIN_PY[e.func.id])}({args})"
            suffix = ", " + args if args else ""
            return f"{self._runtime('_wv_call')}({self.expr(e.func)}{suffix})"
        if isinstance(e, SuperCall):
            receiver = self.expr(e.args[0])
            args = [self.argument(a) for a in e.args[1:]]
            rendered = ", ".join([receiver, self._python_ident(e.owner.name),
                                    json.dumps(e.method)] + args)
            return f"{self._runtime('_wv_supercall')}({rendered})"
        if isinstance(e, PyImport):
            return f"{self._runtime('_wv_pymod')}({self.expr(e.module_expr)})"
        if isinstance(e, JavaImport):
            return f"{self._runtime('_wv_jclass')}({self.expr(e.class_expr)})"
        raise Exception(f"py backend: unexpected {type(e).__name__}")

    def num(self, v):
        if v != v:
            return f'{self._host_builtins()}.float("nan")'
        if v == float("inf"):
            return f'{self._host_builtins()}.float("inf")'
        if v == float("-inf"):
            return f'{self._host_builtins()}.float("-inf")'
        return repr(v)

    def binop(self, e):
        l, r = self.expr(e.left), self.expr(e.right)
        op = e.op
        if op == "+":
            return f"{self._runtime('_wv_add')}({l}, {r})"
        if op == "-":
            return f"{self._runtime('_wv_sub')}({l}, {r})"
        if op == "*":
            return f"{self._runtime('_wv_mul')}({l}, {r})"
        if op == "/":
            return f"{self._runtime('_wv_div')}({l}, {r})"
        if op == "%":
            return f"{self._runtime('_wv_mod')}({l}, {r})"
        if op == "**":
            return f"{self._runtime('_wv_pow')}({l}, {r})"
        if op == "==":
            return f"{self._runtime('_wv_eq')}({l}, {r})"
        if op == "!=":
            return f"(not {self._runtime('_wv_eq')}({l}, {r}))"
        if op in ("<", "<=", ">", ">="):
            return f"({self._runtime('_wv_cmp')}({l}, {r}) {op} 0)"
        if op == "and":
            return f"({l} and {r})"
        if op == "or":
            return f"({l} or {r})"
        raise Exception(f"py backend: bad op {op}")


def generate(prog, source_path="<src>"):
    return Gen(source_path=source_path).generate(prog)
