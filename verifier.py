"""Latent transition verifier: a small, independent safety core.

The code generators and the generated runtime (`gen_py.PRELUDE`) are treated as
untrusted, complex implementations. This module is the trusted part: it does not
import any of them and knows only Latent's value domain and the contract of each
state transition. Latent-native operands are checked on the way in, and native
results are checked on the way out. Opaque host objects keep their host behavior;
values normalized back into Latent-native containers are checked before use.

Invariant enforced (phase 1, value-domain closure):
  * Exact Python ints in signed 64-bit range and float64 values are Latent
    numeric values. Out-of-range ints in a native operand/result are violations.
  * Native boolean operands are never accepted by numeric transitions.
Host handles (py/java interop objects) keep their own semantics; those are
governed by capabilities, which is phase 2.

The source of this file is inlined verbatim into every generated Python program
by gen_py, so it must stay self-contained: stdlib-free, no imports.
"""

_LT_VF_INT_MIN = -(1 << 63)
_LT_VF_INT_MAX = (1 << 63) - 1
_LT_VF_INT = int
_LT_VF_FLOAT = float
_LT_VF_SCALARS = (type(None), bool, int, float, str, list, dict)
_LT_VF_NUMERIC_PAIRS = ((int, int), (int, float), (float, int), (float, float))

# Binary arithmetic: operand type pairs allowed between Latent-native values.
# Mixed int/float value exactness is checked separately below.
_LT_VF_BINARY = {
    "_wv_add": _LT_VF_NUMERIC_PAIRS + ((str, str), (list, list)),
    "_wv_sub": _LT_VF_NUMERIC_PAIRS,
    "_wv_mul": _LT_VF_NUMERIC_PAIRS,
    "_wv_div": _LT_VF_NUMERIC_PAIRS,
    "_wv_mod": _LT_VF_NUMERIC_PAIRS,
    "_wv_pow": _LT_VF_NUMERIC_PAIRS,
}
_LT_VF_UNARY = {"_wv_neg": (int, float)}
# Transitions that may reach host objects; native values must stay in-domain.
_LT_VF_OUTPUT_ONLY = ("_wv_wgetattr", "_wv_wcall", "_wv_index", "_wv_call",
                      "_wv_len", "_wv_int", "_wv_range", "_wv_iter",
                      "_wv_keys", "_wv_push", "_wv_wsetattr",
                      "_wv_wsetindex", "_wv_supercall")


class LatentVerifierError(TypeError):
    """A transition broke the Latent value-domain contract."""


def _lt_vf_is_native(v, latent_types):
    t = type(v)
    return t in _LT_VF_SCALARS or t in latent_types


def _lt_vf_is_checkable(v, latent_types):
    return (_lt_vf_is_native(v, latent_types) or type(v) is int or
            isinstance(v, (list, tuple, dict, set, frozenset)))


def _lt_vf_has_host_int(v):
    """Return true only for an integer outside Latent's signed64 value domain."""
    pending = [v]
    seen = set()
    while pending:
        current = pending.pop()
        if isinstance(current, bool):
            continue
        if type(current) is int:
            if current < _LT_VF_INT_MIN or current > _LT_VF_INT_MAX:
                return True
            continue
        if not isinstance(current, (list, tuple, dict, set, frozenset)):
            continue
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(current, dict):
            for key, value in dict.items(current):
                pending.append(key)
                pending.append(value)
        elif isinstance(current, list):
            pending.extend(list.__iter__(current))
        elif isinstance(current, tuple):
            pending.extend(tuple.__iter__(current))
        elif isinstance(current, set):
            pending.extend(set.__iter__(current))
        else:
            pending.extend(frozenset.__iter__(current))
    return False


def _lt_vf_check_out(name, v):
    if _lt_vf_has_host_int(v):
        raise LatentVerifierError(
            "verifier: %s produced an int outside signed 64-bit range" % name)
    return v


def _lt_vf_reject(name, fn, args, kinds):
    # The operands break the contract. If the implementation also rejects them,
    # keep its (user-facing) diagnostic; if it accepts them, its acceptance is
    # exactly the bug class the verifier exists for, and its result is dropped.
    # Safe because guarded native transitions are pure.
    fn(*args)
    raise LatentVerifierError(
        "verifier: %s does not accept %s" % (name[4:], kinds))


def _lt_vf_mixed_int_is_exact(a, b):
    if type(a) is int and type(b) is float:
        return _LT_VF_INT(_LT_VF_FLOAT(a)) == a
    if type(a) is float and type(b) is int:
        return _LT_VF_INT(_LT_VF_FLOAT(b)) == b
    return True


def _lt_vf_wrap_binary(name, fn, allowed, latent_types):
    def checked(a, b):
        if _lt_vf_has_host_int(a) or _lt_vf_has_host_int(b):
            raise LatentVerifierError(
                "verifier: %s got an int outside signed 64-bit range" % name[4:])
        if _lt_vf_is_native(a, latent_types) and _lt_vf_is_native(b, latent_types):
            if (type(a), type(b)) not in allowed:
                _lt_vf_reject(name, fn, (a, b), "(%s, %s)"
                              % (_lt_vf_kind(a), _lt_vf_kind(b)))
            if not _lt_vf_mixed_int_is_exact(a, b):
                _lt_vf_reject(name, fn, (a, b),
                              "(int, float) without exact representation")
            r = fn(a, b)
            if type(r) not in _LT_VF_SCALARS:
                raise LatentVerifierError(
                    "verifier: %s returned a non-Latent value" % name[4:])
            return _lt_vf_check_out(name, r)
        return _lt_vf_check_out(name, fn(a, b))
    checked.__name__ = name
    return checked


def _lt_vf_wrap_unary(name, fn, allowed, latent_types):
    def checked(a):
        if _lt_vf_has_host_int(a):
            raise LatentVerifierError(
                "verifier: %s got an int outside signed 64-bit range" % name[4:])
        if _lt_vf_is_native(a, latent_types):
            if type(a) not in allowed:
                _lt_vf_reject(name, fn, (a,), _lt_vf_kind(a))
            r = fn(a)
            if type(r) not in _LT_VF_SCALARS:
                raise LatentVerifierError(
                    "verifier: %s returned a non-Latent value" % name[4:])
            return _lt_vf_check_out(name, r)
        return _lt_vf_check_out(name, fn(a))
    checked.__name__ = name
    return checked


def _lt_vf_wrap_output(name, fn, latent_types):
    def checked(*args, **kwargs):
        for a in args:
            if (_lt_vf_is_checkable(a, latent_types) and
                    _lt_vf_has_host_int(a)):
                raise LatentVerifierError(
                    "verifier: %s got an int outside signed 64-bit range" % name[4:])
        for a in kwargs.values():
            if (_lt_vf_is_checkable(a, latent_types) and
                    _lt_vf_has_host_int(a)):
                raise LatentVerifierError(
                    "verifier: %s got an int outside signed 64-bit range" % name[4:])
        result = fn(*args, **kwargs)
        if _lt_vf_is_checkable(result, latent_types):
            return _lt_vf_check_out(name, result)
        return result
    checked.__name__ = name
    return checked


def _lt_vf_kind(v):
    return {type(None): "nil", bool: "bool", int: "int", float: "num",
            str: "str", list: "list", dict: "map"}.get(type(v), type(v).__name__)


def _lt_vf_install(ns, latent_type_names=("_LtObj", "_LtClass", "_LtFunction",
                                   "_InteropBoundMethod",
                                   "_InteropModuleFunction",
                                   "_InteropStaticMethod")):
    """Replace the guarded transitions in runtime namespace `ns` in place."""
    latent_types = tuple(ns[n] for n in latent_type_names if n in ns)
    for name, allowed in _LT_VF_BINARY.items():
        if name in ns:
            ns[name] = _lt_vf_wrap_binary(name, ns[name], allowed, latent_types)
    for name, allowed in _LT_VF_UNARY.items():
        if name in ns:
            ns[name] = _lt_vf_wrap_unary(name, ns[name], allowed, latent_types)
    for name in _LT_VF_OUTPUT_ONLY:
        if name in ns:
            ns[name] = _lt_vf_wrap_output(name, ns[name], latent_types)
    ns["_lt_verifier_installed"] = True
    return ns
