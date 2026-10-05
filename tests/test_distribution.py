from __future__ import annotations

from types import ModuleType

import pytest

from giso import Giso
import giso.distribution as distribution_module


class FakeDistribution:
    def __init__(self, name="Example-Tools", version="1.2.3", top_level=None, files=()):
        self.metadata = {"Name": name}
        self.version = version
        self._top_level = top_level
        self.files = files

    def read_text(self, filename):
        if filename == "top_level.txt":
            return self._top_level
        return None


def module_with_status(name, value):
    module = ModuleType(name)
    exec(f"def status():\n    return {value!r}\n", module.__dict__)
    return module


def test_distribution_resolves_packages_from_packages_distributions(monkeypatch):
    dist = FakeDistribution()
    monkeypatch.setattr(distribution_module.metadata, "distribution", lambda name: dist)
    monkeypatch.setattr(
        distribution_module.metadata,
        "packages_distributions",
        lambda: {
            "example_tools": ["example_tools"],
            "example_helpers": ["Example.Tools"],
            "other": ["something-else"],
        },
    )

    resolved = Giso._resolve_distribution("example-tools")

    assert resolved.name == "Example-Tools"
    assert resolved.version == "1.2.3"
    assert resolved.packages == ("example_helpers", "example_tools")


def test_distribution_falls_back_to_top_level_metadata(monkeypatch):
    dist = FakeDistribution(top_level="example_tools\nexample_helpers\n_private\nnot-valid\n")
    monkeypatch.setattr(distribution_module.metadata, "distribution", lambda name: dist)
    monkeypatch.setattr(distribution_module.metadata, "packages_distributions", lambda: {})

    resolved = Giso._resolve_distribution("Example.Tools")

    assert resolved.packages == ("example_helpers", "example_tools")


def test_distribution_falls_back_to_distribution_files(monkeypatch):
    dist = FakeDistribution(
        files=(
            "example_tools/__init__.py",
            "example_helpers.py",
            "Example_Tools-1.2.3.dist-info/METADATA",
            "data-file.txt",
        )
    )
    monkeypatch.setattr(distribution_module.metadata, "distribution", lambda name: dist)
    monkeypatch.setattr(distribution_module.metadata, "packages_distributions", lambda: {})

    resolved = Giso._resolve_distribution("example-tools")

    assert resolved.packages == ("example_helpers", "example_tools")


def test_distribution_rejects_missing_import_roots(monkeypatch):
    dist = FakeDistribution(files=("Example_Tools-1.2.3.dist-info/METADATA",))
    monkeypatch.setattr(distribution_module.metadata, "distribution", lambda name: dist)
    monkeypatch.setattr(distribution_module.metadata, "packages_distributions", lambda: {})

    with pytest.raises(ValueError, match="exposes no importable"):
        Giso._resolve_distribution("example-tools")


def test_distribution_direct_method_uses_existing_module_fold(monkeypatch):
    dist = FakeDistribution()
    modules = {"example_tools": module_with_status("example_tools", "ready")}
    monkeypatch.setattr(distribution_module.metadata, "distribution", lambda name: dist)
    monkeypatch.setattr(
        distribution_module.metadata,
        "packages_distributions",
        lambda: {"example_tools": ["Example-Tools"]},
    )
    monkeypatch.setattr(distribution_module.importlib, "import_module", modules.__getitem__)

    giso = Giso().distribution("example-tools")

    assert giso.status() == "ready"


def test_dist_string_routes_to_distribution_resolver(monkeypatch):
    dist = FakeDistribution()
    modules = {"example_tools": module_with_status("example_tools", "resolved")}
    monkeypatch.setattr(distribution_module.metadata, "distribution", lambda name: dist)
    monkeypatch.setattr(
        distribution_module.metadata,
        "packages_distributions",
        lambda: {"example_tools": ["Example-Tools"]},
    )
    monkeypatch.setattr(distribution_module.importlib, "import_module", modules.__getitem__)

    giso = Giso("dist:example-tools")

    assert giso.status() == "resolved"


def test_distribution_name_must_be_non_empty():
    with pytest.raises(TypeError, match="non-empty string"):
        Giso._resolve_distribution("")
