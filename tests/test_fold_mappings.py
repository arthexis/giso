from __future__ import annotations

import pytest

from giso import Giso


def double(value: int) -> int:
    return value * 2


def upper(value: str) -> str:
    return value.upper()


def paprika(value: str) -> str:
    return f"{value}:paprika"


class StatefulTool:
    def __init__(self, prefix: str):
        self.prefix = prefix

    def render(self, value: str) -> str:
        return f"{self.prefix}:{value}"


def test_mapping_keys_define_callable_capability_paths():
    giso = Giso(
        {
            "math": {"double": double},
            "text": {"upper": upper},
        }
    )

    assert giso.math.double(4) == 8
    assert giso.text.upper("giso") == "GISO"
    assert set(giso.operations) == {"math.double", "text.upper"}


def test_mapping_supports_arbitrarily_deep_namespace_trees():
    giso = Giso({"tools": {"math": {"double": double}}})

    assert giso.tools.math.double(5) == 10
    assert giso["tools.math.double"](6) == 12
    assert "tools.math.double" in giso.operations


def test_callable_leaf_uses_mapping_key_instead_of_function_name():
    giso = Giso({"math": {"times_two": double}})

    assert giso.math.times_two(7) == 14
    assert "math.times_two" in giso.operations
    assert "math.double" not in giso.operations


def test_richer_fold_source_keeps_its_surface_under_mapping_prefix():
    tool = StatefulTool("configured")
    giso = Giso({"service": tool})

    assert giso.service.stateful_tool.render("item") == "configured:item"
    tool.prefix = "changed"
    assert giso.service.stateful_tool.render("item") == "changed:item"
    assert "service.stateful_tool.render" in giso.operations


def test_giso_leaf_prefixes_operations_and_existing_result_history():
    source = Giso(paprika)
    source.paprika("seed")

    target = Giso({"extras": source})

    assert target.results.history == (("extras.paprika", "seed:paprika"),)
    assert target.extras.paprika("stew") == "stew:paprika"
    assert target.results.history[-1] == ("extras.paprika", "stew:paprika")
    assert source.results.history == (("paprika", "seed:paprika"),)


def test_mapping_ignores_none_leaf_values():
    giso = Giso({"math": {"double": double, "missing": None}})

    assert giso.math.double(3) == 6
    assert not hasattr(giso.math, "missing")


@pytest.mark.parametrize("key", [1, "", "not-valid", "_private", "has.dot"])
def test_mapping_rejects_non_public_identifier_keys(key):
    with pytest.raises(ValueError, match="Mapping keys must be public Python identifiers"):
        Giso({key: double})


def test_mapping_propagates_unsupported_leaf_errors():
    with pytest.raises(TypeError, match="Unsupported source type: object"):
        Giso({"bad": object()})


def test_mapping_has_constructor_fold_iadd_and_add_parity():
    source = {"math": {"double": double}}

    constructed = Giso(source)
    folded = Giso()
    returned = folded.fold(source)
    augmented = Giso()
    original_id = id(augmented)
    augmented += source
    original = Giso(upper)
    derived = original + source

    assert returned is folded
    assert id(augmented) == original_id
    assert constructed.math.double(2) == 4
    assert folded.math.double(2) == 4
    assert augmented.math.double(2) == 4
    assert derived.math.double(2) == 4
    assert original.upper("giso") == "GISO"
    assert not hasattr(original, "math")
