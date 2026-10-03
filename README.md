# Giso

Giso is an experiment in building a live Python object by folding software into it.

A `Giso` starts almost empty. Fold in Python functions, classes, modules, files, or directories and it mutates in place to expose the capabilities it discovers.

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

The current experiment intentionally has no third-party runtime dependencies and no CLI, MCP, web server, recipes, security model, remote execution, deployment machinery, or application-specific integrations. The only goal is to preserve and explore the original GSoL idea: an object that can fold software into itself and gain capabilities while it is running.

## Supported folding

`Giso.fold(...)` currently accepts:

- Python callables
- Python classes
- imported Python modules
- `.py` files
- directories containing Python files
- nested lists/tuples/sets of the above

Imported callables from a folded module are ignored; only functions and classes defined by that module are attached.

`fold()` mutates the existing object and returns the same `Giso`, so notebook-style incremental construction works naturally.
