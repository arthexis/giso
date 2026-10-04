from __future__ import annotations

import io
import xml.etree.ElementTree as ET

import pytest


@pytest.fixture
def wsdl_file(tmp_path):
    """Return a small writer for scenario-specific WSDL fixtures."""

    def write(content: str, name: str = "service.wsdl"):
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return write


@pytest.fixture
def bytes_response():
    """Wrap bytes in the context-manager interface returned by urlopen()."""

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    return Response


@pytest.fixture
def soap_payload():
    """Extract the first payload element from a serialized SOAP request."""

    def extract(request, envelope_namespace: str):
        root = ET.fromstring(request.body)
        body = root.find(f"{{{envelope_namespace}}}Body")
        assert body is not None
        children = list(body)
        assert children
        return children[0]

    return extract
