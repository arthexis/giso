# Giso

Giso is an experiment in building a live Python object by folding software into it.

A `Giso` starts almost empty. Fold in Python functions, classes, object instances, mappings, modules, files, or directories and it mutates in place to expose the capabilities it discovers.

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

Configured object instances can also be folded. Their public class-defined methods are attached under the same class-derived namespace, but calls remain bound to the original instance so its state is preserved:

```python
class Counter:
    def __init__(self, value: int = 0):
        self.value = value

    def increment(self) -> int:
        self.value += 1
        return self.value


counter = Counter(4)
g = Giso(counter)

assert g.counter.increment() == 5
assert g.counter.increment() == 6
assert counter.value == 6
```

Private methods, plain attributes, and properties on folded instances are not exposed as operations.

Mappings provide a declarative way to define capability trees. Mapping keys define the exported path, so the callable's Python name does not have to match the Giso operation name:

```python
def twice(value: int) -> int:
    return value * 2


g = Giso({
    "tools": {
        "math": {
            "double": twice,
        },
    },
})

assert g.tools.math.double(4) == 8
```

Nested mappings create nested namespaces. Callable leaves use their mapping key as the operation name. Other supported fold sources keep their discovered capability surface under the mapping key as a prefix. Mapping keys must be public Python identifiers.

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

Imported callables from a folded module are ignored; only functions and classes defined by that module are attached.

Object instances expose public methods defined by their class while preserving the original bound instance and its state. Built-in values remain unsupported fold sources.

Mappings recursively build capability namespaces. Callable leaves are renamed by their key; richer fold sources are prefixed by the mapping path while preserving their own discovered operations and result history.

`fold()` mutates the existing object and returns the same `Giso`, so notebook-style incremental construction works naturally.
