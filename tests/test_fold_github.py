from __future__ import annotations

import io
import pathlib
import tarfile

import pytest

from giso import Giso


def make_repository_archive(tmp_path: pathlib.Path) -> pathlib.Path:
    source = tmp_path / "source"
    root = source / "example-deadbeef"
    package = root / "tools"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "def version():\n    return '1.0'\n",
        encoding="utf-8",
    )
    (package / "math.py").write_text(
        "def double(value):\n    return value * 2\n",
        encoding="utf-8",
    )
    nested = root / "extras" / "plugin"
    nested.mkdir(parents=True)
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (nested / "feature.py").write_text(
        "def enabled():\n    return True\n",
        encoding="utf-8",
    )

    archive = tmp_path / "repository.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(root, arcname=root.name)
    return archive


def configure_resolver(monkeypatch, archive: pathlib.Path, *, default_branch: str = "main"):
    monkeypatch.setattr(Giso, "_github_default_branch", lambda self, owner, repo: default_branch)
    monkeypatch.setattr(
        Giso,
        "_github_resolve_commit",
        lambda self, owner, repo, ref: "deadbeef",
    )
    monkeypatch.setattr(
        Giso,
        "_github_archive",
        lambda self, owner, repo, commit: archive,
    )


def test_github_source_uses_default_branch_and_folds_repository(tmp_path, monkeypatch):
    archive = make_repository_archive(tmp_path)
    configure_resolver(monkeypatch, archive)

    giso = Giso("github:owner/example")

    assert giso.tools.version() == "1.0"
    assert giso.tools.math.double(4) == 8
    assert giso.provenance == [
        {
            "type": "github",
            "source": "github:owner/example",
            "requested_ref": "",
            "resolved_ref": "main",
            "commit": "deadbeef",
            "subdirectory": "",
        }
    ]


def test_github_source_accepts_explicit_ref_with_slashes(tmp_path, monkeypatch):
    archive = make_repository_archive(tmp_path)
    seen = []
    monkeypatch.setattr(
        Giso,
        "_github_default_branch",
        lambda self, owner, repo: pytest.fail("explicit ref must not request default branch"),
    )
    monkeypatch.setattr(
        Giso,
        "_github_resolve_commit",
        lambda self, owner, repo, ref: seen.append(ref) or "deadbeef",
    )
    monkeypatch.setattr(Giso, "_github_archive", lambda self, owner, repo, commit: archive)

    giso = Giso("github:owner/example@feature/package-folding")

    assert seen == ["feature/package-folding"]
    assert giso.tools.math.double(3) == 6
    assert giso.provenance[0]["requested_ref"] == "feature/package-folding"


def test_github_source_can_select_repository_subdirectory(tmp_path, monkeypatch):
    archive = make_repository_archive(tmp_path)
    configure_resolver(monkeypatch, archive)

    giso = Giso("github:owner/example#extras/plugin")

    assert giso.plugin.feature.enabled() is True
    assert "tools.math.double" not in giso.operations
    assert giso.provenance[0]["subdirectory"] == "extras/plugin"


def test_github_source_combines_ref_and_subdirectory(tmp_path, monkeypatch):
    archive = make_repository_archive(tmp_path)
    configure_resolver(monkeypatch, archive)

    giso = Giso("github:owner/example@v1.2.0#extras/plugin")

    assert giso.plugin.feature.enabled() is True
    assert giso.provenance[0]["requested_ref"] == "v1.2.0"


def test_github_archive_cache_reuses_commit_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("GISO_CACHE_DIR", str(tmp_path / "cache"))
    cached = tmp_path / "cache" / "github" / "owner" / "example" / "deadbeef.tar.gz"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached")

    def fail_urlopen(*args, **kwargs):
        pytest.fail("cached commit must not be downloaded again")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)

    assert Giso()._github_archive("owner", "example", "deadbeef") == cached


def test_github_provenance_survives_giso_folding(tmp_path, monkeypatch):
    archive = make_repository_archive(tmp_path)
    configure_resolver(monkeypatch, archive)
    source = Giso("github:owner/example@main")

    folded = Giso(source)

    assert folded.tools.math.double(5) == 10
    assert folded.provenance == source.provenance


@pytest.mark.parametrize(
    "source",
    [
        "github:",
        "github:owner",
        "github:owner/repo@",
        "github:owner/repo#",
        "github:owner/repo#../secret",
    ],
)
def test_invalid_github_sources_are_rejected(source):
    with pytest.raises(ValueError):
        Giso(source)


def test_missing_github_subdirectory_is_reported(tmp_path, monkeypatch):
    archive = make_repository_archive(tmp_path)
    configure_resolver(monkeypatch, archive)

    with pytest.raises(ValueError, match="does not exist"):
        Giso("github:owner/example#missing")
