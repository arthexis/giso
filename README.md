# Giso

Giso is an experiment in building a live Python object by folding software and described services into it.

A `Giso` starts almost empty. Fold in Python functions, classes, object instances, mappings, modules, packages, files, directories, archives, repositories, package releases, or described network services and it mutates in place to expose the capabilities it discovers.

```python
from giso import Giso


def math__double(value: int) -> int:
    return value * 2


class DiagramTool:
    def validate(self, path: str) -> str:
        return f"valid:{path}"


g = Giso(math__double, DiagramTool)
assert g.math.double(4) == 8
assert g.diagram_tool.validate("drawing.svg") == "valid:drawing.svg"
```

The double underscore in a function name creates a namespace: `math__double` becomes `g.math.double`. Public methods on folded classes and configured object instances are exposed under snake-case class namespaces. Instance calls remain bound to the original object, so its state is preserved.

Mappings provide a declarative way to build capability trees:

```python
def twice(value: int) -> int:
    return value * 2


g = Giso({"tools": {"math": {"double": twice}}})
assert g.tools.math.double(4) == 8
```

Nested mappings create nested namespaces. Callable leaves use their mapping key as the exported operation name.

## Python packages and archives

Importable packages can be folded directly by package object or import name. Package/module hierarchy is preserved, while ordinary imported modules retain flat behavior.

Filesystem archives can also be folded. Giso supports `.zip`, `.whl`, `.tar.gz`, and `.tgz`; archives are extracted into a private temporary root and handed back to the normal local folding pipeline. Path traversal and archive links are rejected.

Public GitHub repositories and PyPI releases use explicit resolvers:

```python
g = Giso("github:arthexis/example@main#src/plugin")
g.pypi("requests", version="2.32.5")
```

Equivalent direct methods are available when the method itself already identifies the resolver:

```python
g = Giso()
g.github("arthexis/example", ref="main", subdirectory="src/plugin")
g.pypi("requests", version="2.32.5")
```

GitHub references resolve to an exact commit before folding. PyPI references select a verified release artifact and reuse the archive pipeline. Neither resolver installs software into the Python environment.

## OpenAPI

OpenAPI 3.x JSON descriptions can be loaded from local files or HTTPS sources:

```python
g = Giso().openapi("https://api.example.com/openapi.json")
```

The explicit string form is also supported:

```python
g = Giso("openapi:https://api.example.com/openapi.json")
```

The parsed description, selected base URL, and provenance are retained for REST operation generation. Request headers can be supplied to `openapi(...)`; they remain private and are not copied into provenance.

## SOAP / WSDL

SOAP services can be folded from a local WSDL 1.1 document or an HTTPS WSDL URL:

```python
from giso import Giso, SoapFault


g = Giso().soap(
    "https://example.com/customer?wsdl",
    headers={"Authorization": "Bearer ..."},
)

customer = g.customer_service.get_customer(
    body={"CustomerId": 7},
)
```

The explicit resolver form is equivalent:

```python
g = Giso("soap:https://example.com/customer?wsdl")
```

Giso recursively loads WSDL imports and XSD imports/includes, discovers SOAP 1.1 and SOAP 1.2 bindings, and exposes operations under deterministic snake-case paths such as `customer_service.get_customer`. When multiple ports in one service expose the same operation, later collisions are disambiguated under `service.port.operation`.

SOAP operation calls use a single `body=` mapping. The supported schema subset covers primitive/scalar values, booleans, nested `xsd:sequence` complex values, optional values, finite or unbounded repeated elements, nillable values, and simple enumeration restrictions. The request is serialized according to the WSDL binding and sent by HTTP POST.

To inspect a request without sending it, use the operation's `prepare()` helper:

```python
request = g.customer_service.get_customer.prepare(
    body={"CustomerId": 7},
)

print(request.endpoint)
print(request.headers)
print(request.body)
```

`prepare()` returns a `SoapRequest` containing the endpoint, transport headers, SOAP version/action, and serialized XML bytes.

Successful SOAP responses are decoded into ordinary Python scalar/dict/list values. SOAP 1.1 and SOAP 1.2 Faults raise `SoapFault`:

```python
try:
    g.customer_service.get_customer(body={"CustomerId": -1})
except SoapFault as exc:
    print(exc.code, exc.reason, exc.detail)
```

The SOAP implementation deliberately does **not** attempt to be a complete WSDL/XSD stack. Unsupported constructs such as `xsd:choice`, `xsd:all`, complex/simple content extension, XSD attributes, and wildcard `xsd:any` fail explicitly with `UnsupportedSoapSchema` instead of being guessed. WSDL 2.0, WS-Security, MTOM/attachments, XML signatures/encryption, and a general-purpose XSD engine are outside the current scope.

Remote WSDL/XSD acquisition requires HTTPS. XML containing `DOCTYPE` or `ENTITY` declarations is rejected before parsing. Authentication/request headers remain private and are not written into provenance.

## Live sources

Finite-looking iterables implementing both `Iterable` and `Sized` are consumed eagerly. Iterators and generators are retained as private live sources and are not consumed during `fold()`.

When a capability lookup misses, each live source gets at most one chance to provide a new ingredient. Generators supporting `send()` receive a `CapabilityRequest` containing the unresolved path after priming.

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

Exhausted sources are discarded. Live iterator state is intentionally not cloned by `g + source`; already-materialized capabilities and results are copied.

## Supported folding

`Giso.fold(...)` currently accepts:

- Python callables and classes
- configured Python object instances
- mappings/dictionaries of foldable capabilities
- ordinary imported Python modules
- imported/importable Python packages
- `.py` files and Python package directories
- ordinary directories containing Python files
- `.zip`, `.whl`, `.tar.gz`, and `.tgz` archives
- public GitHub references using `github:owner/repository[@ref][#subdirectory]`
- PyPI references using `pypi:project[@version]`
- OpenAPI 3.x JSON descriptions using `openapi:<source>`
- WSDL 1.1 SOAP descriptions using `soap:<source>`
- nested lists/tuples/sets
- finite-looking `Sized` iterables
- deferred iterators/generators
- another `Giso`

Imported callables from a folded module are ignored; only functions and classes defined by that module are attached. Private package paths are skipped.

`fold()` mutates the existing object and returns the same `Giso`, so notebook-style incremental construction works naturally. Direct resolver methods such as `github()`, `pypi()`, `openapi()`, and `soap()` follow the same fluent convention.

The project intentionally has no third-party runtime dependencies and no CLI, MCP server, deployment machinery, or application-specific integrations. The goal remains narrow: explore a live object that can fold software and described external capability surfaces into one callable namespace.
