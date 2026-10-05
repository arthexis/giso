from __future__ import annotations

import ast

import pytest

from giso import Giso


def test_ast_module_folds_defined_functions():
    tree = ast.parse(
        """
def math__double(value):
    return value * 2
"""
    )

    giso = Giso(tree)

    assert giso.math.double(4) == 8


def test_ast_module_folds_defined_classes():
    tree = ast.parse(
        """
class Tool:
    def status(self):
        return "ready"
"""
    )

    giso = Giso(tree)

    assert giso.tool.status() == "ready"


def test_ast_module_ignores_imported_callables():
    tree = ast.parse(
        """
from math import sqrt

def local(value):
    return value + 1
"""
    )

    giso = Giso(tree)

    assert "local" in giso.operations
    assert "sqrt" not in giso.operations


def test_ast_expression_evaluates_then_folds_result():
    tree = ast.parse("{'double': lambda value: value * 2}", mode="eval")

    giso = Giso(tree)

    assert giso.double(5) == 10


def test_ast_expression_can_produce_callable():
    tree = ast.parse("lambda value: value + 3", mode="eval")

    giso = Giso(tree)

    assert giso.operations["<lambda>"](4) == 7


def test_ast_sources_work_inside_nested_source_containers():
    tree = ast.parse(
        """
def status():
    return "nested"
"""
    )

    giso = Giso([[tree]])

    assert giso.status() == "nested"


def test_ast_source_works_inside_named_constructor_branch():
    tree = ast.parse(
        """
def status():
    return "charger"
"""
    )

    giso = Giso(charger=tree)

    assert giso.charger.status() == "charger"


def test_exec_code_object_folds_definitions_from_synthetic_module():
    code = compile(
        """
def status():
    return "compiled"
""",
        "<compiled-test>",
        "exec",
    )

    giso = Giso(code)

    assert giso.status() == "compiled"


def test_eval_code_object_folds_returned_value():
    code = compile("{'double': lambda value: value * 2}", "<compiled-test>", "eval")

    giso = Giso(code)

    assert giso.double(6) == 12


def test_code_object_works_inside_named_constructor_branch():
    code = compile(
        """
def status():
    return "compiled-branch"
""",
        "<compiled-branch>",
        "exec",
    )

    giso = Giso(charger=code)

    assert giso.charger.status() == "compiled-branch"


def test_code_object_requiring_arguments_is_rejected():
    def status(value):
        return value

    with pytest.raises(TypeError, match="requiring arguments"):
        Giso(status.__code__)


def test_code_object_with_free_variables_is_rejected():
    marker = "closed"

    def status():
        return marker

    with pytest.raises(TypeError, match="free variables"):
        Giso(status.__code__)


def test_compile_constructor_builds_giso_from_statement_source():
    giso = Giso.compile(
        """
def math__double(value):
    return value * 2
"""
    )

    assert giso.math.double(7) == 14


def test_eval_constructor_builds_giso_from_expression_value():
    giso = Giso.eval("{'triple': lambda value: value * 3}")

    assert giso.triple(4) == 12


def test_compile_and_eval_preserve_custom_giso_name():
    compiled = Giso.compile("def status(): return 'compiled'", name="compiled_tools")
    evaluated = Giso.eval("{'status': lambda: 'evaluated'}", name="evaluated_tools")

    assert compiled.__name__ == "compiled_tools"
    assert evaluated.__name__ == "evaluated_tools"


def test_compiled_constructor_result_can_be_mounted_as_named_branch():
    child = Giso.compile("def status(): return 'charger'")
    parent = Giso(charger=child)

    assert parent.charger.status() == "charger"


def test_compile_and_eval_reject_non_string_inputs():
    with pytest.raises(TypeError, match="source must be a string"):
        Giso.compile(ast.parse("pass"))
    with pytest.raises(TypeError, match="expression must be a string"):
        Giso.eval(ast.parse("1", mode="eval"))


def test_compile_and_eval_propagate_syntax_errors():
    with pytest.raises(SyntaxError):
        Giso.compile("def broken(:")
    with pytest.raises(SyntaxError):
        Giso.eval("1 +")


def test_compile_result_survives_ordinary_copy_semantics():
    original = Giso.compile("def status(): return 'compiled'")
    copied = Giso(original)

    assert copied.status() == "compiled"
