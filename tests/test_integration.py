from __future__ import annotations

from giso import Giso, Sigil
from tests.ingredients import basic


def test_module_object_and_module_string_fold_the_same_surface():
    from_object = Giso(basic)
    from_string = Giso("tests.ingredients.basic")

    assert set(from_object.operations) == set(from_string.operations)
    assert from_string.math.double(3) == 6
    assert from_string.text.upper("giso") == "GISO"
    assert from_string.greeter.hello("cook") == "hello cook"


def test_sigils_results_and_addition_work_together():
    left = Giso("tests.ingredients.basic")
    double = left[["math.double"]]

    assert isinstance(double, Sigil)
    assert double(4) == 8
    assert left.results["math.double"] == 8

    def local__identity(value: str) -> str:
        return value

    right = Giso(local__identity)
    combined = left + right

    assert combined is not left
    assert combined.results.history == left.results.history
    assert combined.local.identity("x") == "x"
    assert not hasattr(left, "local")


def test_giso_to_giso_fold_carries_capabilities_and_results():
    source = Giso("tests.ingredients.basic")
    source.text.upper("stew")

    target = Giso()
    target += source

    assert target.text.upper("soup") == "SOUP"
    assert target.results.history[:1] == (("text.upper", "STEW"),)
    assert source.results.history == (("text.upper", "STEW"),)


def test_self_fold_uses_existing_operation_without_polluting_results():
    giso = Giso("tests.ingredients.basic")
    before = giso.results.history

    ingredient = giso << "kitchen make_extra"

    assert isinstance(ingredient, Giso)
    assert giso.spice.paprika("pot") == "pot:paprika"
    assert before == ()
    assert ("kitchen.make_extra", ingredient) not in giso.results.history
    assert ("spice.paprika", "seed:paprika") in giso.results.history
    assert giso.results.last == "pot:paprika"
