from __future__ import annotations

import io
import pathlib

import pytest

from giso import Giso


ROOT_WSDL = """<?xml version="1.0"?>
<wsdl:definitions
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    xmlns:imp="urn:binding"
    targetNamespace="urn:root">
  <wsdl:import namespace="urn:binding" location="binding.wsdl" />
  <wsdl:types>
    <xsd:schema targetNamespace="urn:root-types">
      <xsd:import namespace="urn:types" schemaLocation="schema.xsd" />
    </xsd:schema>
  </wsdl:types>
  <wsdl:service name="CustomerService">
    <wsdl:port name="CustomerPort" binding="imp:CustomerBinding">
      <soap:address location="https://api.example.com/soap" />
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>
"""

BINDING_WSDL = """<?xml version="1.0"?>
<wsdl:definitions
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    targetNamespace="urn:binding">
  <wsdl:binding name="CustomerBinding" type="CustomerPortType">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="document" />
    <wsdl:operation name="GetCustomer">
      <soap:operation soapAction="urn:GetCustomer" />
    </wsdl:operation>
    <wsdl:operation name="UpdateCustomer">
      <soap:operation soapAction="urn:UpdateCustomer" style="document" />
    </wsdl:operation>
  </wsdl:binding>
</wsdl:definitions>
"""

SCHEMA_XSD = """<?xml version="1.0"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:types">
  <xsd:include schemaLocation="common.xsd" />
</xsd:schema>
"""

COMMON_XSD = """<?xml version="1.0"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:types" />
"""


def write_graph(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "service.wsdl").write_text(ROOT_WSDL, encoding="utf-8")
    (tmp_path / "binding.wsdl").write_text(BINDING_WSDL, encoding="utf-8")
    (tmp_path / "schema.xsd").write_text(SCHEMA_XSD, encoding="utf-8")
    (tmp_path / "common.xsd").write_text(COMMON_XSD, encoding="utf-8")
    return tmp_path / "service.wsdl"


def test_soap_method_loads_wsdl_graph_and_discovers_operations(tmp_path):
    path = write_graph(tmp_path)

    giso = Giso()
    result = giso.soap(path)

    assert result is giso
    source = giso._soap_sources[-1]
    assert source["version"] == "1.1"
    assert source["endpoint"] == "https://api.example.com/soap"
    assert len(source["documents"]) == 4
    assert source["model"]["services"][0]["name"] == "CustomerService"
    assert source["model"]["services"][0]["ports"][0]["binding_name"] == "CustomerBinding"
    assert [operation["name"] for operation in source["model"]["operations"]] == [
        "GetCustomer",
        "UpdateCustomer",
    ]
    assert source["model"]["operations"][0]["soap_action"] == "urn:GetCustomer"


def test_soap_string_source_uses_explicit_prefix(tmp_path):
    path = write_graph(tmp_path)

    giso = Giso(f"soap:{path}")

    assert giso.provenance[-1]["type"] == "soap"
    assert giso.provenance[-1]["source"] == str(path)


def test_soap_records_provenance_without_headers(tmp_path):
    path = write_graph(tmp_path)

    giso = Giso().soap(path, headers={"Authorization": "Bearer secret"})

    assert giso.provenance[-1] == {
        "type": "soap",
        "source": str(path),
        "wsdl_version": "1.1",
        "endpoint": "https://api.example.com/soap",
        "documents": "4",
    }
    assert giso._soap_sources[-1]["headers"] == {"Authorization": "Bearer secret"}
    assert "secret" not in repr(giso.provenance)


def test_soap_endpoint_can_be_overridden(tmp_path):
    path = write_graph(tmp_path)

    giso = Giso().soap(path, endpoint="http://localhost:9000/service")

    assert giso._soap_sources[-1]["endpoint"] == "http://localhost:9000/service"
    assert giso.provenance[-1]["endpoint"] == "http://localhost:9000/service"


def test_https_wsdl_and_relative_imports_are_loaded(monkeypatch):
    payloads = {
        "https://example.com/service.wsdl": ROOT_WSDL.encode(),
        "https://example.com/binding.wsdl": BINDING_WSDL.encode(),
        "https://example.com/schema.xsd": SCHEMA_XSD.encode(),
        "https://example.com/common.xsd": COMMON_XSD.encode(),
    }
    seen = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def urlopen(request, timeout=30):
        seen.append((request.full_url, request.get_header("Authorization")))
        return Response(payloads[request.full_url])

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    giso = Giso().soap(
        "https://example.com/service.wsdl",
        headers={"Authorization": "Bearer secret"},
    )

    assert len(giso._soap_sources[-1]["documents"]) == 4
    assert {url for url, _header in seen} == set(payloads)
    assert all(header == "Bearer secret" for _url, header in seen)


def test_remote_wsdl_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        Giso().soap("http://example.com/service.wsdl")


def test_imported_remote_documents_cannot_downgrade_to_http(monkeypatch):
    wsdl = ROOT_WSDL.replace("binding.wsdl", "http://example.com/binding.wsdl")

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=30: Response(wsdl.encode()),
    )

    with pytest.raises(ValueError, match="imports must use HTTPS"):
        Giso().soap("https://example.com/service.wsdl")


def test_soap_rejects_doctype_and_entities(tmp_path):
    path = tmp_path / "unsafe.wsdl"
    path.write_text(
        """<!DOCTYPE definitions [<!ENTITY xxe SYSTEM \"file:///etc/passwd\">]>
        <wsdl:definitions xmlns:wsdl=\"http://schemas.xmlsoap.org/wsdl/\" targetNamespace=\"urn:x\">&xxe;</wsdl:definitions>""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="declarations are not allowed"):
        Giso().soap(path, endpoint="https://example.com/soap")


def test_soap_rejects_wsdl_2_for_now(tmp_path):
    path = tmp_path / "service.wsdl"
    path.write_text(
        '<description xmlns="http://www.w3.org/ns/wsdl" targetNamespace="urn:test" />',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Only WSDL 1.1"):
        Giso().soap(path, endpoint="https://example.com/soap")


def test_soap_requires_discovered_or_explicit_endpoint(tmp_path):
    path = tmp_path / "service.wsdl"
    path.write_text(
        '<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" targetNamespace="urn:test" />',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="pass endpoint explicitly"):
        Giso().soap(path)

    giso = Giso().soap(path, endpoint="https://example.com/soap")
    assert giso._soap_sources[-1]["endpoint"] == "https://example.com/soap"


def test_soap_sources_survive_giso_folding(tmp_path):
    path = write_graph(tmp_path)
    source = Giso().soap(path, headers={"X-Test": "yes"})

    folded = Giso(source)

    assert folded.provenance == source.provenance
    assert folded._soap_sources[0]["headers"] == {"X-Test": "yes"}
    assert folded._soap_sources[0]["model"] == source._soap_sources[0]["model"]
    assert folded._soap_sources[0] is not source._soap_sources[0]
