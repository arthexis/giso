from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from giso import Giso


def package_files() -> dict[str, str]:
    return {
        "tools/__init__.py": "def version():\n    return '1.0'\n",
        "tools/math.py": "def double(value):\n    return value * 2\n",
        "tools/data.txt": "archive-data\n",
        "tools/resource.py": (
            "from pathlib import Path\n\n"
            "def read_data():\n"
            "    return Path(__file__).with_name('data.txt').read_text().strip()\n"
        ),
        "tools/text/__init__.py": "",
        "tools/text/format.py": (
            "def slugify(value):\n"
            "    return value.lower().replace(' ', '-')\n"
        ),
    }


def write_zip(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def write_tar(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            payload = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def test_zip_archive_folds_package_hierarchy(tmp_path: Path):
    archive = write_zip(tmp_path / "tools.zip", package_files())

    giso = Giso(archive)

    assert giso.tools.version() == "1.0"
    assert giso.tools.math.double(4) == 8
    assert giso.tools.text.format.slugify("Hello Giso") == "hello-giso"


def test_archive_extraction_survives_for_runtime_resources(tmp_path: Path):
    archive = write_zip(tmp_path / "tools.zip", package_files())

    giso = Giso(archive)

    assert giso.tools.resource.read_data() == "archive-data"


def test_wheel_folds_python_package_and_ignores_metadata(tmp_path: Path):
    files = package_files()
    files.update(
        {
            "example-1.0.dist-info/METADATA": "Name: example\nVersion: 1.0\n",
            "example-1.0.data/scripts/metadata_tool.py": (
                "def metadata__should_not_load():\n"
                "    return 'metadata'\n"
            ),
        }
    )
    wheel = write_zip(tmp_path / "example-1.0-py3-none-any.whl", files)

    giso = Giso(str(wheel))

    assert giso.tools.math.double(3) == 6
    assert "metadata.should_not_load" not in giso.operations


def test_tar_gz_archive_folds_package_hierarchy(tmp_path: Path):
    archive = write_tar(tmp_path / "tools.tar.gz", package_files())

    giso = Giso(archive)

    assert giso.tools.math.double(5) == 10
    assert giso.tools.resource.read_data() == "archive-data"


def test_archive_can_contain_multiple_top_level_packages(tmp_path: Path):
    files = package_files()
    files.update(
        {
            "extra/__init__.py": "",
            "extra/value.py": "def answer():\n    return 42\n",
        }
    )
    archive = write_zip(tmp_path / "bundle.zip", files)

    giso = Giso(archive)

    assert giso.tools.math.double(2) == 4
    assert giso.extra.value.answer() == 42


def test_zip_archive_rejects_parent_traversal(tmp_path: Path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../escape.py", "def escape(): return True\n")

    with pytest.raises(ValueError, match="Unsafe archive entry"):
        Giso(archive)


def test_tar_archive_rejects_links(tmp_path: Path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as tarred:
        link = tarfile.TarInfo("tools/link.py")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../escape.py"
        tarred.addfile(link)

    with pytest.raises(ValueError, match="Archive links are not supported"):
        Giso(archive)
