from __future__ import annotations

import functools
import importlib.util
import inspect
import pathlib
import re
import sys
from types import ModuleType
from typing import Any, Callable


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
    """A live object that can ingest Python modules, classes, and callables."""

    def __init__(self, *sources: Any, name: str = "giso"):
        self.__name__ = name
        self.modules: dict[str, ModuleType] = {}
        self.namespaces: dict[str, Namespace] = {}
        self.operations: dict[str, Callable[..., Any]] = {}
        if sources:
            self.ingest(*sources)

    def ingest(self, *sources: Any) -> "Giso":
        """Ingest supported Python sources and mutate this Giso in place."""
        for source in self._flatten(sources):
            if source is None:
                continue
            if isinstance(source, (str, pathlib.Path)):
                self._ingest_path(pathlib.Path(source))
            elif inspect.isclass(source) or callable(source) or isinstance(source, ModuleType):
                self._attach_component(source)
            else:
                raise TypeError(f"Unsupported source type: {type(source).__name__}")
        return self

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
            except AttributeError as exc:
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

    def _ingest_path(self, path: pathlib.Path) -> None:
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
        raise ValueError(f"Cannot ingest path: {path}")

    def _attach_module(self, path: pathlib.Path) -> None:
        module_name = f"giso_ingested_{path.stem}_{abs(hash(path))}"
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
        namespace = self.namespaces.get(namespace_name)
        if namespace is None:
            namespace = Namespace(f"{self.__name__}.{namespace_name}")
            self.namespaces[namespace_name] = namespace
            setattr(self, namespace_name, namespace)

        for name in dir(cls):
            if name.startswith("_"):
                continue
            value = getattr(cls, name)
            if not callable(value):
                continue
            operation = self._bind_class_callable(cls, value)
            setattr(namespace, name, operation)
            self.operations[f"{namespace_name}.{name}"] = operation

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
            namespace = self.namespaces.get(namespace_name)
            if namespace is None:
                namespace = Namespace(f"{self.__name__}.{namespace_name}")
                self.namespaces[namespace_name] = namespace
                setattr(self, namespace_name, namespace)
            setattr(namespace, operation_name, func)
            self.operations[f"{namespace_name}.{operation_name}"] = func
            return

        setattr(self, raw_name, func)
        self.operations[raw_name] = func

    @staticmethod
    def _snake_case(name: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    def __iter__(self):
        for name in sorted(self.operations):
            yield self.operations[name]

    def __repr__(self) -> str:
        return f"<Giso {self.__name__}: {len(self.operations)} operations>"
