from __future__ import annotations

import ast

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
