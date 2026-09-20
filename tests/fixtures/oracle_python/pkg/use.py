import pkg.core as core
from pkg import helper as h2
from pkg.core import helper, outer, Child, Base, Opts


def a():
    return helper()


def b():
    return outer(helper)


def c(x: Child) -> Child:
    return x


def d():
    return core.helper()


def e():
    return h2()


def f():
    return list(map(helper, [1]))


def g(obj: Base):
    return obj.new_method()


class K(Child):
    def m(self):
        return self.n()

    def n(self):
        return 1


def h(o: Opts):
    return o.backoff()


def i():
    def cb():
        return helper()

    return cb()
