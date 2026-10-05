from __future__ import annotations

import importlib
import importlib.metadata as metadata
import pathlib
import re
from dataclasses import dataclass
from typing import Any

from .python_ast import Giso as PythonAstGiso


@dataclass(frozen=True)
class _ResolvedDistribution:
    name: str
    version: str
    packages: tuple[str, ...]


class Giso(PythonAstGiso):
    """A Giso that can resolve installed Python distributions."""

    def fold(self, *sources: Any) -> "Giso":
        for source in self._flatten(sources):
            if isinstance(source, str) and source.startswith("dist:"):
                self.distribution(source[5:])
            else:
                super().fold(source)
        return self

    def distribution(self, name: str) -> "Giso":
        """Resolve one installed distribution and fold its import roots atomically."""
        resolved = self._resolve_distribution(name)
        child = type(self)(name=self.__name__)
        for package_name in resolved.packages:
            module = importlib.import_module(package_name)
            child.fold(module)

        provenance = {
            "type": "distribution",
            "source": f"dist:{resolved.name}",
            "name": resolved.name,
            "version": resolved.version,
            "packages": ",".join(resolved.packages),
        }
        if provenance not in child.provenance:
            child.provenance.append(provenance)
        self.fold(child)
        return self

    @classmethod
    def _resolve_distribution(cls, name: str) -> _ResolvedDistribution:
        if not isinstance(name, str) or not name.strip():
            raise TypeError("Distribution name must be a non-empty string")

        requested_name = name.strip()
        dist = metadata.distribution(requested_name)
        metadata_name = dist.metadata.get("Name")
        canonical_name = requested_name if metadata_name is None else metadata_name
        if not isinstance(canonical_name, str) or not canonical_name.strip():
            raise ValueError(f"Installed distribution {requested_name!r} has no valid name metadata")
        canonical_name = canonical_name.strip()

        version = dist.version
        if not isinstance(version, str) or not version.strip():
            raise ValueError(f"Installed distribution {canonical_name!r} has no valid version metadata")
        version = version.strip()

        packages = cls._distribution_packages(dist, canonical_name)
        if not packages:
            raise ValueError(
                f"Installed distribution {canonical_name!r} exposes no importable top-level packages"
            )
        return _ResolvedDistribution(
            name=canonical_name,
            version=version,
            packages=packages,
        )

    @classmethod
    def _distribution_packages(
        cls,
        dist: metadata.Distribution,
        canonical_name: str,
    ) -> tuple[str, ...]:
        expected = cls._normalize_distribution_name(canonical_name)
        discovered = {
            package
            for package, distributions in metadata.packages_distributions().items()
            if cls._is_import_root(package)
            and any(cls._normalize_distribution_name(item) == expected for item in distributions)
        }
        if discovered:
            return tuple(sorted(discovered))

        top_level = dist.read_text("top_level.txt")
        if top_level:
            names = {
                line.strip()
                for line in top_level.splitlines()
                if cls._is_import_root(line.strip())
            }
            if names:
                return tuple(sorted(names))

        files = tuple(dist.files or ())
        names: set[str] = set()
        for file in files:
            path = pathlib.PurePosixPath(str(file))
            if not path.parts:
                continue
            root = path.parts[0]
            if root.endswith((".dist-info", ".egg-info", ".data")):
                continue
            if len(path.parts) == 1 and root.endswith(".py"):
                module_name = root[:-3]
                if cls._is_import_root(module_name):
                    names.add(module_name)
                continue
            if (
                len(path.parts) == 2
                and path.parts[1] == "__init__.py"
                and cls._is_import_root(root)
            ):
                names.add(root)
        return tuple(sorted(names))

    @staticmethod
    def _normalize_distribution_name(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    @staticmethod
    def _is_import_root(name: str) -> bool:
        return bool(name) and name.isidentifier() and not name.startswith("_")
