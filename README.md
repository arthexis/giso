# Giso

Giso is an experiment in building a live Python object by folding software into it.

A `Giso` starts almost empty. Fold in Python functions, classes, object instances, mappings, modules, files, directories, finite iterables, or live iterator sources and it mutates in place to expose the capabilities it discovers.

```python
from giso import Giso


def math__double(value: int) -> int:
    return value * 2


class DiagramTool:
    def validate(self, path: str) -> str:
        return f"valid:{path}"


g = Giso()
g.fold(math__double, DiagramTool)

assert g.math.double(4) == 8
assert g.diagram_tool.validate("drawing.svg") == "valid:drawing.svg"
```

The double underscore in a function name creates a namespace: `math__double` becomes `g.math.double`. Public methods on a folded class become operations under a snake-case namespace derived from the class name.

Configured object instances can also be folded. Their public class-defined methods are attached under the same class-derived namespace, but calls remain bound to the original instance so its state is preserved.

Mappings provide a declarative way to define capability trees. Mapping keys define the exported path, nested mappings create nested namespaces, and callable leaves use their mapping key as the operation name.

Finite-looking iterables that implement both `Iterable` and `Sized` are consumed eagerly and each item is folded through the normal source rules. Iterators and generators are different: they are retained as private live sources and are not consumed during `fold()`.

When a capability lookup misses, each live source gets at most one chance to provide a new ingredient for that lookup. Plain iterators advance once. Generators that support `send()` receive a `CapabilityRequest` containing the unresolved path after they are primed, so they can choose an ingredient specifically for the requested capability.

```python
from giso import CapabilityRequest, Giso


def status() -> str:
    return "ready"


def provider():
    request = yield
    while True:
        if request.path == "status":
            request = yield status
        else:
            request = yield None


g = Giso(provider())
assert g.status() == "ready"
```

A failed lookup never loops over one source repeatedly: each deferred source advances at most once for that lookup. A later lookup may advance it once again. Exhausted sources are discarded. Live iterator state is intentionally not cloned by `g + source`; only already-materialized capabilities and results are copied.

The current experiment intentionally has no third-party runtime dependencies and no CLI, MCP, web server, recipes, security model, remote execution, deployment machinery, or application-specific integrations. The only goal is to preserve and explore the original GSoL idea: an object that can fold software into itself and gain capabilities while it is running.

## Supported folding

`Giso.fold(...)` currently accepts:

- Python callables
- Python classes
- configured Python object instances
- mappings/dictionaries of foldable capabilities
- imported Python modules
- `.py` files
- directories containing Python files
- nested lists/tuples/sets of the above
- finite-looking `Sized` iterables, consumed eagerly
- iterators/generators, retained as deferred live capability sources

Imported callables from a folded module are ignored; only functions and classes defined by that module are attached.

Object instances expose public methods defined by their class while preserving the original bound instance and its state. Built-in values remain unsupported fold sources.

Mappings recursively build capability namespaces. Callable leaves are renamed by their key; richer fold sources are prefixed by the mapping path while preserving their own discovered operations and result history.

`fold()` mutates the existing object and returns the same `Giso`, so notebook-style incremental construction works naturally.
