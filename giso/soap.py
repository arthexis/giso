from __future__ import annotations

import copy
import io
import pathlib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Mapping

from .openapi import Giso as OpenAPIGiso

WSDL11 = "http://schemas.xmlsoap.org/wsdl/"
WSDL20 = "http://www.w3.org/ns/wsdl"
SOAP11 = "http://schemas.xmlsoap.org/wsdl/soap/"
SOAP12 = "http://schemas.xmlsoap.org/wsdl/soap12/"
XSD = "http://www.w3.org/2001/XMLSchema"


class Giso(OpenAPIGiso):
    """A Giso that can also load SOAP services described by WSDL 1.1."""

    def __init__(self, *sources: Any, name: str = "giso"):
        self._soap_sources: list[dict[str, Any]] = []
        super().__init__(*sources, name=name)

    def _fold_string(self, source: str) -> None:
        if source.startswith("soap:"):
            self._fold_soap(source[len("soap:") :])
            return
        super()._fold_string(source)

    def _fold_giso(self, source: Any) -> None:
        super()._fold_giso(source)
        for entry in getattr(source, "_soap_sources", ()):
            if entry not in self._soap_sources:
                self._soap_sources.append(copy.deepcopy(entry))

    def soap(
        self,
        source: str | pathlib.Path,
        *,
        endpoint: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> "Giso":
        """Load a WSDL service description and retain its SOAP discovery model."""
        self._fold_soap(source, endpoint=endpoint, headers=headers)
        return self

    def _fold_soap(
        self,
        source: str | pathlib.Path,
        *,
        endpoint: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        source_text = str(source)
        if not source_text.strip():
            raise ValueError("SOAP source must not be empty")

        request_headers = self._validate_soap_headers(headers)
        root_source, documents = self._load_wsdl_graph(source, request_headers)
        root_document = documents[root_source]
        version = self._validate_root_wsdl(root_document)
        model = self._discover_wsdl11(documents, root_source)
        resolved_endpoint = self._resolve_soap_endpoint(model, endpoint)

        self._soap_sources.append(
            {
                "source": source_text,
                "root_source": root_source,
                "version": version,
                "endpoint": resolved_endpoint,
                "headers": request_headers,
                "documents": documents,
                "model": model,
            }
        )
        self.provenance.append(
            {
                "type": "soap",
                "source": source_text,
                "wsdl_version": version,
                "endpoint": resolved_endpoint,
                "documents": str(len(documents)),
            }
        )

    def _load_wsdl_graph(
        self,
        source: str | pathlib.Path,
        headers: Mapping[str, str],
    ) -> tuple[str, dict[str, dict[str, Any]]]:
        root_source = self._canonical_soap_source(source)
        documents: dict[str, dict[str, Any]] = {}
        pending = [root_source]

        while pending:
            current = pending.pop(0)
            if current in documents:
                continue
            payload = self._load_soap_resource(current, headers)
            document = self._parse_xml_document(current, payload)
            documents[current] = document

            for location in self._document_references(document):
                resolved = self._resolve_soap_reference(current, location)
                if resolved not in documents and resolved not in pending:
                    pending.append(resolved)

        return root_source, documents

    @staticmethod
    def _canonical_soap_source(source: str | pathlib.Path) -> str:
        if isinstance(source, pathlib.Path):
            return str(source.expanduser().resolve())
        text = source.strip()
        parsed = urllib.parse.urlparse(text)
        if parsed.scheme:
            if parsed.scheme != "https":
                raise ValueError("Remote SOAP/WSDL sources must use HTTPS")
            return text
        return str(pathlib.Path(text).expanduser().resolve())

    @staticmethod
    def _load_soap_resource(source: str, headers: Mapping[str, str]) -> bytes:
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme == "https":
            request_headers = {
                "Accept": "application/wsdl+xml, application/xml, text/xml",
                "User-Agent": "giso",
            }
            request_headers.update(headers)
            request = urllib.request.Request(source, headers=request_headers)
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                raise ValueError(f"SOAP/WSDL request failed ({exc.code}): {source}") from exc
            except (urllib.error.URLError, OSError) as exc:
                raise ValueError(f"Cannot load SOAP/WSDL document: {source}") from exc

        path = pathlib.Path(source)
        if not path.is_file():
            raise ValueError(f"SOAP/WSDL file does not exist: {path}")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Cannot load SOAP/WSDL document: {path}") from exc

    @staticmethod
    def _parse_xml_document(source: str, payload: bytes) -> dict[str, Any]:
        upper = payload.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise ValueError(f"SOAP/WSDL XML declarations are not allowed: {source}")
        try:
            namespaces: dict[str, str] = {}
            for _event, item in ET.iterparse(io.BytesIO(payload), events=("start-ns",)):
                prefix, uri = item
                namespaces[prefix or ""] = uri
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise ValueError(f"Cannot parse SOAP/WSDL XML document: {source}") from exc

        namespace, local_name = Giso._split_tag(root.tag)
        kind = "xml"
        version = ""
        if namespace == WSDL11 and local_name == "definitions":
            kind = "wsdl"
            version = "1.1"
        elif namespace == WSDL20 and local_name == "description":
            kind = "wsdl"
            version = "2.0"
        elif namespace == XSD and local_name == "schema":
            kind = "xsd"

        return {
            "source": source,
            "kind": kind,
            "version": version,
            "target_namespace": root.attrib.get("targetNamespace", ""),
            "namespaces": namespaces,
            "root": root,
        }

    @staticmethod
    def _document_references(document: dict[str, Any]) -> list[str]:
        root: ET.Element = document["root"]
        references: list[str] = []
        tags_and_attributes = (
            (f"{{{WSDL11}}}import", "location"),
            (f"{{{WSDL20}}}import", "location"),
            (f"{{{WSDL20}}}include", "location"),
            (f"{{{XSD}}}import", "schemaLocation"),
            (f"{{{XSD}}}include", "schemaLocation"),
        )
        for tag, attribute in tags_and_attributes:
            for element in root.iter(tag):
                location = element.attrib.get(attribute)
                if isinstance(location, str) and location.strip():
                    references.append(location.strip())
        return references

    @staticmethod
    def _resolve_soap_reference(base: str, location: str) -> str:
        base_url = urllib.parse.urlparse(base)
        location_url = urllib.parse.urlparse(location)
        if base_url.scheme == "https":
            resolved = urllib.parse.urljoin(base, location)
            if urllib.parse.urlparse(resolved).scheme != "https":
                raise ValueError("Remote SOAP/WSDL imports must use HTTPS")
            return resolved
        if location_url.scheme:
            if location_url.scheme != "https":
                raise ValueError("SOAP/WSDL imports may only use local files or HTTPS")
            return location
        return str((pathlib.Path(base).parent / location).expanduser().resolve())

    @staticmethod
    def _validate_root_wsdl(document: dict[str, Any]) -> str:
        if document["kind"] != "wsdl":
            raise ValueError("SOAP source must resolve to a WSDL document")
        if document["version"] != "1.1":
            raise ValueError("Only WSDL 1.1 documents are supported")
        return "1.1"

    @classmethod
    def _discover_wsdl11(
        cls,
        documents: dict[str, dict[str, Any]],
        root_source: str,
    ) -> dict[str, Any]:
        bindings: dict[tuple[str, str], dict[str, Any]] = {}
        services: list[dict[str, Any]] = []

        for document in documents.values():
            if document["version"] != "1.1":
                continue
            root: ET.Element = document["root"]
            target_namespace = document["target_namespace"]
            for binding in root.findall(f"{{{WSDL11}}}binding"):
                name = binding.attrib.get("name", "")
                if not name:
                    continue
                soap_binding = binding.find(f"{{{SOAP11}}}binding")
                soap_version = "1.1"
                if soap_binding is None:
                    soap_binding = binding.find(f"{{{SOAP12}}}binding")
                    soap_version = "1.2"
                if soap_binding is None:
                    continue
                operations: list[dict[str, str]] = []
                for operation in binding.findall(f"{{{WSDL11}}}operation"):
                    operation_name = operation.attrib.get("name", "")
                    if not operation_name:
                        continue
                    soap_operation = operation.find(
                        f"{{{SOAP11 if soap_version == '1.1' else SOAP12}}}operation"
                    )
                    operations.append(
                        {
                            "name": operation_name,
                            "soap_action": "" if soap_operation is None else soap_operation.attrib.get("soapAction", ""),
                            "style": "" if soap_operation is None else soap_operation.attrib.get("style", ""),
                        }
                    )
                bindings[(target_namespace, name)] = {
                    "name": name,
                    "namespace": target_namespace,
                    "soap_version": soap_version,
                    "transport": soap_binding.attrib.get("transport", ""),
                    "style": soap_binding.attrib.get("style", ""),
                    "operations": operations,
                }

        for document in documents.values():
            if document["version"] != "1.1":
                continue
            root: ET.Element = document["root"]
            for service in root.findall(f"{{{WSDL11}}}service"):
                service_name = service.attrib.get("name", "")
                ports: list[dict[str, Any]] = []
                for port in service.findall(f"{{{WSDL11}}}port"):
                    binding_ref = port.attrib.get("binding", "")
                    binding_key = cls._resolve_qname(document, binding_ref)
                    binding = bindings.get(binding_key)
                    address = port.find(f"{{{SOAP11}}}address")
                    soap_version = "1.1"
                    if address is None:
                        address = port.find(f"{{{SOAP12}}}address")
                        soap_version = "1.2"
                    endpoint = "" if address is None else address.attrib.get("location", "")
                    ports.append(
                        {
                            "name": port.attrib.get("name", ""),
                            "binding": binding_ref,
                            "binding_name": "" if binding is None else binding["name"],
                            "soap_version": soap_version if binding is None else binding["soap_version"],
                            "endpoint": endpoint,
                            "operations": [] if binding is None else copy.deepcopy(binding["operations"]),
                        }
                    )
                services.append({"name": service_name, "ports": ports})

        operations: list[dict[str, str]] = []
        endpoints: list[str] = []
        for service in services:
            for port in service["ports"]:
                endpoint = port["endpoint"]
                if endpoint and endpoint not in endpoints:
                    endpoints.append(endpoint)
                for operation in port["operations"]:
                    operations.append(
                        {
                            "service": service["name"],
                            "port": port["name"],
                            "binding": port["binding_name"],
                            "name": operation["name"],
                            "soap_action": operation["soap_action"],
                            "soap_version": port["soap_version"],
                            "endpoint": endpoint,
                        }
                    )

        return {
            "root_source": root_source,
            "target_namespace": documents[root_source]["target_namespace"],
            "bindings": list(bindings.values()),
            "services": services,
            "operations": operations,
            "endpoints": endpoints,
        }

    @staticmethod
    def _resolve_qname(document: dict[str, Any], value: str) -> tuple[str, str]:
        if not value:
            return "", ""
        if ":" in value:
            prefix, local = value.split(":", 1)
            return document["namespaces"].get(prefix, ""), local
        namespace = document["namespaces"].get("", document["target_namespace"])
        return namespace, value

    @classmethod
    def _resolve_soap_endpoint(cls, model: dict[str, Any], override: str | None) -> str:
        if override is not None:
            return cls._validate_soap_endpoint(override)
        endpoints = model["endpoints"]
        if not endpoints:
            raise ValueError("WSDL contains no SOAP endpoint; pass endpoint explicitly")
        return cls._validate_soap_endpoint(endpoints[0])

    @staticmethod
    def _validate_soap_endpoint(value: str) -> str:
        value = value.strip()
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("SOAP endpoint must be an absolute HTTP(S) URL")
        return value

    @staticmethod
    def _validate_soap_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
        if headers is None:
            return {}
        result: dict[str, str] = {}
        for name, value in headers.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("SOAP header names must be non-empty strings")
            if not isinstance(value, str):
                raise ValueError("SOAP header values must be strings")
            result[name] = value
        return result

    @staticmethod
    def _split_tag(tag: str) -> tuple[str, str]:
        if tag.startswith("{") and "}" in tag:
            namespace, local = tag[1:].split("}", 1)
            return namespace, local
        return "", tag
