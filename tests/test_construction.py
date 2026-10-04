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


def live_status() -> str:
    return "live"


def live_provider(requests: list[str]):
    request = yield
    while True:
        requests.append(request.path)
        if request.path == "live_status":
            request = yield live_status
        else:
            request = yield None


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


@pytest.mark.parametrize("branch_name", ["_private", "bad-name", ""])
def test_branch_names_must_be_public_python_identifiers(branch_name):
    with pytest.raises(ValueError, match="public Python identifiers"):
        Giso(**{branch_name: protocol_status})


def test_nested_mapping_and_explicit_giso_are_equivalent():
    implicit = Giso(
        charger={
            "protocol": protocol_status,
            "diagnostics": [diagnostics_status, meter_read],
        }
    )
    explicit = Giso(
        charger=Giso(
            protocol=protocol_status,
            diagnostics=(diagnostics_status, meter_read),
        )
    )

    assert set(implicit.operations) == set(explicit.operations)
    assert implicit.charger.protocol.protocol_status() == "protocol"
    assert explicit.charger.protocol.protocol_status() == "protocol"
    assert implicit.charger.diagnostics.meter_read() == 42
    assert explicit.charger.diagnostics.meter_read() == 42


def test_preexecuted_nested_giso_result_history_is_prefixed():
    child = Giso(protocol_status)
    child.protocol_status()

    parent = Giso(charger=child)

    assert parent.results.history == (("charger.protocol_status", "protocol"),)


def test_live_source_in_named_branch_stays_deferred_and_receives_relative_path():
    requests: list[str] = []
    giso = Giso(charger=live_provider(requests))

    assert requests == []
    assert giso.charger.live_status() == "live"
    assert requests == ["live_status"]
    assert "charger.live_status" in giso.operations


def test_live_source_inside_nested_mapping_resolves_under_full_branch_path():
    requests: list[str] = []
    giso = Giso(charger={"diagnostics": live_provider(requests)})

    assert giso.charger.diagnostics.live_status() == "live"
    assert requests == ["live_status"]
    assert "charger.diagnostics.live_status" in giso.operations


def test_existing_nested_giso_retains_live_source_by_reference():
    requests: list[str] = []
    child = Giso(live_provider(requests))
    parent = Giso(charger=child)

    assert parent.charger.live_status() == "live"
    assert requests == ["live_status"]
    assert "charger.live_status" in parent.operations


def test_unrelated_parent_lookup_does_not_advance_branch_live_source():
    requests: list[str] = []
    giso = Giso(charger=live_provider(requests))

    with pytest.raises(AttributeError):
        _ = giso.missing

    assert requests == []
    assert giso.charger.live_status() == "live"
