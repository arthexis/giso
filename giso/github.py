from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .core import Giso as CoreGiso


class Giso(CoreGiso):
    """A Giso that can also resolve explicit public GitHub repository sources."""

    def __init__(self, *sources: Any, name: str = "giso"):
        self.provenance: list[dict[str, str]] = []
        super().__init__(*sources, name=name)

    def _fold_string(self, source: str) -> None:
        if source.startswith("github:"):
            self._fold_github(source)
            return
        super()._fold_string(source)

    def _fold_giso(self, source: CoreGiso) -> None:
        super()._fold_giso(source)
        for entry in getattr(source, "provenance", ()):
            if entry not in self.provenance:
                self.provenance.append(dict(entry))

    def _fold_github(self, source: str) -> None:
        owner, repo, requested_ref, subdirectory = self._parse_github_source(source)
        resolved_ref = requested_ref or self._github_default_branch(owner, repo)
        commit = self._github_resolve_commit(owner, repo, resolved_ref)
        archive = self._github_archive(owner, repo, commit)

        if subdirectory is None:
            self._fold_archive(archive)
        else:
            self._fold_github_archive_subdirectory(archive, subdirectory)

        self.provenance.append(
            {
                "type": "github",
                "source": f"github:{owner}/{repo}",
                "requested_ref": requested_ref or "",
                "resolved_ref": resolved_ref,
                "commit": commit,
                "subdirectory": subdirectory or "",
            }
        )

    @staticmethod
    def _parse_github_source(source: str) -> tuple[str, str, str | None, str | None]:
        body = source[len("github:") :]
        repository_part, separator, subdirectory = body.partition("#")
        if separator:
            subdirectory = subdirectory.strip("/")
            if not subdirectory or ".." in pathlib.PurePosixPath(subdirectory).parts:
                raise ValueError(f"Invalid GitHub subdirectory: {source}")
        else:
            subdirectory = None

        repository, marker, requested_ref = repository_part.partition("@")
        if marker and not requested_ref:
            raise ValueError(f"Invalid GitHub ref: {source}")
        if not marker:
            requested_ref = None

        parts = repository.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("GitHub sources must use github:owner/repository")
        owner, repo = parts
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not repo:
            raise ValueError("GitHub sources must name a repository")
        return owner, repo, requested_ref, subdirectory

    @staticmethod
    def _github_api_json(path: str) -> dict[str, Any]:
        url = f"https://api.github.com{path}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "giso",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ValueError(f"GitHub resource not found: {path}") from exc
            raise ValueError(f"GitHub request failed ({exc.code}): {path}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"GitHub request failed: {path}") from exc

    def _github_default_branch(self, owner: str, repo: str) -> str:
        data = self._github_api_json(f"/repos/{owner}/{repo}")
        branch = data.get("default_branch")
        if not isinstance(branch, str) or not branch:
            raise ValueError(f"GitHub repository has no default branch: {owner}/{repo}")
        return branch

    def _github_resolve_commit(self, owner: str, repo: str, ref: str) -> str:
        encoded_ref = urllib.parse.quote(ref, safe="")
        data = self._github_api_json(f"/repos/{owner}/{repo}/commits/{encoded_ref}")
        sha = data.get("sha")
        if not isinstance(sha, str) or not sha:
            raise ValueError(f"GitHub ref did not resolve to a commit: {owner}/{repo}@{ref}")
        return sha

    def _github_archive(self, owner: str, repo: str, commit: str) -> pathlib.Path:
        cache_dir = self._github_cache_root() / owner / repo
        cache_dir.mkdir(parents=True, exist_ok=True)
        archive = cache_dir / f"{commit}.tar.gz"
        if archive.is_file():
            return archive

        url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{commit}"
        request = urllib.request.Request(url, headers={"User-Agent": "giso"})
        temporary = archive.with_name(f".{archive.name}.{os.getpid()}.tmp")
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(archive)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            temporary.unlink(missing_ok=True)
            raise ValueError(f"Cannot download GitHub repository archive: {owner}/{repo}@{commit}") from exc
        return archive

    @staticmethod
    def _github_cache_root() -> pathlib.Path:
        configured = os.environ.get("GISO_CACHE_DIR")
        if configured:
            return pathlib.Path(configured).expanduser() / "github"
        xdg = os.environ.get("XDG_CACHE_HOME")
        if xdg:
            return pathlib.Path(xdg).expanduser() / "giso" / "github"
        return pathlib.Path.home() / ".cache" / "giso" / "github"

    def _fold_github_archive_subdirectory(self, archive: pathlib.Path, subdirectory: str) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="giso-github-")
        root = pathlib.Path(temporary.name)
        try:
            self._extract_tar_archive(archive, root)
            children = list(root.iterdir())
            if len(children) != 1 or not children[0].is_dir():
                raise ValueError("GitHub archive has an unexpected root layout")
            repository_root = children[0]
            selected = repository_root.joinpath(*pathlib.PurePosixPath(subdirectory).parts).resolve()
            if selected != repository_root and repository_root.resolve() not in selected.parents:
                raise ValueError(f"Invalid GitHub subdirectory: {subdirectory}")
            if not selected.exists():
                raise ValueError(f"GitHub subdirectory does not exist: {subdirectory}")
            self._fold_path(selected)
        except Exception:
            temporary.cleanup()
            raise
        self._archive_roots.append(temporary)
