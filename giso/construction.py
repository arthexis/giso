from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .soap_schema import Giso as SoapSchemaGiso


@dataclass(frozen=True)
class _PreparedBranch:
    name: str
    prepared: "_PreparedIngredients"


@dataclass(frozen=True)
class _PreparedIngredients:
    roots: tuple[Any, ...]
    branches: tuple[_PreparedBranch, ...]


class Giso(SoapSchemaGiso):
    """A Giso with declarative named-branch construction."""

    def __init__(self, *sources: Any, name: str = "giso", **branches: Any):
        prepared = self._prepare_ingredients(sources, branches)
        super().__init__(name=name)
        self._construct_prepared(prepared)

    @classmethod
    def _prepare_ingredients(
        cls,
        sources: tuple[Any, ...],
        branches: Mapping[str, Any],
    ) -> _PreparedIngredients:
        prepared_branches = tuple(
            cls._prepare_branch(branch_name, value)
            for branch_name, value in branches.items()
        )
        return _PreparedIngredients(
            roots=tuple(sources),
            branches=prepared_branches,
        )

    @classmethod
    def _prepare_branch(cls, name: str, value: Any) -> _PreparedBranch:
        cls._validate_branch_name(name)
        if isinstance(value, Mapping):
            prepared = cls._prepare_ingredients((), value)
        elif isinstance(value, (list, tuple)):
            prepared = cls._prepare_ingredients(tuple(value), {})
        else:
            prepared = cls._prepare_ingredients((value,), {})
        return _PreparedBranch(name=name, prepared=prepared)

    def _construct_prepared(self, prepared: _PreparedIngredients) -> None:
        if prepared.roots:
            self.fold(*prepared.roots)
        for branch in prepared.branches:
            self._construct_prepared_branch(branch)

    def _construct_prepared_branch(self, branch: _PreparedBranch) -> None:
        child = type(self)(name=self.__name__)
        child._construct_prepared(branch.prepared)
        self._mount_prepared_child(branch.name, child)

    def _mount_prepared_child(self, branch_name: str, child: "Giso") -> None:
        self.modules.update(child.modules)
        for root in child._archive_roots:
            if root not in self._archive_roots:
                self._archive_roots.append(root)
        for operation_name, operation in child.operations.items():
            original = getattr(operation, "__giso_original__", operation)
            self._attach_operation(f"{branch_name}.{operation_name}", original)
        for operation_name, value in child.results.history:
            self.results.add(f"{branch_name}.{operation_name}", value)

    @staticmethod
    def _validate_branch_name(name: str) -> None:
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
            raise ValueError("Branch names must be public Python identifiers")
