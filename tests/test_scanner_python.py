from __future__ import annotations

from ibwd.scanner.python import extract_python_symbols

SOURCE = b"""\
class User:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return self.name


def create_user(name):
    return User(name)


@some_decorator
def decorated():
    pass
"""


def test_extract_python_symbols_finds_class_function_and_method():
    symbols = extract_python_symbols(SOURCE, "src/models.py")
    by_name = {s.name: s for s in symbols}

    assert by_name["User"].kind == "Class"
    assert by_name["User"].qualified_name == "src/models.py::User"
    assert by_name["User"].start_line == 1

    assert by_name["greet"].kind == "Method"
    assert by_name["greet"].qualified_name == "src/models.py::User.greet"
    assert by_name["greet"].start_line == 5

    assert by_name["create_user"].kind == "Function"
    assert by_name["create_user"].qualified_name == "src/models.py::create_user"

    assert by_name["__init__"].kind == "Method"
    assert by_name["__init__"].qualified_name == "src/models.py::User.__init__"


def test_extract_python_symbols_handles_decorators():
    symbols = extract_python_symbols(SOURCE, "src/models.py")
    decorated = next(s for s in symbols if s.name == "decorated")
    assert decorated.kind == "Function"
    # start_line includes the decorator line
    assert decorated.start_line == 13
