from typing import Callable, Generic, TypeVar, overload

T = TypeVar("T")


def helper():
    return 1


def outer(fn):
    return fn()


class Base:
    def new_method(self):
        return 1


class Child(Base, Generic[T]):
    pass


def make():
    def inner():
        return helper()

    return inner


class P:
    @property
    def conf(self):
        return helper()

    @conf.setter
    def conf(self, value):
        outer(value)


@overload
def ov(x: int) -> int: ...
@overload
def ov(x: str) -> str: ...
def ov(x):
    return helper()


def default_backoff():
    return 1


class Opts:
    backoff: Callable = default_backoff


label = "héllo→世界"; touched = helper()


class Coll:
    made = helper()

    def esc(self):
        return 1

    def use(self):
        return self.esc()

    def build(self):
        def esc():
            return 2

        return esc


def shadow(helper):
    return helper()


class Prop:
    @property
    def val(self):
        return 1

    def read(self):
        return self.val


def pick(flag):
    return (helper if flag else outer)(1)
