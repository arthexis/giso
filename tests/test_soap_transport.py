from __future__ import annotations

import io
import pathlib
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


def write_wsdl(tmp_path: pathlib.Path, content: str = WSDL) -> pathlib.Path:
    path = tmp_path / "service.wsdl"
    path.write_text(content, encoding="utf-8")
    return path


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_operation_posts_serialized_request_and_decodes_response(tmp_path, monkeypatch):
    seen = {}

    def urlopen(request, timeout=30):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["data"] = request.data
        seen["content_type"] = request.get_header("Content-type")
        seen["soap_action"] = request.get_header("Soapaction")
        return Response(SOAP11_RESPONSE)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    giso = Giso().soap(write_wsdl(tmp_path))

    result = giso.customer_service.get_customer(body={"CustomerId": 7})

    assert seen["url"] == "https://api.example.com/soap"
    assert seen["method"] == "POST"
    assert seen["data"].startswith(b"<?xml")
    assert seen["content_type"] == "text/xml; charset=utf-8"
    assert seen["soap_action"] == '"urn:GetCustomer"'
    assert result == {
        "Name": "Ada",
        "Active": True,
        "Tag": ["vip", "beta"],
    }
    assert giso.results.last == result


def test_soap11_fault_from_successful_http_response_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=30: Response(SOAP11_FAULT))
    giso = Giso().soap(write_wsdl(tmp_path))

    with pytest.raises(SoapFault) as exc_info:
        giso.customer_service.get_customer(body={})

    fault = exc_info.value
    assert fault.code == "soap:Client"
    assert fault.reason == "Customer not found"
    assert fault.detail == {"ErrorCode": "4041"}
    assert fault.soap_version == "1.1"


def test_soap_fault_body_is_parsed_even_when_http_status_is_error(tmp_path, monkeypatch):
    def urlopen(request, timeout=30):
        raise urllib.error.HTTPError(
            request.full_url,
            500,
            "Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(SOAP11_FAULT),
        )

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    giso = Giso().soap(write_wsdl(tmp_path))

    with pytest.raises(SoapFault, match="Customer not found"):
        giso.customer_service.get_customer(body={})


def test_soap12_fault_is_decoded(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=30: Response(SOAP12_FAULT))
    giso = Giso().soap(write_wsdl(tmp_path, SOAP12_WSDL))

    with pytest.raises(SoapFault) as exc_info:
        giso.customer_service.get_customer(body={})

    assert exc_info.value.code == "soap:Sender"
    assert exc_info.value.reason == "Bad request"
    assert exc_info.value.detail == {"ErrorCode": "12"}
    assert exc_info.value.soap_version == "1.2"


def test_non_soap_http_error_is_reported_cleanly(tmp_path, monkeypatch):
    def urlopen(request, timeout=30):
        raise urllib.error.HTTPError(
            request.full_url,
            502,
            "Bad Gateway",
            hdrs=None,
            fp=io.BytesIO(b"not xml"),
        )

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    giso = Giso().soap(write_wsdl(tmp_path))

    with pytest.raises(ValueError, match=r"SOAP request failed \(502\)"):
        giso.customer_service.get_customer(body={})


def test_response_rejects_unsafe_xml(tmp_path, monkeypatch):
    payload = b'<!DOCTYPE x [<!ENTITY boom "bad">]><x>&boom;</x>'
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=30: Response(payload))
    giso = Giso().soap(write_wsdl(tmp_path))

    with pytest.raises(ValueError, match="disallowed XML declarations"):
        giso.customer_service.get_customer(body={})
