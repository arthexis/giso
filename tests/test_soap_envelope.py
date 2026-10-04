from __future__ import annotations

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


def test_schema_backed_body_serializes_nested_optional_and_repeated_values(wsdl_file, soap_payload):
    giso = Giso().soap(wsdl_file(WSDL, "customer.wsdl"))

    request = giso.customer_service.update_customer.prepare(
        body={
            "CustomerId": 7,
            "Active": True,
            "Tag": ["vip", "beta"],
            "Address": {"City": "Monterrey"},
        }
    )

    payload = soap_payload(request, "http://schemas.xmlsoap.org/soap/envelope/")
    assert payload.tag == "{urn:customer}UpdateCustomerRequest"
    assert payload.find("CustomerId").text == "7"
    assert payload.find("Name") is None
    assert payload.find("Active").text == "true"
    assert [item.text for item in payload.findall("Tag")] == ["vip", "beta"]
    assert payload.find("Address/City").text == "Monterrey"
    assert payload.find("Address/Zip") is None


def test_schema_backed_body_requires_required_fields(wsdl_file):
    giso = Giso().soap(wsdl_file(WSDL, "customer.wsdl"))

    with pytest.raises(ValueError, match="CustomerId"):
        giso.customer_service.update_customer.prepare(body={"Active": True})


def test_schema_backed_body_rejects_unknown_fields(wsdl_file):
    giso = Giso().soap(wsdl_file(WSDL, "customer.wsdl"))

    with pytest.raises(ValueError, match="Unknown SOAP body fields"):
        giso.customer_service.update_customer.prepare(
            body={"CustomerId": 7, "Active": True, "Mystery": "x"}
        )


def test_repeated_field_requires_sequence(wsdl_file):
    giso = Giso().soap(wsdl_file(WSDL, "customer.wsdl"))

    with pytest.raises(TypeError, match="Repeated SOAP body field"):
        giso.customer_service.update_customer.prepare(
            body={"CustomerId": 7, "Active": True, "Tag": "vip"}
        )


def test_soap12_uses_soap12_envelope_and_action_content_type(wsdl_file, soap_payload):
    giso = Giso().soap(wsdl_file(SOAP12_WSDL, "customer.wsdl"))

    request = giso.customer_service.update_customer.prepare(
        body={"CustomerId": 7, "Active": False}
    )

    assert request.soap_version == "1.2"
    assert request.headers["Content-Type"] == (
        'application/soap+xml; charset=utf-8; action="urn:UpdateCustomer"'
    )
    assert "SOAPAction" not in request.headers
    payload = soap_payload(request, "http://www.w3.org/2003/05/soap-envelope")
    assert payload.tag == "{urn:customer}UpdateCustomerRequest"


def test_explicit_headers_are_preserved_without_overwriting_transport_defaults(wsdl_file):
    giso = Giso().soap(
        wsdl_file(WSDL, "customer.wsdl"),
        headers={"Authorization": "Bearer secret", "Content-Type": "custom/type"},
    )

    request = giso.customer_service.update_customer.prepare(
        body={"CustomerId": 7, "Active": True}
    )

    assert request.headers["Authorization"] == "Bearer secret"
    assert request.headers["Content-Type"] == "custom/type"
    assert "secret" not in repr(giso.provenance)
