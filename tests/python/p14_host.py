"""Host functions used by the P14 callable-value regression matrix."""

def scale(value):
    return value * 2


def replacement(value):
    return value * 3


def rebind_scale():
    global scale  # noqa: PLW0603 (host fixture mutates module state on purpose)
    scale = replacement


def echo(value):
    return value


class CallableValue:
    def __call__(self, value=None):
        return value


callable_object = CallableValue()


def __getattr__(name):
    if name == "dynamic_callable":
        return lambda value=None: value
    raise AttributeError(name)
