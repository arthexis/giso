from pathlib import Path

import pytest

from giso import Giso


def valid__value() -> str:
    return "valid"


def test_fold_rejects_unsupported_object_type():
    with pytest.raises(TypeError, match="Unsupported source type: object"):
        Giso().fold(object())


def test_fold_rejects_missing_path_or_module_string():
    with pytest.raises(ValueError, match="Cannot fold path or import module"):
        Giso().fold("definitely_missing_giso_ingredient")


def test_fold_rejects_existing_non_python_file(tmp_path: Path):
    ingredient = tmp_path / "ingredient.txt"
    ingredient.write_text("not python", encoding="utf-8")

    with pytest.raises(ValueError, match="Cannot fold path"):
        Giso().fold(ingredient)


def test_fold_rejects_empty_string():
    with pytest.raises(ValueError, match="Cannot fold an empty module name"):
        Giso().fold("")


def test_nested_collection_propagates_unsupported_source_error():
    giso = Giso()

    with pytest.raises(TypeError, match="Unsupported source type: int"):
        giso.fold([valid__value, (None, [42])])

    # Folding is incremental: valid sources visited before the bad value remain.
    assert giso.valid.value() == "valid"
