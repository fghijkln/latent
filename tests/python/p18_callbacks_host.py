"""Python host functions for the synchronous P18 callback tests."""
import math
import os
import threading
from functools import partial

_saved_callback = None


class OpaqueValue:
    def label(self):
        return "opaque"


def opaque_value():
    return OpaqueValue()


def identity(value):
    return value


def invoke_one(callback):
    return callback("one")


def invoke_two(callback):
    return callback("a", "b")


def repeat(callback, count):
    return [callback(index) for index in range(count)]


def echo(value):
    return value


def inspect_conversion(callback):
    result = callback(
        True, None, 7, -0.0, "雪🙂", (2, {"deep": [3]}), OpaqueValue())
    valid = (
        isinstance(result, list) and len(result) == 8 and
        result[0] is True and result[1] is True and
        type(result[2]) is float and result[2] == 7.5 and
        type(result[3]) is float and math.copysign(1.0, result[3]) < 0 and
        result[4] == "雪🙂" and result[5] == 3 and
        result[6] == "opaque" and result[7] is None)
    return "conversion-ok" if valid else "conversion-bad"


def save(callback):
    global _saved_callback
    _saved_callback = callback
    return "saved"


def save_then_fail(callback):
    global _saved_callback
    _saved_callback = callback
    raise ValueError("host-failure-after-save")


def call_saved():
    if _saved_callback is None:
        raise RuntimeError("no callback was saved")
    return _saved_callback("late")


def call_keyword(callback):
    return callback(value="keyword")


def call_on_worker(callback):
    errors = []

    def worker():
        try:
            callback("worker")
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(3)
    if thread.is_alive():
        raise RuntimeError("callback worker did not finish")
    if not errors:
        raise AssertionError("callback unexpectedly ran on worker")
    raise errors[0]


def deferred(callback):
    return (callback(value) for value in ("deferred",))


def async_deferred(callback):
    async def result():
        return callback("async")
    return result()


def generator_value():
    return (value for value in ("callback-returned-generator",))


def consume_callback_generator(callback):
    result = callback("generator")
    next(result)
    return "generator-was-consumed"


def consume_callback_callable(callback):
    result = callback("callable")
    return result("MUST-NOT-RUN")


def native_fd_write(callback):
    os.write(1, b"P18_NATIVE_FD1\n")
    return callback("fd-write")


def native_fd_read(callback):
    value = os.read(0, 1)
    return callback("fd-read-empty" if value == b"" else "fd-read-consumed")


def call_with_star_args(callback):
    return callback(*("star-arg",))


def call_zero_one_two(callback):
    return " | ".join((callback(), callback("one"), callback("two", "?")))


def call_variadic_sequence(callback):
    vectors = ((), ("a",), ("a", "b"))
    return ",".join(str(callback(*args)) for args in vectors)


def call_with_partial(callback):
    return partial(callback, "partial")()


def call_with_two_star_args(callback):
    return callback(*("first", "second"))


def call_with_star_kwargs(callback):
    return callback(**{"value": "star-kwarg"})


def catch_callback_error(callback):
    try:
        callback("host-catch")
    except Exception as error:
        return "caught:" + str(error)
    return "not-caught"


def transform_callback_error(callback):
    try:
        callback("host-transform")
    except Exception as error:
        raise ValueError("wrapped:" + str(error))
    return "not-transformed"


def rethrow_callback_error(callback):
    try:
        callback("host-rethrow")
    except Exception:
        raise
    return "not-rethrown"


def call_ordered_kwargs(callback):
    return callback("head", "tail", flag=True, **{"z": 1, "if": 2, "雪": 3})


def call_keyword_defaults(callback):
    return [callback(flag=True), callback(value=None, flag=False)]


def call_keyword_conversion(callback):
    result = callback(flag=True, missing=None, number=7, negative_zero=-0.0,
                      nested=(2, {"deep": [3]}), opaque=OpaqueValue())
    valid = (
        result[0] is True and result[1] is True and
        type(result[2]) is float and result[2] == 7.5 and
        type(result[3]) is float and math.copysign(1.0, result[3]) < 0 and
        result[4] == 3 and result[5] == "opaque")
    return "callback-kwargs-conversion-ok" if valid else repr(result)


def call_conflicting_kwargs(callback):
    return callback("positional", value="named")


def call_unexpected_kwargs(callback):
    return callback(value="ok", other="bad")


def call_missing_keyword_only(callback):
    return callback("value")


def call_duplicate_keyword_merge(callback):
    return callback(value=1, **{"value": 2})
