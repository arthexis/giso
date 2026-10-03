from __future__ import annotations

import functools
import importlib
import importlib.util
import inspect
import pathlib
import re
import shlex
import sys
from collections.abc import Iterable, Iterator, Mapping, MutableMapping, Sized
from types import ModuleType
from typing import Any, Callable


class Results(MutableMapping[str, Any]):
    """Accumulated results produced by operations executed through a Giso."""

    def __init__(self):
        self._latest: dict[str, Any] = {}
        self._history: list[tuple[str, Any]] = []

    def add(self, operation: str, value: Any) -> Any:
        """Record a result and return the value unchanged."""
        self._latest[operation] = value
        self._history.append((operation, value))
        return value

    def fold(self, other: "Results") -> "Results":
        """Fold another result history into this one in call order."""
        for operation, value in other.history:
            self.add(operation, value)
        return self

    @property
    def history(self) -> tuple[tuple[str, Any], ...]:
        """Return all recorded operation results in call order."""
        return tuple(self._history)

    @property
    def last(self) -> Any:
        """Return the most recently recorded value, or None when empty."""
        if not self._history:
            return None
        return self._history[-1][1]

    def __getitem__(self, key: str) -> Any:
        return self._latest[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.add(key, value)

    def __delitem__(self, key: str) -> None:
        del self._latest[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._latest)

    def __len__(self) -> int:
        return len(self._latest)

    def __repr__(self) -> str:
        return repr(self._latest)


class CapabilityRequest:
    """Describe a capability path requested from a deferred source."""

    def __init__(self, path: str):
        self.path = path

    def __repr__(self) -> str:
        return f"<CapabilityRequest {self.path!r}>"


class _DeferredSource:
    """Advance one iterator at most once for each unresolved lookup."""

    def __init__(self, iterator: Iterator[Any]):
        self.iterator = iterator
        self.primed = False
        self.exhausted = False

    def request(self, path: str) -> Any:
        if self.exhausted:
            return None

        sender = getattr(self.iterator, "send", None)
        if sender is None:
            return self._next()

        if not self.primed:
            initial = self._next()
            self.primed = True
            if self.exhausted or initial is not None:
                return initial

        try:
            return sender(CapabilityRequest(path))
        except StopIteration:
            self.exhausted = True
            return None

    def _next(self) -> Any:
        try:
            return next(self.iterator)
        except StopIteration:
            self.exhausted = True
            return None


class Namespace:
    """A mutable namespace of capabilities attached to a Giso."""

    def __init__(self, name: str, root: "Giso | None" = None, path: str | None = None):
        self.__name__ = name
        self._root = root
        self._path = path

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or self._root is None or self._path is None:
            raise AttributeError(name)
        path = f"{self._path}.{name}"
        try:
            return self._root._lookup(path)
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __iter__(self):
        for name in sorted(vars(self)):
            if name.startswith("_"):
                continue
            value = getattr(self, name)
            if callable(value):
                yield value

    def __repr__(self) -> str:
        names = sorted(name for name in vars(self) if not name.startswith("_"))
        return f"<Namespace {self.__name__}: {', '.join(names)}>"


class Sigil:
    """A lazy reference to a value or capability rooted in a Giso."""

    def __init__(self, root: "Giso", path: str):
        if not isinstance(path, str) or not path:
            raise TypeError("Sigil path must be a non-empty string")
        self.root = root
        self.path = path

    def resolve(self) -> Any:
        """Resolve this sigil against the current state of its root Giso."""
        return self.root[self.path]

    @property
    def value(self) -> Any:
        """Resolve this sigil as a property."""
        return self.resolve()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Resolve the sigil and call the result when it is callable."""
        value = self.resolve()
        if args or kwargs:
            if not callable(value):
                raise TypeError(f"Sigil {self.path!r} does not resolve to a callable")
            return value(*args, **kwargs)
        return value

    def __repr__(self) -> str:
        return f"<Sigil {self.root.__name__}[{self.path!r}]>"


class Giso:
    """A live object that can fold Python software and other Gisos into itself."""

    def __init__(self, *sources: Any, name: str = "giso"):
        self.__name__ = name
        self.modules: dict[str, ModuleType] = {}
        self.namespaces: dict[str, Namespace] = {}
        self.operations: dict[str, Callable[..., Any]] = {}
        self.results = Results()
        self._deferred_sources: list[_DeferredSource] = []
        if sources:
            self.fold(*sources)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or not self.__dict__.get("_deferred_sources"):
            raise AttributeError(name)
        try:
            return self._lookup(name)
        except KeyError as exc:
            raise AttributeError(name) from exc

    def fold(self, *sources: Any) -> "Giso":
        """Fold supported sources into this Giso and mutate it in place."""
        for source in self._flatten(sources):
            if source is None:
                continue
            if isinstance(source, Giso):
                self._fold_giso(source)
            elif isinstance(source, Mapping):
                self._fold_mapping(source)
            elif isinstance(source, pathlib.Path):
                self._fold_path(source)
            elif isinstance(source, str):
                self._fold_string(source)
            elif inspect.isclass(source) or callable(source) or isinstance(source, ModuleType):
                self._attach_component(source)
            elif isinstance(source, Iterator):
                self._deferred_sources.append(_DeferredSource(source))
            elif isinstance(source, Sized) and isinstance(source, Iterable):
                self._fold_finite_iterable(source)
            elif self._is_foldable_instance(source):
                self._attach_instance(source)
            else:
                raise TypeError(f"Unsupported source type: {type(source).__name__}")
        return self

    def __iadd__(self, source: Any) -> "Giso":
        """Fold a source into this Giso and return the same instance."""
        return self.fold(source)

    def __add__(self, source: Any) -> "Giso":
        """Return a new Giso with this capability and result state plus one folded source."""
        derived = self._clone()
        derived.fold(source)
        return derived

    def __lshift__(self, command: str) -> Any:
        """Execute one of this Giso's operations and fold its transient result back into itself."""
        operation_name, args, kwargs = self._parse_command(command)
        operation = self.operations[operation_name]
        original = getattr(operation, "__giso_original__", operation)
        value = original(*args, **kwargs)
        self.fold(value)
        return value

    def _parse_command(self, command: str) -> tuple[str, list[str], dict[str, Any]]:
        """Parse a small Gway-like command against the operations already in this Giso."""
        if not isinstance(command, str) or not command.strip():
            raise TypeError("Self-fold expects a non-empty command string")

        tokens = shlex.split(command)
        operation_name = None
        argument_start = 0

        for end in range(len(tokens), 0, -1):
            candidate = ".".join(tokens[:end])
            if candidate in self.operations:
                operation_name = candidate
                argument_start = end
                break

        if operation_name is None:
            raise KeyError(f"No Giso operation matches command: {command}")

        args: list[str] = []
        kwargs: dict[str, Any] = {}
        remaining = tokens[argument_start:]
        index = 0
        while index < len(remaining):
            token = remaining[index]
            if not token.startswith("--"):
                args.append(token)
                index += 1
                continue

            if token.startswith("--no-"):
                kwargs[token[5:].replace("-", "_")] = False
                index += 1
                continue

            option = token[2:]
            if "=" in option:
                name, value = option.split("=", 1)
                kwargs[name.replace("-", "_")] = value
                index += 1
                continue

            name = option.replace("-", "_")
            if index + 1 < len(remaining) and not remaining[index + 1].startswith("--"):
                kwargs[name] = remaining[index + 1]
                index += 2
            else:
                kwargs[name] = True
                index += 1

        return operation_name, args, kwargs

    def _clone(self) -> "Giso":
        """Clone current capabilities/results; deferred live sources are not cloned."""
        derived = type(self)(name=self.__name__)
        derived._fold_giso(self)
        return derived

    def _fold_giso(self, source: "Giso") -> None:
        """Fold another Giso's current capabilities and accumulated results into this one."""
        if source is self:
            return

        self.modules.update(source.modules)
        for operation_name, operation in source.operations.items():
            original = getattr(operation, "__giso_original__", operation)
            self._attach_operation(operation_name, original)
        self.results.fold(source.results)

    def _fold_mapping(self, source: Mapping[Any, Any], prefix: str = "") -> None:
        """Fold a mapping as a declarative capability tree."""
        for key, value in source.items():
            if not isinstance(key, str) or not key.isidentifier() or key.startswith("_"):
                raise ValueError("Mapping keys must be public Python identifiers")

            operation_name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, Mapping):
                self._fold_mapping(value, operation_name)
                continue
            if value is None:
                continue
            if callable(value) and not inspect.isclass(value) and not isinstance(value, ModuleType):
                self._attach_operation(operation_name, value)
                continue

            folded = type(self)(value, name=self.__name__)
            self.modules.update(folded.modules)
            for child_name, operation in folded.operations.items():
                original = getattr(operation, "__giso_original__", operation)
                self._attach_operation(f"{operation_name}.{child_name}", original)
            for child_name, result in folded.results.history:
                self.results.add(f"{operation_name}.{child_name}", result)

    def _fold_finite_iterable(self, source: Iterable[Any]) -> None:
        """Eagerly fold every item from an iterable that advertises a finite size."""
        for value in source:
            self.fold(value)

    def __getitem__(self, key: Any) -> Any:
        """Resolve a value now, or return a lazy Sigil for the double-bracket form."""
        if isinstance(key, list):
            if len(key) != 1:
                raise ValueError("Lazy sigil syntax expects exactly one path")
            return Sigil(self, key[0])
        if not isinstance(key, str) or not key:
            raise TypeError("Giso lookup expects a non-empty string path")
        return self._lookup(key)

    def _lookup(self, path: str) -> Any:
        """Resolve a path, asking each deferred source at most once on a miss."""
        try:
            return self._lookup_current(path)
        except KeyError:
            pass

        for source in tuple(self._deferred_sources):
            ingredient = source.request(path)
            if source.exhausted:
                self._deferred_sources.remove(source)
            if ingredient is not None:
                self.fold(ingredient)
            try:
                return self._lookup_current(path)
            except KeyError:
                continue
        raise KeyError(path)

    def _lookup_current(self, path: str) -> Any:
        """Walk a dotted path without consulting deferred sources."""
        value: Any = self
        for part in path.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
                continue
            try:
                if value is self:
                    value = object.__getattribute__(self, part)
                else:
                    value = object.__getattribute__(value, part)
            except AttributeError:
                try:
                    value = value[part]
                except (KeyError, IndexError, TypeError, AttributeError) as item_exc:
                    raise KeyError(path) from item_exc
        return value

    def _flatten(self, values):
        for value in values:
            if isinstance(value, (list, tuple, set)):
                yield from self._flatten(value)
            else:
                yield value

    def _fold_string(self, source: str) -> None:
        """Fold a string as an existing path first, otherwise as an importable module name."""
        if not source.strip():
            raise ValueError("Cannot fold an empty module name")

        path = pathlib.Path(source).expanduser()
        if path.exists():
            self._fold_path(path)
            return

        module_name = source.replace("\\", "/").strip("/").replace("/", ".")
        if module_name.endswith(".py"):
            module_name = module_name[:-3]
        if not module_name:
            raise ValueError("Cannot fold an empty module name")

        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            raise ValueError(f"Cannot fold path or import module: {source}") from exc

        self.modules[module.__name__] = module
        self._attach_component(module)

    def _fold_path(self, path: pathlib.Path) -> None:
        path = path.expanduser().resolve()
        if path.is_dir():
            for item in sorted(path.rglob("*.py")):
                if item.name.startswith("_"):
                    continue
                self._attach_module(item)
            return
        if path.is_file() and path.suffix == ".py":
            self._attach_module(path)
            return
        raise ValueError(f"Cannot fold path: {path}")

    def _attach_module(self, path: pathlib.Path) -> None:
        module_name = f"giso_folded_{path.stem}_{abs(hash(path))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        self.modules[str(path)] = module
        self._attach_component(module)

    def _attach_component(self, component: Any) -> None:
        if inspect.isclass(component):
            self._attach_class(component)
            return
        if callable(component) and not isinstance(component, ModuleType):
            self._attach_callable(component)
            return
        if isinstance(component, ModuleType):
            for name in dir(component):
                if name.startswith("_"):
                    continue
                value = getattr(component, name)
                if inspect.isclass(value) and value.__module__ == component.__name__:
                    self._attach_class(value)
                elif callable(value) and getattr(value, "__module__", None) == component.__name__:
                    self._attach_callable(value)
            return
        raise TypeError(f"Unsupported component type: {type(component).__name__}")

    @staticmethod
    def _is_foldable_instance(instance: Any) -> bool:
        """Return whether an object instance is an intentional non-built-in fold source."""
        return type(instance).__module__ != "builtins"

    def _attach_instance(self, instance: Any) -> None:
        """Attach public class-defined methods bound to one configured instance."""
        namespace_name = self._snake_case(type(instance).__name__)
        for name in dir(type(instance)):
            if name.startswith("_"):
                continue
            descriptor = inspect.getattr_static(type(instance), name)
            if not (
                inspect.isfunction(descriptor)
                or isinstance(descriptor, (staticmethod, classmethod))
            ):
                continue
            method = getattr(instance, name)
            if callable(method):
                self._attach_operation(f"{namespace_name}.{name}", method)

    def _attach_class(self, cls: type) -> None:
        namespace_name = self._snake_case(cls.__name__)
        for name in dir(cls):
            if name.startswith("_"):
                continue
            value = getattr(cls, name)
            if not callable(value):
                continue
            operation = self._bind_class_callable(cls, value)
            self._attach_operation(f"{namespace_name}.{name}", operation)

    def _bind_class_callable(self, cls: type, func: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(func)
        params = list(signature.parameters.values())
        needs_instance = bool(params and params[0].name in {"self", "cls"})
        if not needs_instance:
            return func

        @functools.wraps(func)
        def operation(*args, **kwargs):
            instance = cls()
            return getattr(instance, func.__name__)(*args, **kwargs)

        return operation

    def _attach_callable(self, func: Callable[..., Any]) -> None:
        raw_name = func.__name__
        if "__" in raw_name:
            namespace_name, operation_name = raw_name.split("__", 1)
            self._attach_operation(f"{namespace_name}.{operation_name}", func)
            return
        self._attach_operation(raw_name, func)

    def _attach_operation(self, operation_name: str, func: Callable[..., Any]) -> None:
        """Attach an operation and route calls through the result accumulator."""
        wrapped = self._wrap_operation(operation_name, func)
        parts = operation_name.split(".")

        if len(parts) == 1:
            setattr(self, operation_name, wrapped)
        else:
            container: Any = self
            path_parts: list[str] = []
            for part in parts[:-1]:
                path_parts.append(part)
                if container is self:
                    namespace = self.namespaces.get(part)
                    if namespace is None:
                        namespace = Namespace(
                            f"{self.__name__}.{part}", root=self, path=part
                        )
                        self.namespaces[part] = namespace
                        setattr(self, part, namespace)
                else:
                    namespace = getattr(container, part, None)
                    if not isinstance(namespace, Namespace):
                        path = ".".join(path_parts)
                        namespace = Namespace(
                            f"{self.__name__}.{path}", root=self, path=path
                        )
                        setattr(container, part, namespace)
                container = namespace
            setattr(container, parts[-1], wrapped)

        self.operations[operation_name] = wrapped

    def _wrap_operation(self, operation_name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def operation(*args: Any, **kwargs: Any) -> Any:
            value = func(*args, **kwargs)
            return self.results.add(operation_name, value)

        operation.__giso_original__ = func
        return operation

    @staticmethod
    def _snake_case(name: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    def __iter__(self):
        for name in sorted(self.operations):
            yield self.operations[name]

    def __repr__(self) -> str:
        return f"<Giso {self.__name__}: {len(self.operations)} operations>"
