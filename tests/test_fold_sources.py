from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from giso import Giso
from tests.ingredients import basic


def collection__callable(value: str) -> str:
    return f"callable:{value}"


class CollectionTool:
    def use(self, value: str) -> str:
        return f"class:{value}"


def nested__giso(value: str) -> str:
    return f"giso:{value}"


def test_constructor_folds_module_object():
    assert isinstance(basic, ModuleType)

    giso = Giso(basic)

    assert giso.math.double(4) == 8
    assert giso.text.upper("giso") == "GISO"
    assert giso.greeter.hello("Ada") == "hello Ada"


def test_fold_mutates_existing_giso_with_module_object():
    giso = Giso()

    returned = giso.fold(basic)

    assert returned is giso
    assert giso.math.double(5) == 10
    assert giso.text.upper("fold") == "FOLD"
    assert giso.greeter.hello("Grace") == "hello Grace"


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


def test_directory_fold_recursively_loads_public_python_files(tmp_path: Path):
    ingredients = tmp_path / "ingredients"
    nested = ingredients / "nested"
    nested.mkdir(parents=True)

    (ingredients / "math_tools.py").write_text(
        "def math__triple(value: int) -> int:\n"
        "    return value * 3\n",
        encoding="utf-8",
    )
    (ingredients / "text_tools.py").write_text(
        "def text__lower(value: str) -> str:\n"
        "    return value.lower()\n",
        encoding="utf-8",
    )
    (nested / "diagram.py").write_text(
        "class DiagramTool:\n"
        "    def validate(self, value: str) -> str:\n"
        "        return f'valid:{value}'\n",
        encoding="utf-8",
    )

    giso = Giso(ingredients)

    assert giso.math.triple(4) == 12
    assert giso.text.lower("GISO") == "giso"
    assert giso.diagram_tool.validate("drawing") == "valid:drawing"
    assert len(giso.modules) == 3


def test_directory_fold_skips_underscore_python_files_and_non_python_files(tmp_path: Path):
    ingredients = tmp_path / "ingredients"
    nested = ingredients / "nested"
    nested.mkdir(parents=True)

    (ingredients / "public.py").write_text(
        "def public__value() -> str:\n"
        "    return 'public'\n",
        encoding="utf-8",
    )
    (ingredients / "__init__.py").write_text(
        "def package__value() -> str:\n"
        "    return 'package'\n",
        encoding="utf-8",
    )
    (ingredients / "_private.py").write_text(
        "def private__value() -> str:\n"
        "    return 'private'\n",
        encoding="utf-8",
    )
    (nested / "_hidden.py").write_text(
        "def hidden__value() -> str:\n"
        "    return 'hidden'\n",
        encoding="utf-8",
    )
    (ingredients / "notes.txt").write_text(
        "def text_file__value():\n"
        "    return 'not python'\n",
        encoding="utf-8",
    )

    giso = Giso()
    returned = giso.fold(ingredients)

    assert returned is giso
    assert giso.public.value() == "public"
    assert "public.value" in giso.operations
    assert "package.value" not in giso.operations
    assert "private.value" not in giso.operations
    assert "hidden.value" not in giso.operations
    assert "text_file.value" not in giso.operations
    assert len(giso.modules) == 1


def test_constructor_folds_existing_python_file_from_string_path(tmp_path: Path):
    ingredient = tmp_path / "ingredient.py"
    ingredient.write_text(
        "def spice__add(value: str) -> str:\n"
        "    return value + '-pepper'\n",
        encoding="utf-8",
    )

    giso = Giso(str(ingredient))

    assert giso.spice.add("stew") == "stew-pepper"
    assert str(ingredient.resolve()) in giso.modules


def test_fold_accepts_existing_directory_from_string_path(tmp_path: Path):
    ingredients = tmp_path / "ingredients"
    ingredients.mkdir()
    (ingredients / "one.py").write_text(
        "def one__value() -> int:\n"
        "    return 1\n",
        encoding="utf-8",
    )
    (ingredients / "two.py").write_text(
        "def two__value() -> int:\n"
        "    return 2\n",
        encoding="utf-8",
    )

    giso = Giso()
    returned = giso.fold(str(ingredients))

    assert returned is giso
    assert giso.one.value() == 1
    assert giso.two.value() == 2
    assert len(giso.modules) == 2


def test_existing_string_path_takes_precedence_over_module_import(tmp_path: Path, monkeypatch):
    ingredient = tmp_path / "collision.py"
    ingredient.write_text(
        "def source__kind() -> str:\n"
        "    return 'path'\n",
        encoding="utf-8",
    )

    import_root = tmp_path / "imports"
    import_root.mkdir()
    (import_root / "collision.py").write_text(
        "def source__kind() -> str:\n"
        "    return 'module'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(import_root))
    monkeypatch.chdir(tmp_path)

    giso = Giso("collision.py")

    assert giso.source.kind() == "path"
    assert str(ingredient.resolve()) in giso.modules


@pytest.mark.parametrize(
    "source",
    [
        "pantry.herbs",
        "pantry/herbs",
        "pantry/herbs.py",
    ],
)
def test_module_string_variants_resolve_same_importable_module(
    source: str, tmp_path: Path, monkeypatch
):
    package = tmp_path / "pantry"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "herbs.py").write_text(
        "def herbs__add(value: str) -> str:\n"
        "    return value + '-oregano'\n",
        encoding="utf-8",
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(elsewhere)

    giso = Giso(source)

    assert giso.herbs.add("stew") == "stew-oregano"
    assert "pantry.herbs" in giso.modules


def test_nested_collections_fold_all_supported_source_shapes(tmp_path: Path):
    ingredient = tmp_path / "ingredient.py"
    ingredient.write_text(
        "def path__value(value: str) -> str:\n"
        "    return f'path:{value}'\n",
        encoding="utf-8",
    )
    other = Giso(nested__giso)

    sources = [
        collection__callable,
        (
            CollectionTool,
            [
                basic,
                {
                    ingredient,
                    other,
                },
            ],
        ),
    ]

    giso = Giso(sources)

    assert giso.collection.callable("x") == "callable:x"
    assert giso.collection_tool.use("x") == "class:x"
    assert giso.math.double(3) == 6
    assert giso.path.value("x") == "path:x"
    assert giso.nested.giso("x") == "giso:x"


def test_nested_collections_ignore_none_at_any_depth():
    giso = Giso(
        [
            None,
            (collection__callable, [None, {None, CollectionTool}]),
            None,
        ]
    )

    assert giso.collection.callable("x") == "callable:x"
    assert giso.collection_tool.use("x") == "class:x"
