"""Python host fixtures for focused P19-B outbound named-call tests."""
from __future__ import annotations

import threading

_events: list[str] = []
_body_calls = 0
_saved_callback = None


def reset():
    global _body_calls, _saved_callback  # noqa: PLW0603 (host fixture mutates module state on purpose)
    _events.clear()
    _body_calls = 0
    _saved_callback = None
    return "reset"


def scale(value, factor=1):
    return value * factor


def mutable_default(value=[]):  # noqa: B006 (intentional)
    if value is None:
        return "None"
    value.append("x")
    return repr(value)


class Rebindable:
    def method(self, value):
        return "old:" + value


def _replacement_method(self, value):  # noqa: ARG001
    return "new:" + value


def rebind_class_method():
    Rebindable.method = _replacement_method
    return "rebound"


class SortableList(list):
    pass


def sortable():
    return SortableList([3, 1, 2])


def join_values(left, right, third="!"):
    return "%s|%s|%s" % (left, right, third)


def positional_only(a, /, b=2, *rest, flag, tail=9, **extra):
    return "%s|%s|%r|%s|%s|%r" % (
        a, b, rest, flag, tail, list(extra.items()))


def strict(value, *, flag):
    global _body_calls  # noqa: PLW0603 (host fixture mutates module state on purpose)
    _body_calls += 1
    return "%s|%s" % (value, flag)


def record(*args, **kwargs):
    global _body_calls  # noqa: PLW0603 (host fixture mutates module state on purpose)
    _body_calls += 1
    return "%r|%r" % (args, list(kwargs.items()))


def body_calls():
    return _body_calls


def mark(label, value):
    _events.append(label)
    return value


def events():
    return ",".join(_events)


class Receiver:
    def record(self, *, first, second):
        return "%s|%s|%s" % ("receiver", first, second)


def receiver():
    _events.append("receiver")
    return Receiver()


def explode(label):
    _events.append(label)
    raise RuntimeError("evaluation-boom")


def describe(value):
    return "%s:%r" % (type(value).__name__, value)


class Opaque:
    def label(self):
        return "opaque-ok"


def opaque():
    return Opaque()


def opaque_label(value):
    return value.label()


def run(callback, *, value, suffix="!"):
    return callback(value + suffix)


def invoke_named(callback, *, value):
    return callback(value)


def invoke_two_named(first, second, *, value):
    return "%s|%s" % (first(value), second(value))


def invoke_pos_and_named(callback, *, alias, value):
    return "%s|%s" % (callback(value), alias(value))


def save(callback):
    global _saved_callback  # noqa: PLW0603 (host fixture mutates module state on purpose)
    _saved_callback = callback
    return "saved"


def save_then_fail(callback):
    global _saved_callback  # noqa: PLW0603 (host fixture mutates module state on purpose)
    _saved_callback = callback
    raise RuntimeError("save-failed")


def call_saved():
    return _saved_callback("stale")


def call_on_worker(callback):
    outcome: list[object] = []

    def worker():
        try:
            outcome.append(callback("worker"))
        except BaseException as error:  # report thread-boundary failure
            outcome.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=3)
    if thread.is_alive():
        raise RuntimeError("worker did not finish")
    if outcome and isinstance(outcome[0], BaseException):
        raise outcome[0]
    return "unexpected:%r" % outcome


class CallableObject:
    def __call__(self, value=None):
        return value


callable_object = CallableObject()
_dynamic_callable = lambda value=None: value

def dynamic_callable():
    return _dynamic_callable


def _make_callable():
    return lambda value=None: value


def property_value():
    return _make_callable()
