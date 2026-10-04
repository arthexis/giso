from __future__ import annotations

import io
import pathlib

import pytest

from giso import Giso, UnsupportedSoapSchema


WSDL_TEMPLATE = """<?xml version="1.0"?>
<wsdl:definitions
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:tns="urn:test"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    targetNamespace="urn:test">
  <wsdl:types>
    <xsd:schema targetNamespace="urn:test">
      {schema}
    </xsd:schema>
  </wsdl:types>
  <wsdl:message name="Input"><wsdl:part name="parameters" element="tns:Request" /></wsdl:message>
  <wsdl:portType name="PortType"><wsdl:operation name="Run"><wsdl:input message="tns:Input" /></wsdl:operation></wsdl:portType>
  <wsdl:binding name="Binding" type="tns:PortType">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="document" />
    <wsdl:operation name="Run"><soap:operation soapAction="urn:Run" /></wsdl:operation>
  </wsdl:binding>
  <wsdl:service name="TestService">
    <wsdl:port name="TestPort" binding="tns:Binding"><soap:address location="https://api.example.com/soap" /></wsdl:port>
  </wsdl:service>
</wsdl:definitions>
"""


def write_wsdl(tmp_path: pathlib.Path, schema: str) -> pathlib.Path:
    path = tmp_path / "service.wsdl"
    path.write_text(WSDL_TEMPLATE.format(schema=schema), encoding="utf-8")
    return path


def test_inline_enum_restriction_is_enforced(tmp_path):
    path = write_wsdl(
        tmp_path,
        """
        <xsd:element name="Request"><xsd:complexType><xsd:sequence>
          <xsd:element name="Mode">
            <xsd:simpleType><xsd:restriction base="xsd:string">
              <xsd:enumeration value="fast"/><xsd:enumeration value="safe"/>
            </xsd:restriction></xsd:simpleType>
          </xsd:element>
        </xsd:sequence></xsd:complexType></xsd:element>
        """,
    )
    giso = Giso().soap(path)

    request = giso.test_service.run.prepare(body={"Mode": "fast"})
    assert b"fast" in request.body
    with pytest.raises(ValueError, match="fast, safe"):
        giso.test_service.run.prepare(body={"Mode": "turbo"})


def test_named_enum_restriction_is_enforced(tmp_path):
    path = write_wsdl(
        tmp_path,
        """
        <xsd:simpleType name="Mode"><xsd:restriction base="xsd:string">
          <xsd:enumeration value="fast"/><xsd:enumeration value="safe"/>
        </xsd:restriction></xsd:simpleType>
        <xsd:element name="Request"><xsd:complexType><xsd:sequence>
          <xsd:element name="Mode" type="tns:Mode"/>
        </xsd:sequence></xsd:complexType></xsd:element>
        """,
    )
    giso = Giso().soap(path)

    with pytest.raises(ValueError, match="fast, safe"):
        giso.test_service.run.prepare(body={"Mode": "other"})


def test_finite_occurrence_bounds_are_enforced(tmp_path):
    path = write_wsdl(
        tmp_path,
        """
        <xsd:element name="Request"><xsd:complexType><xsd:sequence>
          <xsd:element name="Tag" type="xsd:string" minOccurs="2" maxOccurs="3"/>
        </xsd:sequence></xsd:complexType></xsd:element>
        """,
    )
    giso = Giso().soap(path)

    giso.test_service.run.prepare(body={"Tag": ["a", "b"]})
    with pytest.raises(ValueError, match="at least 2"):
        giso.test_service.run.prepare(body={"Tag": ["a"]})
    with pytest.raises(ValueError, match="at most 3"):
        giso.test_service.run.prepare(body={"Tag": ["a", "b", "c", "d"]})


def test_choice_fails_explicitly(tmp_path):
    path = write_wsdl(
        tmp_path,
        """
        <xsd:element name="Request"><xsd:complexType><xsd:choice>
          <xsd:element name="A" type="xsd:string"/><xsd:element name="B" type="xsd:string"/>
        </xsd:choice></xsd:complexType></xsd:element>
        """,
    )
    giso = Giso().soap(path)

    with pytest.raises(UnsupportedSoapSchema, match="xsd:choice"):
        giso.test_service.run.prepare(body={"A": "x"})


def test_attributes_fail_explicitly(tmp_path):
    path = write_wsdl(
        tmp_path,
        """
        <xsd:element name="Request"><xsd:complexType><xsd:sequence>
          <xsd:element name="Value" type="xsd:string"/>
        </xsd:sequence><xsd:attribute name="kind" type="xsd:string"/></xsd:complexType></xsd:element>
        """,
    )
    giso = Giso().soap(path)

    with pytest.raises(UnsupportedSoapSchema, match="attributes"):
        giso.test_service.run.prepare(body={"Value": "x"})


def test_cloned_soap_operation_keeps_prepare_and_executes(monkeypatch, tmp_path):
    path = write_wsdl(
        tmp_path,
        """
        <xsd:element name="Request"><xsd:complexType><xsd:sequence>
          <xsd:element name="Value" type="xsd:string"/>
        </xsd:sequence></xsd:complexType></xsd:element>
        """,
    )
    source = Giso().soap(path)
    folded = Giso(source)

    request = folded.test_service.run.prepare(body={"Value": "hello"})
    assert request.endpoint == "https://api.example.com/soap"

    response_xml = b'''<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
      <soap:Body><RunResponse xmlns="urn:test"><Ok>true</Ok></RunResponse></soap:Body>
    </soap:Envelope>'''

    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=30: Response(response_xml))
    assert folded.test_service.run(body={"Value": "hello"}) == {"Ok": True}
    assert folded.results.last == {"Ok": True}
