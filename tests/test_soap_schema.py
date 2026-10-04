from __future__ import annotations

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


def schema_giso(wsdl_file, schema: str) -> Giso:
    return Giso().soap(wsdl_file(WSDL_TEMPLATE.format(schema=schema)))


def test_inline_enum_restriction_is_enforced(wsdl_file):
    giso = schema_giso(
        wsdl_file,
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

    assert b"fast" in giso.test_service.run.prepare(body={"Mode": "fast"}).body
    with pytest.raises(ValueError, match="fast, safe"):
        giso.test_service.run.prepare(body={"Mode": "turbo"})


def test_named_enum_restriction_is_enforced(wsdl_file):
    giso = schema_giso(
        wsdl_file,
        """
        <xsd:simpleType name="Mode"><xsd:restriction base="xsd:string">
          <xsd:enumeration value="fast"/><xsd:enumeration value="safe"/>
        </xsd:restriction></xsd:simpleType>
        <xsd:element name="Request"><xsd:complexType><xsd:sequence>
          <xsd:element name="Mode" type="tns:Mode"/>
        </xsd:sequence></xsd:complexType></xsd:element>
        """,
    )

    with pytest.raises(ValueError, match="fast, safe"):
        giso.test_service.run.prepare(body={"Mode": "other"})


def test_finite_occurrence_bounds_are_enforced(wsdl_file):
    giso = schema_giso(
        wsdl_file,
        """
        <xsd:element name="Request"><xsd:complexType><xsd:sequence>
          <xsd:element name="Tag" type="xsd:string" minOccurs="2" maxOccurs="3"/>
        </xsd:sequence></xsd:complexType></xsd:element>
        """,
    )

    giso.test_service.run.prepare(body={"Tag": ["a", "b"]})
    with pytest.raises(ValueError, match="at least 2"):
        giso.test_service.run.prepare(body={"Tag": ["a"]})
    with pytest.raises(ValueError, match="at most 3"):
        giso.test_service.run.prepare(body={"Tag": ["a", "b", "c", "d"]})


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (
            """
            <xsd:element name="Request"><xsd:complexType><xsd:choice>
              <xsd:element name="A" type="xsd:string"/><xsd:element name="B" type="xsd:string"/>
            </xsd:choice></xsd:complexType></xsd:element>
            """,
            "xsd:choice",
        ),
        (
            """
            <xsd:element name="Request"><xsd:complexType><xsd:sequence>
              <xsd:element name="Value" type="xsd:string"/>
            </xsd:sequence><xsd:attribute name="kind" type="xsd:string"/></xsd:complexType></xsd:element>
            """,
            "attributes",
        ),
    ],
)
def test_unsupported_schema_constructs_fail_explicitly(wsdl_file, schema, message):
    giso = schema_giso(wsdl_file, schema)

    with pytest.raises(UnsupportedSoapSchema, match=message):
        giso.test_service.run.prepare(body={"A": "x", "Value": "x"})


def test_cloned_soap_operation_keeps_prepare_execution_and_results(wsdl_file, bytes_response, monkeypatch):
    giso = schema_giso(
        wsdl_file,
        """
        <xsd:element name="Request"><xsd:complexType><xsd:sequence>
          <xsd:element name="Value" type="xsd:string"/>
        </xsd:sequence></xsd:complexType></xsd:element>
        """,
    )
    folded = Giso(giso)

    request = folded.test_service.run.prepare(body={"Value": "hello"})
    assert request.endpoint == "https://api.example.com/soap"

    response_xml = b'''<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
      <soap:Body><RunResponse xmlns="urn:test"><Ok>true</Ok></RunResponse></soap:Body>
    </soap:Envelope>'''
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=30: bytes_response(response_xml),
    )

    assert folded.test_service.run(body={"Value": "hello"}) == {"Ok": True}
    assert folded.results.last == {"Ok": True}
