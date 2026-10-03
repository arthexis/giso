from pathlib import Path

from giso import Giso


def plain(value: int = 1) -> int:
    return value + 1


def math__double(value: int) -> int:
    return value * 2


def text__upper(value: str) -> str:
    return value.upper()


def text__suffix(value: str, suffix: str = "!") -> str:
    return value + suffix


class DiagramTool:
    def validate(self, value: str) -> str:
        return f"valid:{value}"


def test_constructor_folds_plain_callable():
    giso = Giso(plain)

    assert giso.plain(2) == 3
    assert getattr(giso.operations["plain"], "__giso_original__") is plain


def test_double_underscore_creates_namespace():
    giso = Giso(math__double)

    assert giso.math.double(4) == 8
    assert "math.double" in giso.operations


def test_class_becomes_namespace():
    giso = Giso(DiagramTool)

    assert giso.diagram_tool.validate("drawing") == "valid:drawing"
    assert "diagram_tool.validate" in giso.operations


def test_fold_mutates_existing_instance():
    giso = Giso()
    assert not hasattr(giso, "plain")

    returned = giso.fold(plain)

    assert returned is giso
    assert giso.plain(9) == 10


def test_constructor_folds_python_file(tmp_path: Path):
    module = tmp_path / "ingredient.py"
    module.write_text(
        "def spice__add(value: str) -> str:\n"
        "    return value + '-paprika'\n",
        encoding="utf-8",
    )

    giso = Giso(module)

    assert giso.spice.add("stew") == "stew-paprika"


def test_constructor_folds_fully_qualified_module_string(tmp_path: Path, monkeypatch):
    package = tmp_path / "ingredients"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "spice.py").write_text(
        "def spice__add(value: str) -> str:\n"
        "    return value + '-cumin'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    giso = Giso("ingredients.spice")

    assert giso.spice.add("stew") == "stew-cumin"
    assert "ingredients.spice" in giso.modules


def test_constructor_folds_slash_style_module_string(tmp_path: Path, monkeypatch):
    package = tmp_path / "pantry"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "herbs.py").write_text(
        "def herbs__add(value: str) -> str:\n"
        "    return value + '-oregano'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    giso = Giso("pantry/herbs")

    assert giso.herbs.add("stew") == "stew-oregano"
    assert "pantry.herbs" in giso.modules


def test_ignores_imported_callables_when_folding_module(tmp_path: Path):
    module = tmp_path / "ingredient.py"
    module.write_text(
        "from pathlib import Path\n\n"
        "def local() -> str:\n"
        "    return 'mine'\n",
        encoding="utf-8",
    )

    giso = Giso(module)

    assert giso.local() == "mine"
    assert "Path" not in giso.operations


def test_fold_accepts_another_giso():
    target = Giso(math__double)
    ingredient = Giso(text__upper)

    returned = target.fold(ingredient)

    assert returned is target
    assert target.math.double(3) == 6
    assert target.text.upper("stew") == "STEW"
    assert ingredient.text.upper("soup") == "SOUP"


def test_folding_giso_copies_multiple_namespaces():
    ingredient = Giso(math__double, text__upper, DiagramTool)
    target = Giso(plain)

    target.fold(ingredient)

    assert target.plain(3) == 4
    assert target.math.double(4) == 8
    assert target.text.upper("giso") == "GISO"
    assert target.diagram_tool.validate("shape") == "valid:shape"
    assert set(target.operations) >= {
        "plain",
        "math.double",
        "text.upper",
        "diagram_tool.validate",
    }


def test_folding_giso_copies_result_history_in_order():
    ingredient = Giso(math__double, text__upper, text__suffix)
    ingredient.math.double(3)
    ingredient.text.upper("fold")
    ingredient.text.suffix("giso", "?")

    target = Giso(plain)
    target.plain(9)
    target.fold(ingredient)

    assert target.results.history == (
        ("plain", 10),
        ("math.double", 6),
        ("text.upper", "FOLD"),
        ("text.suffix", "giso?"),
    )


def test_folding_giso_keeps_results_independent_after_fold():
    ingredient = Giso(text__upper)
    ingredient.text.upper("before")
    target = Giso(ingredient)

    assert target.text is not ingredient.text
    assert target.results.history == (("text.upper", "BEFORE"),)

    target.text.upper("target")
    ingredient.text.upper("ingredient")

    assert target.results.history == (
        ("text.upper", "BEFORE"),
        ("text.upper", "TARGET"),
    )
    assert ingredient.results.history == (
        ("text.upper", "BEFORE"),
        ("text.upper", "INGREDIENT"),
    )


def test_self_fold_is_explicit_no_op():
    giso = Giso(math__double, text__upper)
    giso.math.double(2)
    operations_before = tuple(giso.operations)
    history_before = giso.results.history
    namespaces_before = dict(giso.namespaces)

    returned = giso.fold(giso)

    assert returned is giso
    assert tuple(giso.operations) == operations_before
    assert giso.results.history == history_before
    assert giso.namespaces == namespaces_before
    assert giso.math.double(5) == 10


def test_plus_can_combine_two_gisos_without_mutating_either():
    left = Giso(math__double)
    right = Giso(text__upper)

    combined = left + right

    assert combined is not left
    assert combined is not right
    assert combined.math.double(4) == 8
    assert combined.text.upper("giso") == "GISO"
    assert not hasattr(left, "text")
    assert not hasattr(right, "math")
