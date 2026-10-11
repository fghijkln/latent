"""Small host objects used only by the P13 interop regression matrix."""
import types


class _CallableDescriptor:
    def __get__(self, instance, owner):
        if instance is None:
            return self
        return types.MethodType(lambda self, value: value, instance)


class Sample:
    custom = _CallableDescriptor()

    def __init__(self):
        self.items = []
        self.callback_field = lambda value=None: value
        self.shadow = "field"

    def push(self, value):
        self.items.append(value)
        return len(self.items)

    def shadow(self):
        return "method"

    def accept(self, values):
        return len(values)

    @property
    def callback(self):
        return lambda value=None: value

    @staticmethod
    def static_method(value):
        return value

    @classmethod
    def class_method(cls, value):
        return value


def module_function(value):
    return value
