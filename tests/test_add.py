from pathlib import Path

from giso import Giso
from tests.ingredients import basic


def alpha__one():
    return 1


def alpha__two():
    return 2


def beta():
    return "beta"


def test_iadd_folds_into_same_instance():
    g = Giso()
    original_id = id(g)

    g += alpha__one

    assert id(g) == original_id
    assert g.alpha.one() == 1


def test_add_returns_new_giso_with_existing_and_new_capabilities():
    original = Giso(alpha__one)

    derived = original + beta

    assert derived is not original
    assert original.alpha.one() == 1
    assert derived.alpha.one() == 1
    assert derived.beta() == "beta"
    assert not hasattr(original, "beta")


def test_add_does_not_share_namespace_containers():
    original = Giso(alpha__one)

    derived = original + alpha__two

    assert original.alpha is not derived.alpha
    assert original.alpha.one() == 1
    assert not hasattr(original.alpha, "two")
    assert derived.alpha.one() == 1
    assert derived.alpha.two() == 2


def test_callable_source_has_constructor_fold_and_iadd_parity():
    constructed = Giso(alpha__one)
    folded = Giso()
    returned = folded.fold(alpha__one)
    augmented = Giso()
    original_id = id(augmented)
    augmented += alpha__one

    assert returned is folded
    assert id(augmented) == original_id
    assert constructed.alpha.one() == 1
    assert folded.alpha.one() == 1
    assert augmented.alpha.one() == 1
    assert set(constructed.operations) == set(folded.operations) == set(augmented.operations)


def test_module_source_has_fold_and_add_parity_without_mutating_original():
    folded = Giso(alpha__one)
    returned = folded.fold(basic)

    original = Giso(alpha__one)
    derived = original + basic

    assert returned is folded
    assert folded.math.double(4) == 8
    assert folded.text.upper("giso") == "GISO"
    assert derived.math.double(4) == 8
    assert derived.text.upper("giso") == "GISO"
    assert original.alpha.one() == 1
    assert not hasattr(original, "math")
    assert not hasattr(original, "text")


def test_path_source_has_fold_iadd_and_add_parity(tmp_path: Path):
    ingredient = tmp_path / "ingredient.py"
    ingredient.write_text(
        "def spice__add(value: str) -> str:\n"
        "    return value + '-pepper'\n",
        encoding="utf-8",
    )

    folded = Giso()
    folded.fold(ingredient)

    augmented = Giso()
    augmented += ingredient

    original = Giso(alpha__one)
    derived = original + ingredient

    assert folded.spice.add("stew") == "stew-pepper"
    assert augmented.spice.add("stew") == "stew-pepper"
    assert derived.spice.add("stew") == "stew-pepper"
    assert original.alpha.one() == 1
    assert not hasattr(original, "spice")


def test_giso_source_has_fold_iadd_and_add_parity_without_sharing_results():
    ingredient = Giso(beta)
    ingredient.beta()

    folded = Giso(alpha__one)
    folded.fold(ingredient)

    augmented = Giso(alpha__one)
    augmented += ingredient

    original = Giso(alpha__one)
    derived = original + ingredient

    assert folded.beta() == "beta"
    assert augmented.beta() == "beta"
    assert derived.beta() == "beta"
    assert not hasattr(original, "beta")
    assert ingredient.results.history == (("beta", "beta"),)
    assert folded.results.history[:1] == ingredient.results.history
    assert augmented.results.history[:1] == ingredient.results.history
    assert derived.results.history[:1] == ingredient.results.history
