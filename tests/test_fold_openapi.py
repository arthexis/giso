from __future__ import annotations

import io
import json
import pathlib

import pytest

from giso import Giso


def document(server: str = "https://api.example.com/v1") -> dict:
    return {
        "openapi": "3.1.0",
        "servers": [{"url": server}],
        "paths": {},
    }


def test_openapi_method_loads_local_json_and_records_provenance(tmp_path):
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(document()), encoding="utf-8")

    giso = Giso()
    result = giso.openapi(path)

    assert result is giso
    assert giso.provenance[-1] == {
        "type": "openapi",
        "source": str(path),
        "version": "3.1.0",
        "base_url": "https://api.example.com/v1",
    }
    assert giso._openapi_sources[-1]["document"]["paths"] == {}


def test_openapi_string_source_uses_explicit_prefix(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(document()), encoding="utf-8")

    giso = Giso(f"openapi:{path}")

    assert giso.provenance[-1]["type"] == "openapi"
    assert giso.provenance[-1]["source"] == str(path)


def test_openapi_method_can_override_base_url(tmp_path):
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(document("https://production.example.com")), encoding="utf-8")

    giso = Giso().openapi(path, base_url="http://localhost:8000/")

    assert giso.provenance[-1]["base_url"] == "http://localhost:8000"
    assert giso._openapi_sources[-1]["base_url"] == "http://localhost:8000"


def test_openapi_headers_are_retained_privately_not_in_provenance(tmp_path):
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(document()), encoding="utf-8")

    giso = Giso().openapi(path, headers={"Authorization": "Bearer secret"})

    assert giso._openapi_sources[-1]["headers"] == {"Authorization": "Bearer secret"}
    assert "headers" not in giso.provenance[-1]
    assert "secret" not in repr(giso.provenance)


def test_https_openapi_source_is_loaded(monkeypatch):
    payload = json.dumps(document()).encode()

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=30: Response(payload))

    giso = Giso().openapi("https://example.com/openapi.json")

    assert giso.provenance[-1]["source"] == "https://example.com/openapi.json"


def test_remote_openapi_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        Giso().openapi("http://example.com/openapi.json")


def test_openapi_requires_version_3(tmp_path):
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps({"swagger": "2.0", "paths": {}, "servers": [{"url": "https://api.example.com"}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="OpenAPI 3.x"):
        Giso().openapi(path)


def test_openapi_requires_paths_object(tmp_path):
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps({"openapi": "3.0.3", "servers": [{"url": "https://api.example.com"}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="paths object"):
        Giso().openapi(path)


def test_openapi_requires_server_or_explicit_base_url(tmp_path):
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps({"openapi": "3.0.3", "paths": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="pass base_url explicitly"):
        Giso().openapi(path)

    giso = Giso().openapi(path, base_url="https://override.example.com")
    assert giso.provenance[-1]["base_url"] == "https://override.example.com"


def test_openapi_sources_survive_giso_folding(tmp_path):
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    source = Giso().openapi(path, headers={"X-Test": "yes"})

    folded = Giso(source)

    assert folded.provenance == source.provenance
    assert folded._openapi_sources[0]["headers"] == {"X-Test": "yes"}
