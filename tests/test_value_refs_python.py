"""Python value-use (REFERENCES) extraction edge cases found by the full-repository oracle comparison."""
from __future__ import annotations

from ibwd.scanner.python import extract_python_references


def _refs(source: str) -> set[tuple[str, str | None]]:
    return {(v.name, v.receiver) for v in extract_python_references(source.encode()).value_refs}


def test_generic_base_head_and_arguments_are_not_value_uses():
    assert _refs("class C(Base[Arg], nodes.El[Arg2]):\n    pass\n") == set()


def test_metaclass_keyword_is_still_a_value_use():
    assert ("Meta", None) in _refs("class C(Base, metaclass=Meta):\n    pass\n")


def test_except_and_with_expressions_are_uses_but_their_aliases_are_not():
    refs = _refs("def f():\n    try:\n        pass\n    except ThemeError as exc:\n        raise\n    with Lock as held:\n        pass\n")
    assert ("ThemeError", None) in refs and ("Lock", None) in refs
    assert ("exc", None) not in refs and ("held", None) not in refs


def test_parameter_defaults_are_evaluated_in_the_enclosing_scope():
    refs = _refs("def f(cb=cb, n=default_n):\n    return cb\n")
    assert ("cb", None) in refs and ("default_n", None) in refs      # the default `cb` is the module-level one


def test_body_uses_of_a_parameter_are_not_module_references():
    assert ("cb", None) not in _refs("def f(cb):\n    return cb\n")


def test_head_of_an_attribute_chain_is_a_use_of_a_class_or_function_namespace():
    refs = _refs("def f():\n    return Cls.CONST, logtool.command(1), self.foo\n")
    assert ("Cls", None) in refs and ("logtool", None) in refs and ("CONST", "Cls") in refs
    assert ("self", None) not in refs


def test_a_property_read_at_the_start_of_a_longer_chain_is_a_use():
    refs = _refs("def f(self):\n    self.client.delete(1)\n    return self.a.b\n")
    assert ("client", "self") in refs and ("a", "self") in refs and ("b", "self.a") in refs


def test_function_local_imports_do_not_hide_value_uses_and_super_attributes_are_uses():
    refs = _refs("def f(app):\n    from mod import handler\n    app.connect('x', handler)\n    return super().method\n")
    assert ("handler", None) in refs and ("method", "super") in refs


def test_except_with_a_dotted_exception_class_is_a_use():
    refs = _refs("def f():\n    try:\n        pass\n    except requests.Redirect as err:\n        raise\n")
    assert ("Redirect", "requests") in refs and ("err", None) not in refs


def test_del_is_not_a_use():
    assert ("_StrPath", None) not in _refs("del _StrPath\n")
