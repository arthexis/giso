from __future__ import annotations

import inspect
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Mapping

from .distribution import Giso as DistributionGiso


class AnsibleInspectionError(RuntimeError):
    """Raised when installed Ansible content cannot be inspected safely."""


class AnsibleExecutionError(RuntimeError):
    """Raised when a prepared Ansible module request cannot execute successfully."""

    def __init__(self, message: str, *, result: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class AnsibleModuleSpec:
    """Read-only metadata describing one installed Ansible module."""

    fqcn: str
    collection: str
    name: str
    short_description: str
    description: tuple[str, ...]
    options: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class AnsibleExecutionContext:
    """Bound inventory/host context for Ansible module execution."""

    host: str
    inventory: str
    connection: str | None = None


@dataclass(frozen=True)
class AnsibleModuleRequest:
    """A validated, non-executing Ansible module invocation."""

    fqcn: str
    args: Mapping[str, Any]
    context: AnsibleExecutionContext


class Giso(DistributionGiso):
    """A Giso that can inspect installed Ansible collection modules."""

    def fold(self, *sources: Any) -> "Giso":
        for source in self._flatten(sources):
            if isinstance(source, str) and source.startswith("ansible:"):
                self.ansible(source[len("ansible:") :])
            else:
                super().fold(source)
        return self

    def ansible(
        self,
        collection: str,
        *,
        inventory: str | os.PathLike[str] | None = None,
        host: str | None = None,
        connection: str | None = None,
    ) -> "Giso":
        """Inspect one collection and bind module requests to one execution target."""
        collection = self._validate_collection_name(collection)
        context = self._execution_context(
            inventory=inventory,
            host=host,
            connection=connection,
        )
        modules = self._list_ansible_modules(collection)
        if not modules:
            raise AnsibleInspectionError(
                f"Installed Ansible collection {collection!r} exposes no modules"
            )

        docs = self._load_ansible_module_docs(modules)
        child = type(self)(name=self.__name__)
        for fqcn in modules:
            spec = self._module_spec(collection, fqcn, docs.get(fqcn))
            child._attach_operation(
                f"{collection}.modules.{spec.name}",
                self._inspection_callable(spec, context),
            )

        provenance = {
            "type": "ansible-collection",
            "source": f"ansible:{collection}",
            "collection": collection,
            "modules": ",".join(modules),
        }
        if provenance not in child.provenance:
            child.provenance.append(provenance)
        self.fold(child)
        return self


    def execute(self, request: AnsibleModuleRequest) -> Mapping[str, Any]:
        """Execute one prepared Ansible module request in its bound context."""
        if not isinstance(request, AnsibleModuleRequest):
            raise TypeError("Ansible execution requires an AnsibleModuleRequest from prepare()")
        result = self._run_ansible(request)
        operation_name = f"ansible.{request.context.host}.{request.fqcn}"
        return self.results.add(operation_name, result)

    @classmethod
    def _run_ansible(cls, request: AnsibleModuleRequest) -> Mapping[str, Any]:
        executable = shutil.which("ansible")
        if executable is None:
            raise AnsibleExecutionError(
                "Ansible execution requires ansible from an installed ansible-core"
            )

        module_args = cls._serialize_module_args(request.args)
        with tempfile.TemporaryDirectory(prefix="giso-ansible-") as tree:
            command = [
                executable,
                request.context.host,
                "--inventory",
                request.context.inventory,
            ]
            if request.context.connection is not None:
                command.extend(["--connection", request.context.connection])
            command.extend([
                "--module-name",
                request.fqcn,
                "--args",
                module_args,
                "--tree",
                tree,
            ])
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=os.environ.copy(),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise AnsibleExecutionError(
                    f"Cannot execute Ansible module {request.fqcn}"
                ) from exc

            result_files = [
                os.path.join(tree, name)
                for name in sorted(os.listdir(tree))
                if os.path.isfile(os.path.join(tree, name))
            ]
            if len(result_files) != 1:
                raise AnsibleExecutionError(
                    f"Ansible target {request.context.host!r} produced "
                    f"{len(result_files)} host results; exactly one is required"
                )
            try:
                with open(result_files[0], encoding="utf-8") as handle:
                    result = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                detail = completed.stderr.strip() or completed.stdout.strip()
                message = f"Ansible returned no valid result for {request.fqcn}"
                if detail:
                    message += f": {detail}"
                raise AnsibleExecutionError(message) from exc

            if not isinstance(result, dict):
                raise AnsibleExecutionError(
                    f"Ansible returned an invalid result for {request.fqcn}"
                )
            if completed.returncode != 0 or result.get("failed") is True:
                detail = result.get("msg")
                message = f"Ansible module {request.fqcn} failed"
                if isinstance(detail, str) and detail:
                    message += f": {detail}"
                raise AnsibleExecutionError(message, result=result)
            return result

    @classmethod
    def _execution_context(
        cls,
        *,
        inventory: str | os.PathLike[str] | None,
        host: str | None,
        connection: str | None,
    ) -> AnsibleExecutionContext:
        if inventory is None:
            if host not in (None, "localhost"):
                raise ValueError("An explicit inventory is required for non-localhost targets")
            if connection not in (None, "local"):
                raise ValueError("The implicit localhost inventory only supports local connection")
            return AnsibleExecutionContext(
                host="localhost",
                inventory="localhost,",
                connection="local",
            )

        inventory_value = os.fspath(inventory).strip()
        if not inventory_value:
            raise ValueError("Ansible inventory must be a non-empty path or inventory source")
        if host is None or not isinstance(host, str) or not host.strip():
            raise ValueError("An explicit host is required when inventory is supplied")
        host_value = host.strip()
        if any(char.isspace() for char in host_value):
            raise ValueError("Ansible host must not contain whitespace")
        if connection is not None:
            if not isinstance(connection, str) or not connection.strip():
                raise ValueError("Ansible connection must be a non-empty string")
            connection = connection.strip()
        return AnsibleExecutionContext(
            host=host_value,
            inventory=inventory_value,
            connection=connection,
        )

    @staticmethod
    def _serialize_module_args(args: Mapping[str, Any]) -> str:
        parts = []
        for name, value in args.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif value is None:
                rendered = "null"
            elif isinstance(value, (list, dict)):
                rendered = json.dumps(value, separators=(",", ":"))
            else:
                rendered = str(value)
            parts.append(f"{name}={shlex.quote(rendered)}")
        return " ".join(parts)

    @classmethod
    def _list_ansible_modules(cls, collection: str) -> tuple[str, ...]:
        payload = cls._run_ansible_doc("-t", "module", "-l", "-j", collection)
        if not isinstance(payload, dict):
            raise AnsibleInspectionError("ansible-doc module listing returned invalid JSON")
        prefix = f"{collection}."
        modules = []
        for fqcn in payload:
            if not isinstance(fqcn, str) or not fqcn.startswith(prefix):
                continue
            suffix = fqcn[len(prefix) :]
            if cls._valid_plugin_path(suffix):
                modules.append(fqcn)
        return tuple(sorted(set(modules)))

    @classmethod
    def _load_ansible_module_docs(
        cls,
        modules: tuple[str, ...],
    ) -> dict[str, Any]:
        docs: dict[str, Any] = {}
        for start in range(0, len(modules), 100):
            batch = modules[start : start + 100]
            payload = cls._run_ansible_doc("-t", "module", "-j", *batch)
            if not isinstance(payload, dict):
                raise AnsibleInspectionError("ansible-doc module metadata returned invalid JSON")
            docs.update(payload)
        return docs

    @classmethod
    def _run_ansible_doc(cls, *args: str) -> Any:
        executable = shutil.which("ansible-doc")
        if executable is None:
            raise AnsibleInspectionError(
                "Ansible inspection requires ansible-doc from an installed ansible-core"
            )
        try:
            completed = subprocess.run(
                [executable, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AnsibleInspectionError("Cannot run ansible-doc") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            message = "ansible-doc failed"
            if detail:
                message += f": {detail}"
            raise AnsibleInspectionError(message)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AnsibleInspectionError("ansible-doc returned invalid JSON") from exc

    @classmethod
    def _module_spec(
        cls,
        collection: str,
        fqcn: str,
        payload: Any,
    ) -> AnsibleModuleSpec:
        if not isinstance(payload, dict):
            raise AnsibleInspectionError(f"ansible-doc returned no metadata for {fqcn}")
        document = payload.get("doc")
        if not isinstance(document, dict):
            raise AnsibleInspectionError(f"ansible-doc returned no documentation for {fqcn}")

        prefix = f"{collection}."
        name = fqcn[len(prefix) :]
        short_description = document.get("short_description")
        if not isinstance(short_description, str):
            short_description = ""
        raw_description = document.get("description")
        if isinstance(raw_description, str):
            description = (raw_description,)
        elif isinstance(raw_description, list):
            description = tuple(item for item in raw_description if isinstance(item, str))
        else:
            description = ()
        raw_options = document.get("options")
        options: dict[str, Mapping[str, Any]] = {}
        if isinstance(raw_options, dict):
            for option_name, option in raw_options.items():
                if cls._valid_public_identifier(option_name) and isinstance(option, dict):
                    options[option_name] = dict(option)

        return AnsibleModuleSpec(
            fqcn=fqcn,
            collection=collection,
            name=name,
            short_description=short_description,
            description=description,
            options=options,
        )

    @classmethod
    def _inspection_callable(
        cls,
        spec: AnsibleModuleSpec,
        context: AnsibleExecutionContext,
    ):
        def operation(**kwargs: Any) -> None:
            raise AnsibleInspectionError(
                f"{spec.fqcn} is inspection-only; Ansible execution is not implemented"
            )

        def prepare(**kwargs: Any) -> AnsibleModuleRequest:
            return cls._prepare_module_request(spec, kwargs, context)

        operation.__name__ = spec.name.rsplit(".", 1)[-1]
        operation.__doc__ = spec.short_description or "\n".join(spec.description)
        operation.__signature__ = cls._module_signature(spec)  # type: ignore[attr-defined]
        operation.ansible_spec = spec  # type: ignore[attr-defined]
        operation.prepare = prepare  # type: ignore[attr-defined]
        return operation

    @classmethod
    def _prepare_module_request(
        cls,
        spec: AnsibleModuleSpec,
        supplied: Mapping[str, Any],
        context: AnsibleExecutionContext,
    ) -> AnsibleModuleRequest:
        aliases: dict[str, str] = {}
        for option_name, option in spec.options.items():
            raw_aliases = option.get("aliases")
            if isinstance(raw_aliases, str):
                raw_aliases = [raw_aliases]
            if isinstance(raw_aliases, list):
                for alias in raw_aliases:
                    if cls._valid_public_identifier(alias):
                        aliases[alias] = option_name

        normalized: dict[str, Any] = {}
        for supplied_name, value in supplied.items():
            canonical = supplied_name if supplied_name in spec.options else aliases.get(supplied_name)
            if canonical is None:
                raise AnsibleInspectionError(
                    f"{spec.fqcn} has no documented option {supplied_name!r}"
                )
            if canonical in normalized:
                raise AnsibleInspectionError(
                    f"{spec.fqcn} option {canonical!r} was supplied more than once"
                )
            cls._validate_option_value(spec, canonical, value)
            normalized[canonical] = value

        missing = [
            name
            for name, option in spec.options.items()
            if option.get("required") is True and name not in normalized
        ]
        if missing:
            joined = ", ".join(sorted(missing))
            raise AnsibleInspectionError(
                f"{spec.fqcn} is missing required option(s): {joined}"
            )

        return AnsibleModuleRequest(
            fqcn=spec.fqcn,
            args=normalized,
            context=context,
        )

    @classmethod
    def _validate_option_value(
        cls,
        spec: AnsibleModuleSpec,
        name: str,
        value: Any,
    ) -> None:
        option = spec.options[name]
        expected = cls._option_annotation(option.get("type"))
        if expected is not Any:
            valid = isinstance(value, expected)
            if expected is int and isinstance(value, bool):
                valid = False
            if not valid:
                raise AnsibleInspectionError(
                    f"{spec.fqcn} option {name!r} expects {expected.__name__}"
                )

        choices = option.get("choices")
        if isinstance(choices, list) and value not in choices:
            raise AnsibleInspectionError(
                f"{spec.fqcn} option {name!r} must be one of {choices!r}"
            )

    @classmethod
    def _module_signature(cls, spec: AnsibleModuleSpec) -> inspect.Signature:
        parameters = []
        for name, option in spec.options.items():
            required = option.get("required") is True
            if required:
                default = inspect.Parameter.empty
            elif "default" in option:
                default = option["default"]
            else:
                default = None
            parameters.append(
                inspect.Parameter(
                    name,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=cls._option_annotation(option.get("type")),
                )
            )
        return inspect.Signature(parameters)

    @staticmethod
    def _option_annotation(option_type: Any) -> Any:
        return {
            "str": str,
            "path": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
        }.get(option_type, Any)

    @staticmethod
    def _validate_collection_name(collection: str) -> str:
        if not isinstance(collection, str) or not collection.strip():
            raise TypeError("Ansible collection must be a non-empty namespace.collection string")
        collection = collection.strip()
        parts = collection.split(".")
        if len(parts) != 2 or not all(Giso._valid_public_identifier(part) for part in parts):
            raise ValueError("Ansible collection must use namespace.collection")
        return collection

    @staticmethod
    def _valid_plugin_path(path: str) -> bool:
        return bool(path) and all(Giso._valid_public_identifier(part) for part in path.split("."))

    @staticmethod
    def _valid_public_identifier(value: Any) -> bool:
        return isinstance(value, str) and value.isidentifier() and not value.startswith("_")
