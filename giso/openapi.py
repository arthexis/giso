from __future__ import annotations

import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from .pypi import Giso as PyPIGiso


class Giso(PyPIGiso):
    """A Giso that can also load explicit OpenAPI 3.x descriptions."""

    def __init__(self, *sources: Any, name: str = "giso"):
        self._openapi_sources: list[dict[str, Any]] = []
        super().__init__(*sources, name=name)

    def _fold_string(self, source: str) -> None:
        if source.startswith("openapi:"):
            self._fold_openapi(source[len("openapi:") :])
            return
        super()._fold_string(source)

    def _fold_giso(self, source: Any) -> None:
        super()._fold_giso(source)
        for entry in getattr(source, "_openapi_sources", ()):
            if entry not in self._openapi_sources:
                copied = dict(entry)
                copied["document"] = dict(entry["document"])
                copied["headers"] = dict(entry["headers"])
                self._openapi_sources.append(copied)

    def openapi(
        self,
        source: str | pathlib.Path,
        *,
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> "Giso":
        """Load an OpenAPI description and retain it for capability generation."""
        self._fold_openapi(source, base_url=base_url, headers=headers)
        return self

    def _fold_openapi(
        self,
        source: str | pathlib.Path,
        *,
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        source_text = str(source)
        if not source_text.strip():
            raise ValueError("OpenAPI source must not be empty")

        document = self._load_openapi_document(source)
        version = self._validate_openapi_document(document)
        resolved_base_url = self._resolve_openapi_base_url(document, base_url)
        request_headers = self._validate_openapi_headers(headers)

        self._openapi_sources.append(
            {
                "source": source_text,
                "document": document,
                "version": version,
                "base_url": resolved_base_url,
                "headers": request_headers,
            }
        )
        self.provenance.append(
            {
                "type": "openapi",
                "source": source_text,
                "version": version,
                "base_url": resolved_base_url,
            }
        )

    def _load_openapi_document(self, source: str | pathlib.Path) -> dict[str, Any]:
        if isinstance(source, pathlib.Path):
            return self._load_openapi_file(source)

        parsed = urllib.parse.urlparse(source)
        if parsed.scheme:
            if parsed.scheme != "https":
                raise ValueError("Remote OpenAPI sources must use HTTPS")
            return self._load_openapi_url(source)
        return self._load_openapi_file(pathlib.Path(source).expanduser())

    @staticmethod
    def _load_openapi_file(path: pathlib.Path) -> dict[str, Any]:
        path = path.expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"OpenAPI file does not exist: {path}")
        if path.suffix.lower() != ".json":
            raise ValueError("OpenAPI source must be a JSON document")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot load OpenAPI JSON document: {path}") from exc
        if not isinstance(data, dict):
            raise ValueError("OpenAPI document root must be an object")
        return data

    @staticmethod
    def _load_openapi_url(url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "giso",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"OpenAPI request failed ({exc.code}): {url}") from exc
        except (urllib.error.URLError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot load OpenAPI JSON document: {url}") from exc
        if not isinstance(data, dict):
            raise ValueError("OpenAPI document root must be an object")
        return data

    @staticmethod
    def _validate_openapi_document(document: dict[str, Any]) -> str:
        version = document.get("openapi")
        if not isinstance(version, str) or not version.startswith("3."):
            raise ValueError("Only OpenAPI 3.x documents are supported")
        paths = document.get("paths")
        if not isinstance(paths, dict):
            raise ValueError("OpenAPI document must contain a paths object")
        return version

    @classmethod
    def _resolve_openapi_base_url(
        cls,
        document: dict[str, Any],
        override: str | None,
    ) -> str:
        if override is not None:
            return cls._validate_openapi_base_url(override)

        servers = document.get("servers")
        if not isinstance(servers, list) or not servers:
            raise ValueError("OpenAPI document has no server URL; pass base_url explicitly")
        first = servers[0]
        if not isinstance(first, dict) or not isinstance(first.get("url"), str):
            raise ValueError("OpenAPI document has no valid server URL; pass base_url explicitly")
        return cls._validate_openapi_base_url(first["url"])

    @staticmethod
    def _validate_openapi_base_url(value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OpenAPI base URL must be an absolute HTTP(S) URL")
        return value

    @staticmethod
    def _validate_openapi_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
        if headers is None:
            return {}
        result: dict[str, str] = {}
        for name, value in headers.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("OpenAPI header names must be non-empty strings")
            if not isinstance(value, str):
                raise ValueError("OpenAPI header values must be strings")
            result[name] = value
        return result
