from __future__ import annotations

import pathlib

from giso import Giso, SoapRequest


WSDL = """<?xml version="1.0"?>
<wsdl:definitions
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:soap12="http://schemas.xmlsoap.org/wsdl/soap12/"
    xmlns:tns="urn:test"
    targetNamespace="urn:test">
  <wsdl:binding name="CustomerBinding" type="tns:CustomerPortType">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" style="document" />
    <wsdl:operation name="GetCustomer">
      <soap:operation soapAction="urn:GetCustomer" />
    </wsdl:operation>
    <wsdl:operation name="UpdateCustomer">
      <soap:operation soapAction="urn:UpdateCustomer" />
    </wsdl:operation>
  </wsdl:binding>
  <wsdl:service name="CustomerService">
    <wsdl:port name="CustomerPort" binding="tns:CustomerBinding">
      <soap:address location="https://api.example.com/soap" />
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>
"""

COLLISION_WSDL = """<?xml version="1.0"?>
<wsdl:definitions
    xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:soap12="http://schemas.xmlsoap.org/wsdl/soap12/"
    xmlns:tns="urn:test"
    targetNamespace="urn:test">
  <wsdl:binding name="Soap11Binding" type="tns:CustomerPortType">
    <soap:binding transport="http://schemas.xmlsoap.org/soap/http" />
    <wsdl:operation name="GetCustomer"><soap:operation soapAction="urn:v11" /></wsdl:operation>
  </wsdl:binding>
  <wsdl:binding name="Soap12Binding" type="tns:CustomerPortType">
    <soap12:binding transport="http://www.w3.org/2003/05/soap/bindings/HTTP/" />
    <wsdl:operation name="GetCustomer"><soap12:operation soapAction="urn:v12" /></wsdl:operation>
  </wsdl:binding>
  <wsdl:service name="CustomerService">
    <wsdl:port name="LegacyPort" binding="tns:Soap11Binding">
      <soap:address location="https://api.example.com/soap11" />
    </wsdl:port>
    <wsdl:port name="ModernPort" binding="tns:Soap12Binding">
      <soap12:address location="https://api.example.com/soap12" />
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>
"""


def write_wsdl(tmp_path: pathlib.Path, content: str = WSDL) -> pathlib.Path:
    path = tmp_path / "service.wsdl"
    path.write_text(content, encoding="utf-8")
    return path


def test_soap_fold_materializes_service_operations(tmp_path):
    giso = Giso().soap(write_wsdl(tmp_path))

    assert "customer_service.get_customer" in giso.operations
    assert "customer_service.update_customer" in giso.operations
    assert callable(giso.customer_service.get_customer)


def test_generated_operation_exposes_soap_metadata(tmp_path):
    giso = Giso().soap(write_wsdl(tmp_path))

    metadata = giso.customer_service.get_customer.__giso_soap__
    assert metadata == {
        "service": "CustomerService",
        "port": "CustomerPort",
        "binding": "CustomerBinding",
        "operation": "GetCustomer",
        "soap_action": "urn:GetCustomer",
        "soap_version": "1.1",
        "endpoint": "https://api.example.com/soap",
        "wsdl_source": str(tmp_path / "service.wsdl"),
    }


def test_endpoint_override_is_reflected_in_operation_metadata(tmp_path):
    giso = Giso().soap(
        write_wsdl(tmp_path),
        endpoint="http://localhost:9000/service",
    )

    assert giso.customer_service.get_customer.__giso_soap__["endpoint"] == "http://localhost:9000/service"


def test_generated_operations_expose_prepare_without_network(tmp_path):
    giso = Giso().soap(write_wsdl(tmp_path))

    request = giso.customer_service.get_customer.prepare(body={"CustomerId": 7})

    assert isinstance(request, SoapRequest)
    assert request.endpoint == "https://api.example.com/soap"
    assert giso.results.history == ()


def test_port_namespace_breaks_same_service_operation_collisions(tmp_path):
    giso = Giso().soap(write_wsdl(tmp_path, COLLISION_WSDL))

    assert "customer_service.get_customer" in giso.operations
    assert "customer_service.modern_port.get_customer" in giso.operations
    assert giso.customer_service.get_customer.__giso_soap__["soap_version"] == "1.1"
    assert giso.customer_service.modern_port.get_customer.__giso_soap__["soap_version"] == "1.2"


def test_soap_operation_surface_survives_giso_folding(tmp_path):
    source = Giso().soap(write_wsdl(tmp_path))

    folded = Giso(source)

    assert "customer_service.get_customer" in folded.operations
    assert folded.customer_service.get_customer.__giso_soap__ == source.customer_service.get_customer.__giso_soap__
