from giso import Giso


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
