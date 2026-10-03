from __future__ import annotations

from types import ModuleType

from giso import Giso
from tests.ingredients import basic


def test_constructor_folds_module_object():
    assert isinstance(basic, ModuleType)

    giso = Giso(basic)

    assert giso.math.double(4) == 8
    assert giso.text.upper("giso") == "GISO"
    assert giso.greeter.hello("Ada") == "hello Ada"
    assert giso.modules[basic.__name__] is basic


def test_fold_mutates_existing_giso_with_module_object():
    giso = Giso()

    returned = giso.fold(basic)

    assert returned is giso
    assert giso.math.double(5) == 10
    assert giso.text.upper("fold") == "FOLD"
    assert giso.greeter.hello("Grace") == "hello Grace"
    assert giso.modules[basic.__name__] is basic


def test_module_object_only_exposes_locally_defined_callables_and_classes():
    giso = Giso(basic)

    assert "math.double" in giso.operations
    assert "text.upper" in giso.operations
    assert "greeter.hello" in giso.operations
    assert "kitchen.make_extra" in giso.operations

    # Giso is imported by the ingredient module, not defined there, so folding
    # the module must not expose it as a capability.
    assert "giso" not in giso.operations
    assert not hasattr(giso, "giso")
