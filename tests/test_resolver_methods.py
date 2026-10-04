from giso import Giso


def test_github_method_builds_resolver_source_and_returns_self(monkeypatch):
    seen = []
    monkeypatch.setattr(Giso, "_fold_github", lambda self, source: seen.append(source))

    giso = Giso()
    returned = giso.github("arthexis/example", ref="main", subdirectory="src/plugin")

    assert returned is giso
    assert seen == ["github:arthexis/example@main#src/plugin"]


def test_github_method_supports_repository_only(monkeypatch):
    seen = []
    monkeypatch.setattr(Giso, "_fold_github", lambda self, source: seen.append(source))

    Giso().github("arthexis/example")

    assert seen == ["github:arthexis/example"]


def test_pypi_method_builds_resolver_source_and_returns_self(monkeypatch):
    seen = []
    monkeypatch.setattr(Giso, "_fold_pypi", lambda self, source: seen.append(source))

    giso = Giso()
    returned = giso.pypi("requests", version="2.32.5")

    assert returned is giso
    assert seen == ["pypi:requests@2.32.5"]


def test_pypi_method_supports_latest_release(monkeypatch):
    seen = []
    monkeypatch.setattr(Giso, "_fold_pypi", lambda self, source: seen.append(source))

    Giso().pypi("requests")

    assert seen == ["pypi:requests"]
