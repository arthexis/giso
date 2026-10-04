from __future__ import annotations

import pathlib
import zipfile

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


def test_live_source_in_named_collection_coexists_with_static_ingredients():
    requests: list[str] = []
    giso = Giso(charger=[protocol_status, live_provider(requests)])

    assert giso.charger.protocol_status() == "protocol"
    assert requests == []
    assert giso.charger.live_status() == "live"
    assert requests == ["live_status"]


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


def test_branch_collisions_follow_ordinary_fold_order():
    def first():
        return "first"

    def second():
        return "second"

    first.__name__ = "status"
    second.__name__ = "status"

    ordinary = Giso(first, second)
    branched = Giso(charger=(first, second))

    assert ordinary.status() == "second"
    assert branched.charger.status() == ordinary.status()


def test_branch_mount_preserves_provenance_without_rewriting_source_identity():
    child = Giso(protocol_status)
    child.provenance.append(
        {
            "type": "github",
            "source": "github:arthexis/example",
            "commit": "abc123",
        }
    )

    parent = Giso(charger={"protocol": child})

    assert parent.provenance == child.provenance
    assert parent.provenance[0]["source"] == "github:arthexis/example"
    assert "branch" not in parent.provenance[0]


def test_parent_calls_record_only_prefixed_result_names():
    child = Giso(protocol_status)
    child.protocol_status()
    parent = Giso(charger=child)

    assert parent.charger.protocol_status() == "protocol"

    assert child.results.history == (("protocol_status", "protocol"),)
    assert parent.results.history == (
        ("charger.protocol_status", "protocol"),
        ("charger.protocol_status", "protocol"),
    )


def test_static_hierarchical_tree_survives_copy_and_add():
    original = Giso(charger={"protocol": protocol_status})

    copied = Giso(original)
    derived = original + meter_read

    assert copied.charger.protocol.protocol_status() == "protocol"
    assert derived.charger.protocol.protocol_status() == "protocol"
    assert derived.meter_read() == 42
    assert "meter_read" not in original.operations


def test_ordinary_giso_copy_does_not_clone_branch_live_iterator_state():
    requests: list[str] = []
    child = Giso(live_provider(requests))
    parent = Giso(charger=child)
    copied = Giso(parent)

    with pytest.raises(AttributeError):
        _ = copied.charger

    assert requests == []
    assert parent.charger.live_status() == "live"
    assert requests == ["live_status"]


def test_branch_mount_preserves_archive_root_lifetime(tmp_path: pathlib.Path):
    archive = tmp_path / "tools.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("tool.py", "def archive_status():\n    return 'archive'\n")

    giso = Giso(charger=archive)

    assert giso.charger.archive_status() == "archive"
    assert len(giso._archive_roots) == 1
    assert pathlib.Path(giso._archive_roots[0].name).exists()
