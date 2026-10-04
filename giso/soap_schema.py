from __future__ import annotations

from typing import Any, Mapping
import xml.etree.ElementTree as ET

from .soap import XSD
from .soap_transport import Giso as SoapTransportGiso


class UnsupportedSoapSchema(ValueError):
    """Raised when a WSDL uses an XSD construct outside Giso's supported subset."""


class Giso(SoapTransportGiso):
    """A SOAP-capable Giso with explicit boundaries for its supported XSD subset."""

    def _serialize_complex_value(
        self,
        source: dict[str, Any],
        parent: ET.Element,
        values: Mapping[str, Any],
        schema_node: ET.Element | None,
    ) -> None:
        complex_type = self._complex_type_for(source, schema_node)
        if complex_type is None:
            return super()._serialize_complex_value(source, parent, values, schema_node)

        self._validate_complex_type_shape(complex_type)
        sequence = complex_type.find(f"{{{XSD}}}sequence")
        if sequence is None:
            return super()._serialize_complex_value(source, parent, values, schema_node)

        known = set()
        for child_schema in sequence.findall(f"{{{XSD}}}element"):
            name = child_schema.attrib.get("name")
            if not name:
                continue
            known.add(name)
            min_occurs = self._occurs(child_schema.attrib.get("minOccurs", "1"), name)
            max_occurs = self._max_occurs(child_schema.attrib.get("maxOccurs", "1"), name)
            if name not in values:
                if min_occurs > 0:
                    raise ValueError(f"Missing required SOAP body field: {name}")
                continue

            value = values[name]
            if max_occurs != 1:
                if not isinstance(value, (list, tuple)):
                    raise TypeError(f"Repeated SOAP body field must be a list or tuple: {name}")
                items = list(value)
                if len(items) < min_occurs:
                    raise ValueError(
                        f"SOAP body field {name} requires at least {min_occurs} value(s)"
                    )
                if max_occurs is not None and len(items) > max_occurs:
                    raise ValueError(
                        f"SOAP body field {name} allows at most {max_occurs} value(s)"
                    )
            else:
                items = [value]

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
        self._validate_simple_restriction(source, schema_node, value)
        super()._serialize_schema_value(source, element, value, schema_node)

    def _validate_complex_type_shape(self, complex_type: ET.Element) -> None:
        unsupported = {
            "choice": "xsd:choice",
            "all": "xsd:all",
            "complexContent": "xsd:complexContent",
            "simpleContent": "xsd:simpleContent",
            "attribute": "XSD attributes",
            "any": "xsd:any",
        }
        for local_name, label in unsupported.items():
            if complex_type.find(f"{{{XSD}}}{local_name}") is not None:
                raise UnsupportedSoapSchema(f"Unsupported SOAP schema construct: {label}")

    def _validate_simple_restriction(
        self,
        source: dict[str, Any],
        schema_node: ET.Element,
        value: Any,
    ) -> None:
        if value is None:
            return
        simple_type = schema_node.find(f"{{{XSD}}}simpleType")
        if simple_type is None:
            type_ref = schema_node.attrib.get("type", "")
            if type_ref:
                simple_type = self._find_named_simple_type(source, schema_node, type_ref)
        if simple_type is None:
            return

        restriction = simple_type.find(f"{{{XSD}}}restriction")
        if restriction is None:
            raise UnsupportedSoapSchema("Unsupported SOAP schema construct: non-restriction simpleType")
        enums = [item.attrib.get("value", "") for item in restriction.findall(f"{{{XSD}}}enumeration")]
        if enums and str(value) not in enums:
            raise ValueError(
                f"SOAP body field {schema_node.attrib.get('name', '')} must be one of: {', '.join(enums)}"
            )

    def _find_named_simple_type(
        self,
        source: dict[str, Any],
        schema_node: ET.Element,
        type_ref: str,
    ) -> ET.Element | None:
        local = type_ref.split(":", 1)[-1]
        if type_ref.startswith("xsd:") or type_ref.startswith("xs:"):
            return None
        for schema in self._schemas(source["documents"]):
            for candidate in schema.findall(f"{{{XSD}}}simpleType"):
                if candidate.attrib.get("name") == local:
                    return candidate
        return None

    @staticmethod
    def _occurs(value: str, name: str) -> int:
        try:
            result = int(value)
        except ValueError as exc:
            raise UnsupportedSoapSchema(
                f"Unsupported minOccurs value for SOAP body field {name}: {value}"
            ) from exc
        if result < 0:
            raise UnsupportedSoapSchema(
                f"Unsupported minOccurs value for SOAP body field {name}: {value}"
            )
        return result

    @staticmethod
    def _max_occurs(value: str, name: str) -> int | None:
        if value == "unbounded":
            return None
        try:
            result = int(value)
        except ValueError as exc:
            raise UnsupportedSoapSchema(
                f"Unsupported maxOccurs value for SOAP body field {name}: {value}"
            ) from exc
        if result < 0:
            raise UnsupportedSoapSchema(
                f"Unsupported maxOccurs value for SOAP body field {name}: {value}"
            )
        return result
