from __future__ import annotations

import functools
import importlib.util
import inspect
import pathlib
import re
import sys
from collections.abc import Iterator, MutableMapping
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


class Namespace:
    """A mutable namespace of capabilities attached to a Giso."""

    def __init__(self, name: str):
        self.__name__ = name

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
    """A live object that can fold Python modules, classes, callables, and other Gisos into itself."""

    def __init__(self, *sources: Any, name: str = "giso"):
        self.__name__ = name
        self.modules: dict[str, ModuleType] = {}
        self.namespaces: dict[str, Namespace] = {}
        self.operations: dict[str, Callable[..., Any]] = {}
        self.results = Results()
        if sources:
            self.fold(*sources)

    def fold(self, *sources: Any) -> "Giso":
        """Fold supported sources into this Giso and mutate it in place."""
        for source in self._flatten(sources):
            if source is None:
                continue
            if isinstance(source, Giso):
                self._fold_giso(source)
            elif isinstance(source, (str, pathlib.Path)):
                self._fold_path(pathlib.Path(source))
            elif inspect.isclass(source) or callable(source) or isinstance(source, ModuleType):
                self._attach_component(source)
            else:
                raise TypeError(f"Unsupported source type: {type(source).__name__}")
        return self

    def __iadd__(self, source: Any) -> "Giso":
        """Fold a source into this Giso and return the same instance."""
        return self.fold(source)

    def __add__(self, source: Any) -> "Giso":
        """Return a new Giso with this capability surface plus one folded source."""
        derived = self._clone()
        derived.fold(source)
        return derived

    def _clone(self) -> "Giso":
        """Clone this Giso's current capability surface without sharing namespaces."""
        derived = type(self)(name=self.__name__)
        derived._fold_giso(self)
        return derived

    def _fold_giso(self, source: "Giso") -> None:
        """Fold another Giso's current capability surface into this one."""
        if source is self:
            return

        self.modules.update(source.modules)
        for operation_name, operation in source.operations.items():
            original = getattr(operation, "__giso_original__", operation)
            self._attach_operation(operation_name, original)

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
        """Walk a dotted path from this Giso using attributes or mapping items."""
        value: Any = self
        for part in path.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
                continue
            try:
                value = getattr(value, part)
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

        if "." in operation_name:
            namespace_name, name = operation_name.split(".", 1)
            namespace = self.namespaces.get(namespace_name)
            if namespace is None:
                namespace = Namespace(f"{self.__name__}.{namespace_name}")
                self.namespaces[namespace_name] = namespace
                setattr(self, namespace_name, namespace)
            setattr(namespace, name, wrapped)
        else:
            setattr(self, operation_name, wrapped)

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
