from __future__ import annotations

import io
import urllib.error

import pytest

from giso import Giso, SoapFault


WSDL = """<?xml version="1.0"?>
<wsdl:definitions
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:tns="urn:test"
    targetNamespace="urn:test">
  <wsdl:binding name="CustomerBinding" type="tns:CustomerPortType">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="document" />
    <wsdl:operation name="GetCustomer">
      <soap:operation soapAction="urn:GetCustomer" />
    </wsdl:operation>
  </wsdl:binding>
  <wsdl:service name="CustomerService">
    <wsdl:port name="CustomerPort" binding="tns:CustomerBinding">
      <soap:address location="https://api.example.com/soap" />
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

SOAP11_RESPONSE = b"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetCustomerResponse xmlns="urn:test">
      <Name>Ada</Name>
      <Active>true</Active>
      <Tag>vip</Tag>
      <Tag>beta</Tag>
    </GetCustomerResponse>
  </soap:Body>
</soap:Envelope>
"""

SOAP11_FAULT = b"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <soap:Fault>
      <faultcode>soap:Client</faultcode>
      <faultstring>Customer not found</faultstring>
      <detail><ErrorCode>4041</ErrorCode></detail>
    </soap:Fault>
  </soap:Body>
</soap:Envelope>
"""

SOAP12_FAULT = b"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
  <soap:Body>
    <soap:Fault>
      <soap:Code><soap:Value>soap:Sender</soap:Value></soap:Code>
      <soap:Reason><soap:Text xml:lang="en">Bad request</soap:Text></soap:Reason>
      <soap:Detail><ErrorCode>12</ErrorCode></soap:Detail>
    </soap:Fault>
  </soap:Body>
</soap:Envelope>
"""


def test_operation_posts_serialized_request_and_decodes_response(wsdl_file, bytes_response, monkeypatch):
    seen = {}

    def urlopen(request, timeout=30):
        seen.update(
            url=request.full_url,
            method=request.get_method(),
            data=request.data,
            content_type=request.get_header("Content-type"),
            soap_action=request.get_header("Soapaction"),
        )
        return bytes_response(SOAP11_RESPONSE)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    giso = Giso().soap(wsdl_file(WSDL))

    result = giso.customer_service.get_customer(body={"CustomerId": 7})

    assert seen == {
        "url": "https://api.example.com/soap",
        "method": "POST",
        "data": seen["data"],
        "content_type": "text/xml; charset=utf-8",
        "soap_action": '"urn:GetCustomer"',
    }
    assert seen["data"].startswith(b"<?xml")
    assert result == {"Name": "Ada", "Active": True, "Tag": ["vip", "beta"]}
    assert giso.results.last == result


def test_soap11_fault_from_successful_http_response_raises(wsdl_file, bytes_response, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=30: bytes_response(SOAP11_FAULT),
    )
    giso = Giso().soap(wsdl_file(WSDL))

    with pytest.raises(SoapFault) as exc_info:
        giso.customer_service.get_customer(body={})

    fault = exc_info.value
    assert (fault.code, fault.reason, fault.detail, fault.soap_version) == (
        "soap:Client",
        "Customer not found",
        {"ErrorCode": "4041"},
        "1.1",
    )


def test_soap_fault_body_is_parsed_even_when_http_status_is_error(wsdl_file, monkeypatch):
    def urlopen(request, timeout=30):
        raise urllib.error.HTTPError(
            request.full_url,
            500,
            "Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(SOAP11_FAULT),
        )

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    giso = Giso().soap(wsdl_file(WSDL))

    with pytest.raises(SoapFault, match="Customer not found"):
        giso.customer_service.get_customer(body={})


def test_soap12_fault_is_decoded(wsdl_file, bytes_response, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=30: bytes_response(SOAP12_FAULT),
    )
    giso = Giso().soap(wsdl_file(SOAP12_WSDL))

    with pytest.raises(SoapFault) as exc_info:
        giso.customer_service.get_customer(body={})

    fault = exc_info.value
    assert (fault.code, fault.reason, fault.detail, fault.soap_version) == (
        "soap:Sender",
        "Bad request",
        {"ErrorCode": "12"},
        "1.2",
    )


def test_non_soap_http_error_is_reported_cleanly(wsdl_file, monkeypatch):
    def urlopen(request, timeout=30):
        raise urllib.error.HTTPError(
            request.full_url,
            502,
            "Bad Gateway",
            hdrs=None,
            fp=io.BytesIO(b"not xml"),
        )

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    giso = Giso().soap(wsdl_file(WSDL))

    with pytest.raises(ValueError, match=r"SOAP request failed \(502\)"):
        giso.customer_service.get_customer(body={})


def test_response_rejects_unsafe_xml(wsdl_file, bytes_response, monkeypatch):
    payload = b'<!DOCTYPE x [<!ENTITY boom "bad">]><x>&boom;</x>'
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=30: bytes_response(payload),
    )
    giso = Giso().soap(wsdl_file(WSDL))

    with pytest.raises(ValueError, match="disallowed XML declarations"):
        giso.customer_service.get_customer(body={})
