from pathlib import Path

from giso import Giso


def plain(value: int = 1) -> int:
    return value + 1


def math__double(value: int) -> int:
    return value * 2


class DiagramTool:
    def validate(self, value: str) -> str:
        return f"valid:{value}"


def test_ingests_plain_callable():
    giso = Giso(plain)

    assert giso.plain(2) == 3
    assert giso.operations["plain"] is plain


def test_double_underscore_creates_namespace():
    giso = Giso(math__double)

    assert giso.math.double(4) == 8
    assert "math.double" in giso.operations


def test_class_becomes_namespace():
    giso = Giso(DiagramTool)

    assert giso.diagram_tool.validate("drawing") == "valid:drawing"
    assert "diagram_tool.validate" in giso.operations


def test_ingest_mutates_existing_instance():
    giso = Giso()
    assert not hasattr(giso, "plain")

    returned = giso.ingest(plain)

    assert returned is giso
    assert giso.plain(9) == 10


def test_ingests_python_file(tmp_path: Path):
    module = tmp_path / "ingredient.py"
    module.write_text(
        "def spice__add(value: str) -> str:\n"
        "    return value + '-paprika'\n",
        encoding="utf-8",
    )

    giso = Giso(module)

    assert giso.spice.add("stew") == "stew-paprika"


def test_ignores_imported_callables_when_ingesting_module(tmp_path: Path):
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
