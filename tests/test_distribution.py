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


def distribution_provenance(*packages):
    return {
        "type": "distribution",
        "source": "dist:Example-Tools",
        "name": "Example-Tools",
        "version": "1.2.3",
        "packages": ",".join(packages),
    }


class DistributionHarness:
    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch

    def metadata(self, *, dist=None, packages=None):
        dist = dist or FakeDistribution()
        packages = packages if packages is not None else {"example_tools": [dist.metadata["Name"]]}
        self.monkeypatch.setattr(distribution_module.metadata, "distribution", lambda name: dist)
        self.monkeypatch.setattr(
            distribution_module.metadata,
            "packages_distributions",
            lambda: packages,
        )
        return dist

    def imports(self, modules):
        self.monkeypatch.setattr(distribution_module.importlib, "import_module", modules.__getitem__)

    def installed(self, *, dist=None, packages=None, modules=None):
        dist = self.metadata(dist=dist, packages=packages)
        modules = modules or {"example_tools": module_with_status("example_tools", "ready")}
        self.imports(modules)
        return dist


@pytest.fixture
def distribution(monkeypatch):
    return DistributionHarness(monkeypatch)


def test_distribution_resolves_packages_from_packages_distributions(distribution):
    distribution.metadata(
        packages={
            "example_tools": ["example_tools"],
            "example_helpers": ["Example.Tools"],
            "_private": ["Example-Tools"],
            "not-valid": ["Example-Tools"],
            "other": ["something-else"],
        }
    )

    resolved = Giso._resolve_distribution("example-tools")

    assert resolved.name == "Example-Tools"
    assert resolved.version == "1.2.3"
    assert resolved.packages == ("example_helpers", "example_tools")


def test_distribution_falls_back_to_top_level_metadata(distribution):
    distribution.metadata(
        dist=FakeDistribution(top_level="example_tools\nexample_helpers\n_private\nnot-valid\n"),
        packages={},
    )

    resolved = Giso._resolve_distribution("Example.Tools")

    assert resolved.packages == ("example_helpers", "example_tools")


def test_distribution_falls_back_to_distribution_files(distribution):
    distribution.metadata(
        dist=FakeDistribution(
            files=(
                "example_tools/__init__.py",
                "example_tools/api.py",
                "example_helpers.py",
                "Example_Tools-1.2.3.dist-info/METADATA",
                "assets/data.py",
                "data_file.txt",
            )
        ),
        packages={},
    )

    resolved = Giso._resolve_distribution("example-tools")

    assert resolved.packages == ("example_helpers", "example_tools")


@pytest.mark.parametrize(
    "files",
    [
        ("assets/data.py", "templates/page.py"),
        ("Example_Tools-1.2.3.dist-info/METADATA",),
    ],
)
def test_distribution_file_fallback_rejects_non_packages(distribution, files):
    distribution.metadata(dist=FakeDistribution(files=files), packages={})

    with pytest.raises(ValueError, match="exposes no importable"):
        Giso._resolve_distribution("example-tools")


@pytest.mark.parametrize(
    ("dist", "message"),
    [
        (FakeDistribution(name=""), "valid name metadata"),
        (FakeDistribution(version=""), "valid version metadata"),
    ],
)
def test_distribution_rejects_malformed_metadata(distribution, dist, message):
    distribution.metadata(dist=dist)

    with pytest.raises(ValueError, match=message):
        Giso._resolve_distribution("example-tools")


def test_distribution_direct_method_uses_existing_module_fold(distribution):
    distribution.installed()

    assert Giso().distribution("example-tools").status() == "ready"


def test_dist_string_routes_to_distribution_resolver(distribution):
    distribution.installed(
        modules={"example_tools": module_with_status("example_tools", "resolved")}
    )

    assert Giso("dist:example-tools").status() == "resolved"


def test_distribution_works_inside_named_constructor_branch(distribution):
    distribution.installed()

    giso = Giso(tools="dist:example-tools")

    assert giso.tools.status() == "ready"
    assert giso.provenance == [distribution_provenance("example_tools")]


def test_distribution_records_canonical_provenance_once(distribution):
    packages = {
        "example_helpers": ["example_tools"],
        "example_tools": ["Example.Tools"],
    }
    modules = {
        "example_helpers": module_with_status("example_helpers", "helpers"),
        "example_tools": module_with_status("example_tools", "tools"),
    }
    distribution.installed(packages=packages, modules=modules)

    giso = Giso().distribution("example_tools")

    assert giso.provenance == [
        distribution_provenance("example_helpers", "example_tools")
    ]


def test_distribution_multi_root_order_matches_resolved_package_order(distribution):
    distribution.installed(
        packages={
            "example_tools": ["Example-Tools"],
            "example_helpers": ["Example-Tools"],
        },
        modules={
            "example_helpers": module_with_status("example_helpers", "helpers"),
            "example_tools": module_with_status("example_tools", "tools"),
        },
    )

    assert Giso().distribution("example-tools").status() == "tools"


def test_distribution_failure_does_not_partially_mutate_target(distribution, monkeypatch):
    distribution.metadata(
        packages={
            "example_helpers": ["Example-Tools"],
            "example_tools": ["Example-Tools"],
        }
    )
    first = module_with_status("example_helpers", "helpers")

    def import_module(name):
        if name == "example_helpers":
            return first
        raise ImportError("boom")

    monkeypatch.setattr(distribution_module.importlib, "import_module", import_module)

    giso = Giso()
    with pytest.raises(ImportError, match="boom"):
        giso.distribution("example-tools")

    assert "status" not in giso.operations
    assert giso.provenance == []


def test_distribution_provenance_survives_giso_copy(distribution):
    distribution.installed()
    original = Giso().distribution("example-tools")

    copied = Giso(original)

    assert copied.status() == "ready"
    assert copied.provenance == original.provenance


def test_missing_distribution_error_propagates(monkeypatch):
    def missing(name):
        raise distribution_module.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(distribution_module.metadata, "distribution", missing)

    with pytest.raises(distribution_module.metadata.PackageNotFoundError):
        Giso().distribution("missing-project")


def test_distribution_name_must_be_non_empty():
    with pytest.raises(TypeError, match="non-empty string"):
        Giso._resolve_distribution("")
