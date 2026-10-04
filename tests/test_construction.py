from __future__ import annotations

import pytest

from giso import Giso


def root_status() -> str:
    return "root"


def protocol_status() -> str:
    return "protocol"


def diagnostics_status() -> str:
    return "diagnostics"


def meter_read() -> int:
    return 42


def test_named_single_ingredient_builds_prefixed_branch():
    giso = Giso(charger=protocol_status)

    assert "charger.protocol_status" in giso.operations
    assert giso.charger.protocol_status() == "protocol"


def test_named_list_and_tuple_fold_multiple_ingredients_into_same_branch():
    from_list = Giso(charger=[diagnostics_status, meter_read])
    from_tuple = Giso(charger=(diagnostics_status, meter_read))

    for giso in (from_list, from_tuple):
        assert giso.charger.diagnostics_status() == "diagnostics"
        assert giso.charger.meter_read() == 42


def test_root_ingredients_and_named_branches_can_coexist():
    giso = Giso(root_status, charger=(protocol_status, meter_read))

    assert giso.root_status() == "root"
    assert giso.charger.protocol_status() == "protocol"
    assert giso.charger.meter_read() == 42


def test_prepared_ingredients_normalize_list_and_tuple_equally():
    list_plan = Giso._prepare_ingredients((), {"charger": [protocol_status, meter_read]})
    tuple_plan = Giso._prepare_ingredients((), {"charger": (protocol_status, meter_read)})

    assert list_plan == tuple_plan


def test_nested_mapping_builds_recursive_branches():
    giso = Giso(
        charger={
            "protocol": protocol_status,
            "diagnostics": [diagnostics_status, meter_read],
        },
        system={"status": root_status},
    )

    assert giso.charger.protocol.protocol_status() == "protocol"
    assert giso.charger.diagnostics.diagnostics_status() == "diagnostics"
    assert giso.charger.diagnostics.meter_read() == 42
    assert giso.system.status.root_status() == "root"


def test_explicit_nested_giso_and_mapping_are_semantically_equivalent():
    explicit = Giso(
        charger=Giso(
            protocol=protocol_status,
            diagnostics=(diagnostics_status, meter_read),
        )
    )
    implicit = Giso(
        charger={
            "protocol": protocol_status,
            "diagnostics": [diagnostics_status, meter_read],
        }
    )

    assert set(explicit.operations) == set(implicit.operations)
    assert explicit.charger.protocol.protocol_status() == implicit.charger.protocol.protocol_status()
    assert explicit.charger.diagnostics.diagnostics_status() == implicit.charger.diagnostics.diagnostics_status()
    assert explicit.charger.diagnostics.meter_read() == implicit.charger.diagnostics.meter_read()


def test_nested_giso_results_are_prefixed_when_mounted():
    child = Giso(protocol=protocol_status)
    assert child.protocol.protocol_status() == "protocol"

    parent = Giso(charger=child)

    assert parent.results["charger.protocol.protocol_status"] == "protocol"
    assert parent.charger.protocol.protocol_status() == "protocol"


@pytest.mark.parametrize("branch_name", ["_private", "bad-name", ""])
def test_branch_names_must_be_public_python_identifiers(branch_name):
    with pytest.raises(ValueError, match="public Python identifiers"):
        Giso(**{branch_name: protocol_status})
