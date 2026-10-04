from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .soap_schema import Giso as SoapSchemaGiso


@dataclass(frozen=True)
class _PreparedBranch:
    name: str
    ingredients: tuple[Any, ...]


@dataclass(frozen=True)
class _PreparedIngredients:
    roots: tuple[Any, ...]
    branches: tuple[_PreparedBranch, ...]


class Giso(SoapSchemaGiso):
    """A Giso with declarative named-branch construction."""

    def __init__(self, *sources: Any, name: str = "giso", **branches: Any):
        prepared = self._prepare_ingredients(sources, branches)
        super().__init__(name=name)
        if prepared.roots:
            self.fold(*prepared.roots)
        for branch in prepared.branches:
            self._construct_prepared_branch(branch)

    @classmethod
    def _prepare_ingredients(
        cls,
        sources: tuple[Any, ...],
        branches: Mapping[str, Any],
    ) -> _PreparedIngredients:
        prepared_branches: list[_PreparedBranch] = []
        for branch_name, value in branches.items():
            cls._validate_branch_name(branch_name)
            if isinstance(value, (list, tuple)):
                ingredients = tuple(value)
            else:
                ingredients = (value,)
            prepared_branches.append(
                _PreparedBranch(name=branch_name, ingredients=ingredients)
            )
        return _PreparedIngredients(
            roots=tuple(sources),
            branches=tuple(prepared_branches),
        )

    def _construct_prepared_branch(self, branch: _PreparedBranch) -> None:
        child = type(self)(*branch.ingredients, name=self.__name__)
        self.modules.update(child.modules)
        for root in child._archive_roots:
            if root not in self._archive_roots:
                self._archive_roots.append(root)
        for operation_name, operation in child.operations.items():
            original = getattr(operation, "__giso_original__", operation)
            self._attach_operation(f"{branch.name}.{operation_name}", original)
        for operation_name, value in child.results.history:
            self.results.add(f"{branch.name}.{operation_name}", value)

    @staticmethod
    def _validate_branch_name(name: str) -> None:
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
            raise ValueError("Branch names must be public Python identifiers")
