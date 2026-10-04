from __future__ import annotations

import hashlib
import io
import pathlib
import tarfile
import zipfile

import pytest

from giso import Giso


def make_wheel(tmp_path: pathlib.Path) -> pathlib.Path:
    wheel = tmp_path / "demo_pkg-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("demo_pkg/__init__.py", "def version():\n    return '1.2.3'\n")
        archive.writestr("demo_pkg/math.py", "def double(value):\n    return value * 2\n")
        archive.writestr("demo_pkg-1.2.3.dist-info/METADATA", "Name: demo-pkg\nVersion: 1.2.3\n")
    return wheel


def make_sdist(tmp_path: pathlib.Path) -> pathlib.Path:
    source = tmp_path / "sdist"
    package = source / "demo_pkg-1.2.3" / "demo_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("def version():\n    return '1.2.3'\n", encoding="utf-8")
    (package / "text.py").write_text("def upper(value):\n    return value.upper()\n", encoding="utf-8")
    archive = tmp_path / "demo_pkg-1.2.3.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source / "demo_pkg-1.2.3", arcname="demo_pkg-1.2.3")
    return archive


def artifact(path: pathlib.Path, *, packagetype: str) -> dict:
    return {
        "filename": path.name,
        "packagetype": packagetype,
        "url": f"https://files.pythonhosted.org/packages/{path.name}",
        "digests": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
    }


def test_pypi_source_resolves_latest_and_folds_universal_wheel(tmp_path, monkeypatch):
    wheel = make_wheel(tmp_path)
    metadata = {
        "info": {"version": "1.2.3"},
        "urls": [artifact(wheel, packagetype="bdist_wheel")],
    }
    seen = []
    monkeypatch.setattr(Giso, "_pypi_metadata", lambda self, project, version: seen.append((project, version)) or metadata)
    monkeypatch.setattr(Giso, "_pypi_artifact", lambda self, project, version, selected: wheel)

    giso = Giso("pypi:demo-pkg")

    assert seen == [("demo-pkg", None)]
    assert giso.demo_pkg.version() == "1.2.3"
    assert giso.demo_pkg.math.double(4) == 8
    assert giso.provenance[-1]["type"] == "pypi"
    assert giso.provenance[-1]["version"] == "1.2.3"
    assert giso.provenance[-1]["requested_version"] == ""


def test_pypi_source_accepts_explicit_version(tmp_path, monkeypatch):
    wheel = make_wheel(tmp_path)
    metadata = {"info": {"version": "1.2.3"}, "urls": [artifact(wheel, packagetype="bdist_wheel")]}
    seen = []
    monkeypatch.setattr(Giso, "_pypi_metadata", lambda self, project, version: seen.append((project, version)) or metadata)
    monkeypatch.setattr(Giso, "_pypi_artifact", lambda self, project, version, selected: wheel)

    giso = Giso("pypi:demo-pkg@1.2.3")

    assert seen == [("demo-pkg", "1.2.3")]
    assert giso.demo_pkg.math.double(5) == 10
    assert giso.provenance[-1]["requested_version"] == "1.2.3"


def test_pypi_prefers_universal_py3_wheel_over_sdist(tmp_path):
    wheel = make_wheel(tmp_path)
    sdist = make_sdist(tmp_path)
    selected = Giso._select_pypi_artifact(
        "demo-pkg",
        "1.2.3",
        [artifact(sdist, packagetype="sdist"), artifact(wheel, packagetype="bdist_wheel")],
    )
    assert selected["filename"].endswith("-py3-none-any.whl")


def test_pypi_falls_back_to_sdist_for_platform_specific_wheels(tmp_path):
    sdist = make_sdist(tmp_path)
    platform_wheel = {
        "filename": "demo_pkg-1.2.3-cp313-cp313-manylinux_2_28_x86_64.whl",
        "packagetype": "bdist_wheel",
        "url": "https://files.pythonhosted.org/demo.whl",
        "digests": {"sha256": "0" * 64},
    }
    selected = Giso._select_pypi_artifact("demo-pkg", "1.2.3", [platform_wheel, artifact(sdist, packagetype="sdist")])
    assert selected["filename"].endswith(".tar.gz")


def test_pypi_sdist_folds_through_existing_archive_pipeline(tmp_path, monkeypatch):
    sdist = make_sdist(tmp_path)
    metadata = {"info": {"version": "1.2.3"}, "urls": [artifact(sdist, packagetype="sdist")]}
    monkeypatch.setattr(Giso, "_pypi_metadata", lambda self, project, version: metadata)
    monkeypatch.setattr(Giso, "_pypi_artifact", lambda self, project, version, selected: sdist)

    giso = Giso("pypi:demo-pkg@1.2.3")

    assert giso.demo_pkg.text.upper("giso") == "GISO"


def test_pypi_cache_reuses_verified_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GISO_CACHE_DIR", str(tmp_path / "cache"))
    wheel = make_wheel(tmp_path)
    selected = artifact(wheel, packagetype="bdist_wheel")
    cached = tmp_path / "cache" / "pypi" / "demo-pkg" / "1.2.3" / wheel.name
    cached.parent.mkdir(parents=True)
    cached.write_bytes(wheel.read_bytes())

    def fail_urlopen(*args, **kwargs):
        pytest.fail("verified cached PyPI artifact must not be downloaded again")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)

    assert Giso()._pypi_artifact("Demo_Pkg", "1.2.3", selected) == cached


def test_pypi_download_verifies_sha256(tmp_path, monkeypatch):
    monkeypatch.setenv("GISO_CACHE_DIR", str(tmp_path / "cache"))
    selected = {
        "filename": "demo_pkg-1.2.3-py3-none-any.whl",
        "packagetype": "bdist_wheel",
        "url": "https://files.pythonhosted.org/demo.whl",
        "digests": {"sha256": "0" * 64},
    }

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response(b"not-the-advertised-file"))

    with pytest.raises(ValueError, match="SHA256"):
        Giso()._pypi_artifact("demo-pkg", "1.2.3", selected)


def test_pypi_provenance_survives_giso_folding(tmp_path, monkeypatch):
    wheel = make_wheel(tmp_path)
    metadata = {"info": {"version": "1.2.3"}, "urls": [artifact(wheel, packagetype="bdist_wheel")]}
    monkeypatch.setattr(Giso, "_pypi_metadata", lambda self, project, version: metadata)
    monkeypatch.setattr(Giso, "_pypi_artifact", lambda self, project, version, selected: wheel)
    source = Giso("pypi:demo-pkg")

    folded = Giso(source)

    assert folded.demo_pkg.math.double(6) == 12
    assert folded.provenance == source.provenance


@pytest.mark.parametrize("source", ["pypi:", "pypi:@1.0", "pypi:demo@", "pypi:bad/name"])
def test_invalid_pypi_sources_are_rejected(source):
    with pytest.raises(ValueError):
        Giso(source)


def test_pypi_release_without_supported_artifact_is_reported():
    with pytest.raises(ValueError, match="no supported universal wheel or source distribution"):
        Giso._select_pypi_artifact(
            "demo-pkg",
            "1.2.3",
            [
                {
                    "filename": "demo_pkg-1.2.3-cp313-cp313-win_amd64.whl",
                    "packagetype": "bdist_wheel",
                    "url": "https://files.pythonhosted.org/demo.whl",
                    "digests": {"sha256": "0" * 64},
                }
            ],
        )
