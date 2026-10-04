from __future__ import annotations

from typing import Any

from .soap import Giso as SoapGiso


class Giso(SoapGiso):
    """A Giso that materializes discovered WSDL operations as placeholders."""

    def _fold_soap(self, *args: Any, **kwargs: Any) -> None:
        before = len(self._soap_sources)
        endpoint_overridden = kwargs.get("endpoint") is not None
        super()._fold_soap(*args, **kwargs)
        for source in self._soap_sources[before:]:
            source["endpoint_overridden"] = endpoint_overridden
            self._attach_soap_operations(source)

    def _attach_soap_operations(self, source: dict[str, Any]) -> None:
        model = source["model"]
        seen: dict[str, dict[str, Any]] = {}

        for discovered in model.get("operations", ()):
            service = self._soap_identifier(discovered.get("service", "service"))
            operation = self._soap_identifier(discovered.get("name", "operation"))
            short_path = f"{service}.{operation}"
            metadata = self._soap_operation_metadata(source, discovered)

            if short_path not in seen and short_path not in self.operations:
                path = short_path
                seen[short_path] = metadata
            else:
                port = self._soap_identifier(discovered.get("port", "port"))
                path = f"{service}.{port}.{operation}"
                if path in self.operations:
                    raise ValueError(f"Duplicate SOAP operation path: {path}")

            placeholder = self._soap_placeholder(metadata)
            self._attach_operation(path, placeholder)
            attached = self.operations[path]
            attached.__giso_soap__ = dict(metadata)
            original = getattr(attached, "__giso_original__", None)
            if original is not None:
                original.__giso_soap__ = dict(metadata)

    def _soap_operation_metadata(
        self,
        source: dict[str, Any],
        discovered: dict[str, Any],
    ) -> dict[str, str]:
        if source.get("endpoint_overridden"):
            endpoint = source["endpoint"]
        else:
            endpoint = discovered.get("endpoint", "") or source["endpoint"]
        return {
            "service": str(discovered.get("service", "")),
            "port": str(discovered.get("port", "")),
            "binding": str(discovered.get("binding", "")),
            "operation": str(discovered.get("name", "")),
            "soap_action": str(discovered.get("soap_action", "")),
            "soap_version": str(discovered.get("soap_version", "")),
            "endpoint": str(endpoint),
            "wsdl_source": str(source["source"]),
        }

    @staticmethod
    def _soap_placeholder(metadata: dict[str, str]):
        def operation(*args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError(
                "SOAP operation execution is not implemented yet; "
                "envelope serialization is added in the next chunk"
            )

        operation.__name__ = Giso._soap_identifier(metadata["operation"])
        operation.__doc__ = (
            f"SOAP {metadata['soap_version']} operation {metadata['service']}."
            f"{metadata['operation']} at {metadata['endpoint']}"
        )
        operation.__giso_soap__ = dict(metadata)
        return operation

    @classmethod
    def _soap_identifier(cls, value: str) -> str:
        value = value.strip() or "operation"
        normalized = cls._snake_case(value)
        normalized = "".join(character if character.isalnum() or character == "_" else "_" for character in normalized)
        normalized = normalized.strip("_") or "operation"
        if normalized[0].isdigit():
            normalized = f"_{normalized}"
        return normalized
