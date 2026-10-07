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

## Hierarchical construction

`Giso` can also build a complete named capability tree directly in its constructor:

```python
g = Giso(
    utilities,
    charger={
        "protocol": ocpp_csms,
        "diagnostics": [charger_diagnostics, meter_tools],
    },
    system={
        "network": network_tools,
        "hardware": hardware_tools,
    },
)
```

Positional arguments are ordinary root-level ingredients. Keyword arguments create named branches. Branch values are normalized into a private prepared-ingredients tree before anything is folded:

- one value means one ingredient in that branch;
- a `list` or `tuple` means multiple ingredients folded into the same branch;
- a mapping means recursively nested branch structure;
- an explicit nested `Giso` is mounted as that branch.

List and tuple forms are intentionally equivalent:

```python
a = Giso(charger=[ocpp_csms, charger_diagnostics])
b = Giso(charger=(ocpp_csms, charger_diagnostics))
```

Mappings used as keyword branch values are structural constructor syntax. Positional mappings keep the existing mapping-fold behavior:

```python
Giso({"status": status})
Giso(charger={"status": status})
```

The first exposes `status` at the root using the mapping key as the operation name. The second creates a `charger.status` branch and folds `status` as an ingredient inside it.

Explicit nested Gisos and equivalent nested mappings produce the same capability shape:

```python
implicit = Giso(
    charger={
        "protocol": ocpp_csms,
        "diagnostics": [charger_diagnostics, meter_tools],
    }
)

explicit = Giso(
    charger=Giso(
        protocol=ocpp_csms,
        diagnostics=(charger_diagnostics, meter_tools),
    )
)
```

Named construction preserves ordinary Giso semantics for collision order, provenance, results, archive/module lifetime, and static copy behavior. Resolver provenance keeps the original source identity; branch names change semantic placement, not where an ingredient came from.

Live iterators and generators remain deferred inside their branch. A lookup such as `g.charger.diagnostics.status` is delegated to the branch-local live source using the relative path `diagnostics.status` or `status` as appropriate. Unrelated root lookups do not consume branch-local generators.

An explicitly nested live `Giso` is retained by reference at that branch boundary so its deferred sources remain live. Ordinary `Giso(existing)` and `g + source` copy semantics still do not clone iterator/generator state.

Branch names must be public Python identifiers. `fold()` itself is unchanged; hierarchical naming is constructor syntax rather than a second named-folding API.

## Python AST, compiled code, and source text

Native Python AST objects are foldable sources. `ast.Module` is compiled in `exec` mode into a synthetic module and then follows ordinary module-folding rules. `ast.Expression` is compiled in `eval` mode and the resulting Python value is folded normally.

Standalone compiled `types.CodeType` objects are also foldable. Code produced with `compile(..., mode="exec")` executes in an isolated synthetic module namespace; definitions from that namespace are folded as a module. Code produced with `compile(..., mode="eval")` returns one Python value, which is folded normally.

```python
import ast

module_tree = ast.parse("""
def double(value):
    return value * 2
""")
assert Giso(module_tree).double(4) == 8

expression = compile("{'double': lambda value: value * 2}", "<example>", "eval")
assert Giso(expression).double(5) == 10
```

Raw Python text is deliberately not interpreted by ordinary `fold()` string dispatch. Use explicit classmethod constructors when text itself is the source:

```python
g = Giso.compile("""
def math__double(value):
    return value * 2
""")
assert g.math.double(4) == 8

h = Giso.eval("{'triple': lambda value: value * 3}")
assert h.triple(4) == 12
```

`Giso.compile(source)` parses the string as statement/module source and routes the resulting `ast.Module` through the normal AST/code/module folding path. `Giso.eval(expression)` parses one expression and folds the value it produces. Both accept an optional `name=` for the resulting Giso and propagate normal `SyntaxError` failures.

The helpers do not create a separate execution engine: AST objects, compiled code objects, and text helpers all converge on the same fold semantics. A Giso created by `compile()` or `eval()` can therefore be nested beneath a named constructor branch or copied like any other statically materialized Giso.

Function-body code objects that require positional/keyword arguments or closure cells are not treated as standalone fold sources and raise `TypeError`.

## Python packages, installed distributions, and archives

Importable packages can be folded directly by package object or import name. Package/module hierarchy is preserved, while ordinary imported modules retain flat behavior.

Installed Python distributions can be resolved explicitly by distribution metadata rather than by assuming the packaging name is also an import name:

```python
g = Giso("dist:example-tools")

h = Giso()
h.distribution("example-tools")
```

Distribution lookup uses `importlib.metadata`. Giso resolves the distribution's public top-level import roots, imports each root in deterministic order, and hands every imported package/module back to the normal fold pipeline. Name comparison follows Python packaging normalization, so `Example.Tools`, `example_tools`, and `example-tools` refer to the same distribution identity when metadata maps them together.

Resolution prefers `packages_distributions()`, then `top_level.txt`. As a final conservative fallback, distribution file metadata is used only for top-level `.py` modules and directories that explicitly contain a top-level `__init__.py`; arbitrary data directories are not guessed to be Python packages.

A distribution is folded atomically. If any resolved root fails to import or fold, the target Giso is left unchanged. Successful folds add one provenance record containing the canonical distribution name, installed version, and resolved import roots. That provenance is preserved through ordinary Giso copy and named-branch construction. Missing distributions propagate `importlib.metadata.PackageNotFoundError`; malformed installed metadata and distributions with no usable import roots fail explicitly.

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

## Ansible inspection

Installed Ansible collections can be inspected without importing Ansible internals or executing modules:

```python
g = Giso().ansible("community.general")
nmcli = g.community.general.modules.nmcli
```

Giso uses the installed `ansible-doc` command to discover modules and their documentation. Each module is exposed as an inspection-only operation with an `AnsibleModuleSpec`, generated docstring, and Python signature derived from documented options.

A module operation can prepare a validated request without executing Ansible:

```python
request = nmcli.prepare(
    conn_name="wired",
    state="present",
)

assert request.fqcn == "community.general.nmcli"
assert request.args == {
    "conn_name": "wired",
    "state": "present",
}
```

`prepare()` validates documented option names, required options, aliases, simple documented types, and choices. Aliases are normalized to their canonical option names. Documented defaults are intentionally not injected into the request; default handling remains Ansible's responsibility.

A prepared request can be executed explicitly. With no execution context, Giso keeps the original localhost behavior:

```python
g = Giso().ansible("community.general")
request = g.community.general.modules.nmcli.prepare(conn_name="wired")
result = g.execute(request)
```

For an inventory-backed target, bind one host when inspecting the collection:

```python
g = Giso().ansible(
    "community.general",
    inventory="./inventory.yml",
    host="gway-004",
)

request = g.community.general.modules.nmcli.prepare(conn_name="wired")
result = g.execute(request)
```

The bound host, inventory source, and optional connection are carried by the `AnsibleModuleRequest`. If no explicit connection is supplied, Ansible resolves connection settings from inventory/configuration. Giso invokes the installed `ansible` command and reads structured host results from Ansible's `--tree` output.

The bound host may also be an Ansible group or host pattern:

```python
g = Giso().ansible(
    "community.general",
    inventory="./inventory.yml",
    host="chargers",
)

request = g.community.general.modules.nmcli.prepare(conn_name="wired")
results = g.execute(request)
```

When exactly one concrete host responds, `execute()` preserves the single-host API and returns that host's result mapping directly. When multiple hosts respond, it returns `{hostname: result}` in deterministic host-name order.

Every concrete host result is recorded under `ansible.<host>.<fqcn>`. Multi-host runs are also recorded as an aggregate under `ansible.<pattern>.<fqcn>`. If any host reports `failed` or `unreachable`, Giso records all host results and then raises `AnsibleExecutionError`; for multi-host failures, `exc.result` contains the complete host-to-result mapping, including successful hosts.

Directly calling the module operation still raises `AnsibleInspectionError`; execution remains explicit through `prepare()` followed by `execute()`. Plays, roles, and playbooks are not part of this slice.

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
- native `ast.Module` and `ast.Expression` objects
- standalone compiled `types.CodeType` objects
- ordinary imported Python modules
- imported/importable Python packages
- installed Python distributions using `dist:<distribution>`
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

Raw Python source strings are not accepted by `fold()`; use `Giso.compile(...)` or `Giso.eval(...)` explicitly.

Imported callables from a folded module are ignored; only functions and classes defined by that module are attached. Private package paths are skipped.

`fold()` mutates the existing object and returns the same `Giso`, so notebook-style incremental construction works naturally. Direct resolver methods such as `distribution()`, `github()`, `pypi()`, `openapi()`, and `soap()` follow the same fluent convention.

The project intentionally has no third-party runtime dependencies and no CLI, MCP server, deployment machinery, or application-specific integrations. The goal remains narrow: explore a live object that can fold software and described external capability surfaces into one callable namespace.
