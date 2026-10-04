from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .github import Giso as GitHubGiso


class Giso(GitHubGiso):
    """A Giso that can also resolve explicit public PyPI package sources."""

    def pypi(self, project: str, *, version: str | None = None) -> "Giso":
        """Fold a public PyPI project without requiring a ``pypi:`` source prefix."""
        source = f"pypi:{project}"
        if version is not None:
            source += f"@{version}"
        self._fold_pypi(source)
        return self

    def _fold_string(self, source: str) -> None:
        if source.startswith("pypi:"):
            self._fold_pypi(source)
            return
        super()._fold_string(source)

    def _fold_pypi(self, source: str) -> None:
        project, requested_version = self._parse_pypi_source(source)
        metadata = self._pypi_metadata(project, requested_version)
        info = metadata.get("info")
        if not isinstance(info, dict):
            raise ValueError(f"PyPI returned invalid metadata for {project}")
        version = info.get("version")
        if not isinstance(version, str) or not version:
            raise ValueError(f"PyPI returned no resolved version for {project}")

        urls = metadata.get("urls")
        if not isinstance(urls, list):
            raise ValueError(f"PyPI returned no release files for {project}@{version}")
        artifact = self._select_pypi_artifact(project, version, urls)
        path = self._pypi_artifact(project, version, artifact)
        self._fold_archive(path)

        digests = artifact.get("digests") if isinstance(artifact.get("digests"), dict) else {}
        self.provenance.append(
            {
                "type": "pypi",
                "source": f"pypi:{project}",
                "requested_version": requested_version or "",
                "version": version,
                "filename": str(artifact["filename"]),
                "sha256": str(digests.get("sha256", "")),
                "url": str(artifact["url"]),
            }
        )

    @staticmethod
    def _parse_pypi_source(source: str) -> tuple[str, str | None]:
        body = source[len("pypi:") :].strip()
        project, marker, version = body.partition("@")
        if not project or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", project):
            raise ValueError("PyPI sources must use pypi:project or pypi:project@version")
        if marker and not version:
            raise ValueError(f"Invalid PyPI version: {source}")
        if not marker:
            version = None
        return project, version

    @staticmethod
    def _pypi_json(url: str) -> dict[str, Any]:
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
            if exc.code == 404:
                raise ValueError(f"PyPI package or release not found: {url}") from exc
            raise ValueError(f"PyPI request failed ({exc.code}): {url}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"PyPI request failed: {url}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"PyPI returned invalid metadata: {url}")
        return data

    def _pypi_metadata(self, project: str, version: str | None) -> dict[str, Any]:
        encoded_project = urllib.parse.quote(project, safe="")
        if version is None:
            return self._pypi_json(f"https://pypi.org/pypi/{encoded_project}/json")
        encoded_version = urllib.parse.quote(version, safe="")
        return self._pypi_json(f"https://pypi.org/pypi/{encoded_project}/{encoded_version}/json")

    @classmethod
    def _select_pypi_artifact(
        cls,
        project: str,
        version: str,
        urls: list[Any],
    ) -> dict[str, Any]:
        files = [item for item in urls if isinstance(item, dict) and isinstance(item.get("filename"), str)]
        wheels = [item for item in files if item.get("packagetype") == "bdist_wheel"]
        universal = sorted(
            (item for item in wheels if cls._is_universal_py3_wheel(str(item["filename"]))),
            key=lambda item: str(item["filename"]),
        )
        if universal:
            artifact = universal[0]
        else:
            sdists = sorted(
                (item for item in files if item.get("packagetype") == "sdist"),
                key=lambda item: str(item["filename"]),
            )
            if not sdists:
                raise ValueError(
                    f"PyPI release has no supported universal wheel or source distribution: {project}@{version}"
                )
            artifact = sdists[0]

        url = artifact.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError(f"PyPI release file has an invalid URL: {project}@{version}")
        filename = str(artifact["filename"])
        lower = filename.lower()
        if not lower.endswith((".whl", ".zip", ".tar.gz", ".tgz")):
            raise ValueError(f"PyPI release file has an unsupported archive type: {filename}")
        return artifact

    @staticmethod
    def _is_universal_py3_wheel(filename: str) -> bool:
        return filename.lower().endswith("-py3-none-any.whl")

    def _pypi_artifact(self, project: str, version: str, artifact: dict[str, Any]) -> pathlib.Path:
        filename = str(artifact["filename"])
        url = str(artifact["url"])
        digests = artifact.get("digests") if isinstance(artifact.get("digests"), dict) else {}
        expected_sha256 = digests.get("sha256")
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            raise ValueError(f"PyPI release file has no valid SHA256 digest: {project}@{version}")

        cache_dir = self._pypi_cache_root() / self._normalize_project(project) / version
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / filename
        if path.is_file() and self._sha256(path) == expected_sha256.lower():
            return path
        path.unlink(missing_ok=True)

        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        request = urllib.request.Request(url, headers={"User-Agent": "giso"})
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected_sha256.lower():
                raise ValueError(f"PyPI release file failed SHA256 verification: {project}@{version}")
            temporary.replace(path)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            temporary.unlink(missing_ok=True)
            raise ValueError(f"Cannot download PyPI release file: {project}@{version}")
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return path

    @staticmethod
    def _normalize_project(project: str) -> str:
        return re.sub(r"[-_.]+", "-", project).lower()

    @staticmethod
    def _sha256(path: pathlib.Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _pypi_cache_root() -> pathlib.Path:
        configured = os.environ.get("GISO_CACHE_DIR")
        if configured:
            return pathlib.Path(configured).expanduser() / "pypi"
        xdg = os.environ.get("XDG_CACHE_HOME")
        if xdg:
            return pathlib.Path(xdg).expanduser() / "giso" / "pypi"
        return pathlib.Path.home() / ".cache" / "giso" / "pypi"
