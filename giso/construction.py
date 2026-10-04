from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .core import Namespace
from .soap_schema import Giso as SoapSchemaGiso


@dataclass(frozen=True)
class _PreparedBranch:
    name: str
    prepared: "_PreparedIngredients"


@dataclass(frozen=True)
class _PreparedIngredients:
    roots: tuple[Any, ...]
    branches: tuple[_PreparedBranch, ...]


class _MountedChildSource:
    """Delegate unresolved prefixed lookups to a mounted child Giso."""

    def __init__(self, parent: "Giso", prefix: str, child: "Giso"):
        self.parent = parent
        self.prefix = prefix
        self.child = child
        self.exhausted = False
        self._result_offset = len(child.results.history)

    def request(self, path: str) -> None:
        prefix = f"{self.prefix}."
        if not path.startswith(prefix):
            return None

        relative = path[len(prefix) :]
        try:
            value = self.child._lookup(relative)
        except KeyError:
            value = None

        self._result_offset = self.parent._sync_prepared_child(
            self.prefix,
            self.child,
            result_offset=self._result_offset,
        )
        if isinstance(value, Namespace):
            self.parent._ensure_namespace_path(path)

        self.exhausted = not self.child._deferred_sources
        return None


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
        prepared = branch.prepared
        if (
            not prepared.branches
            and len(prepared.roots) == 1
            and isinstance(prepared.roots[0], SoapSchemaGiso)
        ):
            child = prepared.roots[0]
        else:
            child = type(self)(name=self.__name__)
            child._construct_prepared(prepared)
        self._mount_prepared_child(branch.name, child)

    def _mount_prepared_child(self, branch_name: str, child: "Giso") -> None:
        self._ensure_namespace_path(branch_name)
        self._sync_prepared_child(branch_name, child)
        if child._deferred_sources:
            self._deferred_sources.append(_MountedChildSource(self, branch_name, child))

    def _sync_prepared_child(
        self,
        branch_name: str,
        child: "Giso",
        *,
        result_offset: int = 0,
    ) -> int:
        self.modules.update(child.modules)
        for root in child._archive_roots:
            if root not in self._archive_roots:
                self._archive_roots.append(root)
        for operation_name, operation in child.operations.items():
            mounted_name = f"{branch_name}.{operation_name}"
            if mounted_name in self.operations:
                continue
            original = getattr(operation, "__giso_original__", operation)
            self._attach_operation(mounted_name, original)
        history = child.results.history
        for operation_name, value in history[result_offset:]:
            self.results.add(f"{branch_name}.{operation_name}", value)
        return len(history)

    def _ensure_namespace_path(self, path: str) -> Namespace:
        container: Any = self
        path_parts: list[str] = []
        namespace: Namespace | None = None
        for part in path.split("."):
            path_parts.append(part)
            if container is self:
                namespace = self.namespaces.get(part)
                if namespace is None:
                    namespace = Namespace(f"{self.__name__}.{part}", root=self, path=part)
                    self.namespaces[part] = namespace
                    setattr(self, part, namespace)
            else:
                existing = vars(container).get(part)
                if isinstance(existing, Namespace):
                    namespace = existing
                else:
                    namespace_path = ".".join(path_parts)
                    namespace = Namespace(
                        f"{self.__name__}.{namespace_path}",
                        root=self,
                        path=namespace_path,
                    )
                    setattr(container, part, namespace)
            container = namespace
        assert namespace is not None
        return namespace

    @staticmethod
    def _validate_branch_name(name: str) -> None:
        if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
            raise ValueError("Branch names must be public Python identifiers")
