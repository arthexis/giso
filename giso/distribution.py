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
        """Resolve one installed distribution and fold its import roots."""
        resolved = self._resolve_distribution(name)
        for package_name in resolved.packages:
            module = importlib.import_module(package_name)
            super().fold(module)
        return self

    @classmethod
    def _resolve_distribution(cls, name: str) -> _ResolvedDistribution:
        if not isinstance(name, str) or not name.strip():
            raise TypeError("Distribution name must be a non-empty string")

        dist = metadata.distribution(name.strip())
        canonical_name = dist.metadata.get("Name") or name.strip()
        packages = cls._distribution_packages(dist, canonical_name)
        if not packages:
            raise ValueError(
                f"Installed distribution {canonical_name!r} exposes no importable top-level packages"
            )
        return _ResolvedDistribution(
            name=canonical_name,
            version=dist.version,
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
            if any(cls._normalize_distribution_name(item) == expected for item in distributions)
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

        names: set[str] = set()
        for file in dist.files or ():
            path = pathlib.PurePosixPath(str(file))
            if not path.parts:
                continue
            root = path.parts[0]
            if root.endswith((".dist-info", ".egg-info", ".data")):
                continue
            if len(path.parts) == 1 and root.endswith(".py"):
                root = root[:-3]
            if cls._is_import_root(root):
                names.add(root)
        return tuple(sorted(names))

    @staticmethod
    def _normalize_distribution_name(name: str) -> str:
        return re.sub(r"[-_.]+", "-", name).lower()

    @staticmethod
    def _is_import_root(name: str) -> bool:
        return bool(name) and name.isidentifier() and not name.startswith("_")
