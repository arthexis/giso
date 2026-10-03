from __future__ import annotations

from pathlib import Path
from types import ModuleType

from giso import Giso
from tests.ingredients import basic


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
