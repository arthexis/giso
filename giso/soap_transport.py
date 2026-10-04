from __future__ import annotations

import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Mapping

from .soap_envelope import Giso as SoapEnvelopeGiso, SOAP11_ENV, SOAP12_ENV, SoapRequest


@dataclass(frozen=True)
class SoapFault(Exception):
    """A SOAP 1.1 or 1.2 Fault returned by the remote service."""

    code: str
    reason: str
    detail: Any = None
    soap_version: str = ""

    def __str__(self) -> str:
        message = self.reason or self.code or "SOAP Fault"
        return f"SOAP Fault {self.code}: {message}" if self.code else f"SOAP Fault: {message}"


class Giso(SoapEnvelopeGiso):
    """A Giso whose folded SOAP operations execute HTTP requests."""

    def _soap_placeholder(
        self,
        metadata: dict[str, str],
        source: dict[str, Any] | None = None,
        discovered: dict[str, Any] | None = None,
    ):
        if source is None or discovered is None:
            return super()._soap_placeholder(metadata, source, discovered)

        def prepare(*, body: Mapping[str, Any] | None = None) -> SoapRequest:
            values = {} if body is None else body
            if not isinstance(values, Mapping):
                raise TypeError("SOAP body must be a mapping")
            return self._serialize_soap_request(source, discovered, metadata, values)

        def operation(*, body: Mapping[str, Any] | None = None) -> Any:
            return self._execute_soap_request(prepare(body=body))

        operation.__name__ = self._soap_identifier(metadata["operation"])
        operation.__doc__ = (
            f"Execute SOAP {metadata['soap_version']} operation "
            f"{metadata['service']}.{metadata['operation']}"
        )
        operation.__giso_soap__ = dict(metadata)
        operation.prepare = prepare
        prepare.__giso_soap__ = dict(metadata)
        return operation

    def _execute_soap_request(self, request: SoapRequest) -> Any:
        http_request = urllib.request.Request(
            request.endpoint,
            data=request.body,
            headers=request.headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            if payload:
                try:
                    return self._decode_soap_response(payload, request.soap_version)
                except SoapFault:
                    raise
                except ValueError:
                    pass
            raise ValueError(f"SOAP request failed ({exc.code}): {request.endpoint}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ValueError(f"SOAP request failed: {request.endpoint}") from exc

        return self._decode_soap_response(payload, request.soap_version)

    def _decode_soap_response(self, payload: bytes, soap_version: str) -> Any:
        upper = payload.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise ValueError("SOAP response contains disallowed XML declarations")
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise ValueError("SOAP response is not valid XML") from exc

        expected_ns = SOAP12_ENV if soap_version == "1.2" else SOAP11_ENV
        namespace, local_name = self._split_tag(root.tag)
        if local_name != "Envelope" or namespace != expected_ns:
            raise ValueError("SOAP response has an unexpected envelope")
        body = root.find(f"{{{expected_ns}}}Body")
        if body is None:
            raise ValueError("SOAP response has no Body")

        children = list(body)
        if not children:
            return None
        first = children[0]
        if self._split_tag(first.tag)[1] == "Fault":
            raise self._soap_fault(first, soap_version)
        return self._xml_value(first)

    def _soap_fault(self, fault: ET.Element, soap_version: str) -> SoapFault:
        if soap_version == "1.2":
            code_node = fault.find(f"{{{SOAP12_ENV}}}Code/{{{SOAP12_ENV}}}Value")
            reason_node = fault.find(f"{{{SOAP12_ENV}}}Reason/{{{SOAP12_ENV}}}Text")
            detail_node = fault.find(f"{{{SOAP12_ENV}}}Detail")
        else:
            code_node = fault.find("faultcode")
            reason_node = fault.find("faultstring")
            detail_node = fault.find("detail")
        code = "" if code_node is None or code_node.text is None else code_node.text.strip()
        reason = "" if reason_node is None or reason_node.text is None else reason_node.text.strip()
        detail = None if detail_node is None else self._xml_value(detail_node)
        return SoapFault(code=code, reason=reason, detail=detail, soap_version=soap_version)

    @classmethod
    def _xml_value(cls, element: ET.Element) -> Any:
        children = list(element)
        if not children:
            text = (element.text or "").strip()
            if not text:
                return None
            lowered = text.lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
            return text

        result: dict[str, Any] = {}
        for child in children:
            name = cls._split_tag(child.tag)[1]
            value = cls._xml_value(child)
            if name in result:
                existing = result[name]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    result[name] = [existing, value]
            else:
                result[name] = value
        return result
