from collections import deque

import pytest

from giso import CapabilityRequest, Giso


def alpha() -> str:
    return "alpha"


def beta() -> str:
    return "beta"


def math__double(value: int) -> int:
    return value * 2


def math__triple(value: int) -> int:
    return value * 3


def status() -> str:
    return "ready"


def test_sized_iterable_is_folded_eagerly():
    giso = Giso(deque([alpha, beta]))

    assert giso.alpha() == "alpha"
    assert giso.beta() == "beta"


def test_generator_is_not_consumed_until_lookup_misses():
    events: list[str] = []

    def ingredients():
        events.append("first")
        yield alpha
        events.append("second")
        yield beta

    giso = Giso(ingredients())

    assert events == []
    assert giso.alpha() == "alpha"
    assert events == ["first"]
    assert giso.beta() == "beta"
    assert events == ["first", "second"]


def test_each_unresolved_lookup_advances_plain_generator_once():
    def ingredients():
        yield alpha
        yield beta

    giso = Giso(ingredients())

    with pytest.raises(AttributeError):
        _ = giso.missing
    assert "alpha" in giso.operations
    assert "beta" not in giso.operations

    with pytest.raises(AttributeError):
        _ = giso.missing
    assert "beta" in giso.operations


def test_interactive_generator_receives_capability_request():
    requests: list[str] = []

    def provider():
        request = yield
        while True:
            assert isinstance(request, CapabilityRequest)
            requests.append(request.path)
            if request.path == "status":
                request = yield status
            else:
                request = yield None

    giso = Giso(provider())

    assert giso.status() == "ready"
    assert requests == ["status"]


def test_existing_namespace_requests_full_missing_path():
    requests: list[str] = []

    def provider():
        request = yield
        while True:
            requests.append(request.path)
            if request.path == "math.triple":
                request = yield math__triple
            else:
                request = yield None

    giso = Giso(math__double, provider())

    assert giso.math.double(4) == 8
    assert giso.math.triple(4) == 12
    assert requests == ["math.triple"]


def test_multiple_deferred_sources_each_get_one_chance_per_lookup():
    first_requests: list[str] = []
    second_requests: list[str] = []

    def first():
        request = yield
        while True:
            first_requests.append(request.path)
            request = yield None

    def second():
        request = yield
        while True:
            second_requests.append(request.path)
            if request.path == "status":
                request = yield status
            else:
                request = yield None

    giso = Giso(first(), second())

    assert giso.status() == "ready"
    assert first_requests == ["status"]
    assert second_requests == ["status"]


def test_exhausted_generator_is_removed_after_one_failed_lookup():
    def empty():
        if False:
            yield None

    giso = Giso(empty())

    with pytest.raises(AttributeError):
        _ = giso.missing

    assert giso._deferred_sources == []


def test_add_does_not_clone_live_generator_state():
    def provider():
        yield status

    original = Giso(provider())
    derived = original + alpha

    assert derived.alpha() == "alpha"
    with pytest.raises(AttributeError):
        _ = derived.status
    assert original.status() == "ready"
