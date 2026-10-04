from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

import pytest

from giso import Giso


WSDL = """<?xml version="1.0"?>
<wsdl:definitions
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:tns="urn:customer"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema"
    targetNamespace="urn:customer">
  <wsdl:types>
    <xsd:schema targetNamespace="urn:customer">
      <xsd:element name="UpdateCustomerRequest">
        <xsd:complexType>
          <xsd:sequence>
            <xsd:element name="CustomerId" type="xsd:int" />
            <xsd:element name="Name" type="xsd:string" minOccurs="0" />
            <xsd:element name="Active" type="xsd:boolean" />
            <xsd:element name="Tag" type="xsd:string" minOccurs="0" maxOccurs="unbounded" />
            <xsd:element name="Address" minOccurs="0">
              <xsd:complexType>
                <xsd:sequence>
                  <xsd:element name="City" type="xsd:string" />
                  <xsd:element name="Zip" type="xsd:string" minOccurs="0" />
                </xsd:sequence>
              </xsd:complexType>
            </xsd:element>
          </xsd:sequence>
        </xsd:complexType>
      </xsd:element>
    </xsd:schema>
  </wsdl:types>
  <wsdl:message name="UpdateCustomerInput">
    <wsdl:part name="parameters" element="tns:UpdateCustomerRequest" />
  </wsdl:message>
  <wsdl:portType name="CustomerPortType">
    <wsdl:operation name="UpdateCustomer">
      <wsdl:input message="tns:UpdateCustomerInput" />
    </wsdl:operation>
  </wsdl:portType>
  <wsdl:binding name="CustomerBinding" type="tns:CustomerPortType">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="document" />
    <wsdl:operation name="UpdateCustomer">
      <soap:operation soapAction="urn:UpdateCustomer" />
    </wsdl:operation>
  </wsdl:binding>
  <wsdl:service name="CustomerService">
    <wsdl:port name="CustomerPort" binding="tns:CustomerBinding">
      <soap:address location="https://api.example.com/customer" />
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>
"""

SOAP12_WSDL = WSDL.replace(
    'xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"',
    'xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap12/"',
).replace(
    'transport="http://schemas.xmlsoap.org/soap/http"',
    'transport="http://www.w3.org/2003/05/soap/bindings/HTTP/"',
)


def write_wsdl(tmp_path: pathlib.Path, content: str = WSDL) -> pathlib.Path:
    path = tmp_path / "customer.wsdl"
    path.write_text(content, encoding="utf-8")
    return path


def payload_from(request, envelope_ns: str):
    root = ET.fromstring(request.body)
    body = root.find(f"{{{envelope_ns}}}Body")
    return list(body)[0]


def test_schema_backed_body_serializes_nested_optional_and_repeated_values(tmp_path):
    giso = Giso().soap(write_wsdl(tmp_path))

    request = giso.customer_service.update_customer(
        body={
            "CustomerId": 7,
            "Active": True,
            "Tag": ["vip", "beta"],
            "Address": {"City": "Monterrey"},
        }
    )

    payload = payload_from(request, "http://schemas.xmlsoap.org/soap/envelope/")
    assert payload.tag == "{urn:customer}UpdateCustomerRequest"
    assert payload.find("CustomerId").text == "7"
    assert payload.find("Name") is None
    assert payload.find("Active").text == "true"
    assert [item.text for item in payload.findall("Tag")] == ["vip", "beta"]
    assert payload.find("Address/City").text == "Monterrey"
    assert payload.find("Address/Zip") is None


def test_schema_backed_body_requires_required_fields(tmp_path):
    giso = Giso().soap(write_wsdl(tmp_path))

    with pytest.raises(ValueError, match="CustomerId"):
        giso.customer_service.update_customer(body={"Active": True})


def test_schema_backed_body_rejects_unknown_fields(tmp_path):
    giso = Giso().soap(write_wsdl(tmp_path))

    with pytest.raises(ValueError, match="Unknown SOAP body fields"):
        giso.customer_service.update_customer(
            body={"CustomerId": 7, "Active": True, "Mystery": "x"}
        )


def test_repeated_field_requires_sequence(tmp_path):
    giso = Giso().soap(write_wsdl(tmp_path))

    with pytest.raises(TypeError, match="Repeated SOAP body field"):
        giso.customer_service.update_customer(
            body={"CustomerId": 7, "Active": True, "Tag": "vip"}
        )


def test_soap12_uses_soap12_envelope_and_action_content_type(tmp_path):
    giso = Giso().soap(write_wsdl(tmp_path, SOAP12_WSDL))

    request = giso.customer_service.update_customer(
        body={"CustomerId": 7, "Active": False}
    )

    assert request.soap_version == "1.2"
    assert request.headers["Content-Type"] == (
        'application/soap+xml; charset=utf-8; action="urn:UpdateCustomer"'
    )
    assert "SOAPAction" not in request.headers
    payload = payload_from(request, "http://www.w3.org/2003/05/soap-envelope")
    assert payload.tag == "{urn:customer}UpdateCustomerRequest"


def test_explicit_headers_are_preserved_without_overwriting_transport_defaults(tmp_path):
    giso = Giso().soap(
        write_wsdl(tmp_path),
        headers={"Authorization": "Bearer secret", "Content-Type": "custom/type"},
    )

    request = giso.customer_service.update_customer(
        body={"CustomerId": 7, "Active": True}
    )

    assert request.headers["Authorization"] == "Bearer secret"
    assert request.headers["Content-Type"] == "custom/type"
    assert "secret" not in repr(giso.provenance)
