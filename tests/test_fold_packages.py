from pathlib import Path

from giso import Giso


def write_python(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def build_package(tmp_path: Path) -> Path:
    package = tmp_path / "tools"
    write_python(
        package / "__init__.py",
        "def version() -> str:\n"
        "    return '1.0'\n",
    )
    write_python(
        package / "math.py",
        "def double(value: int) -> int:\n"
        "    return value * 2\n",
    )
    write_python(
        package / "shared.py",
        "def offset() -> int:\n"
        "    return 3\n",
    )
    write_python(
        package / "relative.py",
        "from .shared import offset\n\n"
        "def shifted(value: int) -> int:\n"
        "    return value + offset()\n",
    )
    write_python(
        package / "text" / "__init__.py",
        "def label() -> str:\n"
        "    return 'text'\n",
    )
    write_python(
        package / "text" / "format.py",
        "def slugify(value: str) -> str:\n"
        "    return value.lower().replace(' ', '-')\n",
    )
    return package


def test_package_directory_preserves_module_hierarchy(tmp_path: Path):
    package = build_package(tmp_path)

    giso = Giso(package)

    assert giso.tools.version() == "1.0"
    assert giso.tools.math.double(4) == 8
    assert giso.tools.text.label() == "text"
    assert giso.tools.text.format.slugify("Hello Giso") == "hello-giso"
    assert set(giso.operations) >= {
        "tools.version",
        "tools.math.double",
        "tools.text.label",
        "tools.text.format.slugify",
    }


def test_package_modules_support_relative_imports(tmp_path: Path):
    package = build_package(tmp_path)

    giso = Giso(package)

    assert giso.tools.relative.shifted(4) == 7
    assert "tools.relative.shifted" in giso.operations


def test_package_directory_string_path_uses_package_aware_folding(tmp_path: Path):
    package = build_package(tmp_path)

    giso = Giso(str(package))

    assert giso.tools.math.double(5) == 10
    assert giso.tools.text.format.slugify("String Path") == "string-path"


def test_package_fold_skips_private_modules_and_private_subpackages(tmp_path: Path):
    package = build_package(tmp_path)
    write_python(
        package / "_private.py",
        "def secret() -> str:\n"
        "    return 'secret'\n",
    )
    write_python(package / "_hidden" / "__init__.py", "")
    write_python(
        package / "_hidden" / "inside.py",
        "def hidden() -> str:\n"
        "    return 'hidden'\n",
    )

    giso = Giso(package)

    assert "tools._private.secret" not in giso.operations
    assert "tools._hidden.inside.hidden" not in giso.operations
    assert giso.tools.math.double(2) == 4


def test_plain_directory_keeps_flat_directory_semantics(tmp_path: Path):
    directory = tmp_path / "ingredients"
    write_python(
        directory / "math_tools.py",
        "def math__triple(value: int) -> int:\n"
        "    return value * 3\n",
    )

    giso = Giso(directory)

    assert giso.math.triple(4) == 12
    assert "ingredients.math_tools.math.triple" not in giso.operations
