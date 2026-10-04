from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import xml.etree.ElementTree as ET

from .soap import SOAP11, SOAP12, WSDL11, XSD
from .soap_operations import Giso as SoapOperationsGiso

SOAP11_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
SOAP12_ENV = "http://www.w3.org/2003/05/soap-envelope"


@dataclass(frozen=True)
class SoapRequest:
    """A serialized SOAP request ready for transport."""

    endpoint: str
    headers: dict[str, str]
    body: bytes
    soap_version: str
    soap_action: str


class Giso(SoapOperationsGiso):
    """A Giso that serializes discovered SOAP operations into request envelopes."""

    def _soap_placeholder(
        self,
        metadata: dict[str, str],
        source: dict[str, Any] | None = None,
        discovered: dict[str, Any] | None = None,
    ):
        if source is None or discovered is None:
            return super()._soap_placeholder(metadata, source, discovered)

        def operation(*, body: Mapping[str, Any] | None = None) -> SoapRequest:
            values = {} if body is None else body
            if not isinstance(values, Mapping):
                raise TypeError("SOAP body must be a mapping")
            return self._serialize_soap_request(source, discovered, metadata, values)

        operation.__name__ = self._soap_identifier(metadata["operation"])
        operation.__doc__ = (
            f"Serialize SOAP {metadata['soap_version']} operation "
            f"{metadata['service']}.{metadata['operation']}"
        )
        operation.__giso_soap__ = dict(metadata)
        return operation

    def _serialize_soap_request(
        self,
        source: dict[str, Any],
        discovered: dict[str, Any],
        metadata: dict[str, str],
        values: Mapping[str, Any],
    ) -> SoapRequest:
        input_element = self._soap_input_element(source, discovered)
        if input_element is None:
            namespace = source["model"].get("target_namespace", "")
            element_name = metadata["operation"]
            schema = None
        else:
            namespace, element_name, schema = input_element

        envelope_ns = SOAP12_ENV if metadata["soap_version"] == "1.2" else SOAP11_ENV
        envelope = ET.Element(f"{{{envelope_ns}}}Envelope")
        body = ET.SubElement(envelope, f"{{{envelope_ns}}}Body")
        payload = ET.SubElement(body, self._qualified(namespace, element_name))
        self._serialize_complex_value(source, payload, values, schema)
        xml = ET.tostring(envelope, encoding="utf-8", xml_declaration=True)

        headers = dict(source.get("headers", {}))
        action = metadata.get("soap_action", "")
        if metadata["soap_version"] == "1.2":
            content_type = "application/soap+xml; charset=utf-8"
            if action:
                content_type += f'; action="{action}"'
            headers.setdefault("Content-Type", content_type)
        else:
            headers.setdefault("Content-Type", "text/xml; charset=utf-8")
            if action:
                headers.setdefault("SOAPAction", f'"{action}"')

        return SoapRequest(
            endpoint=metadata["endpoint"],
            headers=headers,
            body=xml,
            soap_version=metadata["soap_version"],
            soap_action=action,
        )

    def _soap_input_element(
        self,
        source: dict[str, Any],
        discovered: dict[str, Any],
    ) -> tuple[str, str, ET.Element | None] | None:
        documents = source["documents"]
        binding_name = discovered.get("binding", "")
        operation_name = discovered.get("name", "")

        binding_document = None
        binding = None
        for document in documents.values():
            if document.get("version") != "1.1":
                continue
            root = document["root"]
            for candidate in root.findall(f"{{{WSDL11}}}binding"):
                if candidate.attrib.get("name") == binding_name:
                    binding_document, binding = document, candidate
                    break
            if binding is not None:
                break
        if binding_document is None or binding is None:
            return None

        port_type_ref = binding.attrib.get("type", "")
        port_type_qname = self._resolve_qname(binding_document, port_type_ref)
        port_type_document, port_type = self._find_wsdl_named(
            documents, "portType", port_type_qname
        )
        if port_type_document is None or port_type is None:
            return None

        port_operation = next(
            (
                item
                for item in port_type.findall(f"{{{WSDL11}}}operation")
                if item.attrib.get("name") == operation_name
            ),
            None,
        )
        if port_operation is None:
            return None
        input_node = port_operation.find(f"{{{WSDL11}}}input")
        if input_node is None:
            return None
        message_qname = self._resolve_qname(port_type_document, input_node.attrib.get("message", ""))
        message_document, message = self._find_wsdl_named(documents, "message", message_qname)
        if message_document is None or message is None:
            return None

        part = message.find(f"{{{WSDL11}}}part")
        if part is None:
            return None
        element_ref = part.attrib.get("element", "")
        if element_ref:
            element_qname = self._resolve_qname(message_document, element_ref)
            schema_element = self._find_xsd_element(documents, element_qname)
            return element_qname[0], element_qname[1], schema_element

        type_ref = part.attrib.get("type", "")
        if type_ref:
            type_qname = self._resolve_qname(message_document, type_ref)
            return source["model"].get("target_namespace", ""), operation_name, self._find_xsd_type(documents, type_qname)
        return None

    @classmethod
    def _find_wsdl_named(
        cls,
        documents: dict[str, dict[str, Any]],
        local_name: str,
        qname: tuple[str, str],
    ) -> tuple[dict[str, Any] | None, ET.Element | None]:
        namespace, name = qname
        for document in documents.values():
            if document.get("version") != "1.1" or document.get("target_namespace") != namespace:
                continue
            for element in document["root"].findall(f"{{{WSDL11}}}{local_name}"):
                if element.attrib.get("name") == name:
                    return document, element
        return None, None

    @classmethod
    def _find_xsd_element(
        cls,
        documents: dict[str, dict[str, Any]],
        qname: tuple[str, str],
    ) -> ET.Element | None:
        namespace, name = qname
        for schema in cls._schemas(documents):
            if schema.attrib.get("targetNamespace", "") != namespace:
                continue
            for element in schema.findall(f"{{{XSD}}}element"):
                if element.attrib.get("name") == name:
                    return element
        return None

    @classmethod
    def _find_xsd_type(
        cls,
        documents: dict[str, dict[str, Any]],
        qname: tuple[str, str],
    ) -> ET.Element | None:
        namespace, name = qname
        for schema in cls._schemas(documents):
            if schema.attrib.get("targetNamespace", "") != namespace:
                continue
            for element in schema.findall(f"{{{XSD}}}complexType"):
                if element.attrib.get("name") == name:
                    return element
        return None

    @staticmethod
    def _schemas(documents: dict[str, dict[str, Any]]):
        for document in documents.values():
            root = document["root"]
            if document.get("kind") == "xsd":
                yield root
            for schema in root.iter(f"{{{XSD}}}schema"):
                yield schema

    def _serialize_complex_value(
        self,
        source: dict[str, Any],
        parent: ET.Element,
        values: Mapping[str, Any],
        schema_node: ET.Element | None,
    ) -> None:
        complex_type = self._complex_type_for(source, schema_node)
        sequence = None if complex_type is None else complex_type.find(f"{{{XSD}}}sequence")
        if sequence is None:
            for key, value in values.items():
                self._append_untyped(parent, str(key), value)
            return

        known = set()
        for child_schema in sequence.findall(f"{{{XSD}}}element"):
            name = child_schema.attrib.get("name")
            if not name:
                continue
            known.add(name)
            required = child_schema.attrib.get("minOccurs", "1") != "0"
            repeated = child_schema.attrib.get("maxOccurs", "1") not in {"0", "1"}
            if name not in values:
                if required:
                    raise ValueError(f"Missing required SOAP body field: {name}")
                continue
            value = values[name]
            items = value if repeated and isinstance(value, (list, tuple)) else [value]
            if repeated and not isinstance(value, (list, tuple)):
                raise TypeError(f"Repeated SOAP body field must be a list or tuple: {name}")
            for item in items:
                child = ET.SubElement(parent, name)
                self._serialize_schema_value(source, child, item, child_schema)

        unknown = set(values) - known
        if unknown:
            raise ValueError(f"Unknown SOAP body fields: {', '.join(sorted(map(str, unknown)))}")

    def _serialize_schema_value(
        self,
        source: dict[str, Any],
        element: ET.Element,
        value: Any,
        schema_node: ET.Element,
    ) -> None:
        if value is None:
            if schema_node.attrib.get("nillable", "false").lower() == "true":
                element.set("{http://www.w3.org/2001/XMLSchema-instance}nil", "true")
                return
            return
        complex_type = self._complex_type_for(source, schema_node)
        if complex_type is not None:
            if not isinstance(value, Mapping):
                raise TypeError(f"SOAP complex field must be a mapping: {schema_node.attrib.get('name', '')}")
            self._serialize_complex_value(source, element, value, complex_type)
            return
        element.text = self._soap_scalar(value)

    def _complex_type_for(
        self,
        source: dict[str, Any],
        schema_node: ET.Element | None,
    ) -> ET.Element | None:
        if schema_node is None:
            return None
        if schema_node.tag == f"{{{XSD}}}complexType":
            return schema_node
        inline = schema_node.find(f"{{{XSD}}}complexType")
        if inline is not None:
            return inline
        type_ref = schema_node.attrib.get("type", "")
        if not type_ref:
            return None
        for document in source["documents"].values():
            namespaces = document.get("namespaces", {})
            if ":" in type_ref:
                prefix, local = type_ref.split(":", 1)
                namespace = namespaces.get(prefix, "")
            else:
                local = type_ref
                namespace = document.get("target_namespace", "")
            found = self._find_xsd_type(source["documents"], (namespace, local))
            if found is not None:
                return found
        return None

    @classmethod
    def _append_untyped(cls, parent: ET.Element, name: str, value: Any) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                cls._append_untyped(parent, name, item)
            return
        child = ET.SubElement(parent, name)
        if isinstance(value, Mapping):
            for nested_name, nested_value in value.items():
                cls._append_untyped(child, str(nested_name), nested_value)
        elif value is not None:
            child.text = cls._soap_scalar(value)

    @staticmethod
    def _soap_scalar(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _qualified(namespace: str, name: str) -> str:
        return f"{{{namespace}}}{name}" if namespace else name
