from __future__ import annotations

import inspect
import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping

from .distribution import Giso as DistributionGiso


class AnsibleInspectionError(RuntimeError):
    """Raised when installed Ansible content cannot be inspected safely."""


@dataclass(frozen=True)
class AnsibleModuleSpec:
    """Read-only metadata describing one installed Ansible module."""

    fqcn: str
    collection: str
    name: str
    short_description: str
    description: tuple[str, ...]
    options: Mapping[str, Mapping[str, Any]]


class Giso(DistributionGiso):
    """A Giso that can inspect installed Ansible collection modules."""

    def fold(self, *sources: Any) -> "Giso":
        for source in self._flatten(sources):
            if isinstance(source, str) and source.startswith("ansible:"):
                self.ansible(source[len("ansible:") :])
            else:
                super().fold(source)
        return self

    def ansible(self, collection: str) -> "Giso":
        """Inspect modules from one installed Ansible collection without executing them."""
        collection = self._validate_collection_name(collection)
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
                self._inspection_callable(spec),
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
    def _inspection_callable(cls, spec: AnsibleModuleSpec):
        def operation(**kwargs: Any) -> None:
            raise AnsibleInspectionError(
                f"{spec.fqcn} is inspection-only; Ansible execution is not implemented"
            )

        operation.__name__ = spec.name.rsplit(".", 1)[-1]
        operation.__doc__ = spec.short_description or "\n".join(spec.description)
        operation.__signature__ = cls._module_signature(spec)  # type: ignore[attr-defined]
        operation.ansible_spec = spec  # type: ignore[attr-defined]
        return operation

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
