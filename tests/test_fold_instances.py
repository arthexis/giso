from __future__ import annotations

from giso import Giso


class StatefulCounter:
    def __init__(self, start: int = 0):
        self.value = start
        self.label = "counter"

    def increment(self, amount: int = 1) -> int:
        self.value += amount
        return self.value

    def current(self) -> int:
        return self.value

    def _reset(self) -> None:
        self.value = 0

    @property
    def explosive(self):
        raise AssertionError("properties must not be evaluated while folding")


class ConfiguredGreeter:
    def __init__(self, prefix: str):
        self.prefix = prefix

    def hello(self, name: str) -> str:
        return f"{self.prefix} {name}"

    @staticmethod
    def kind() -> str:
        return "greeter"

    @classmethod
    def class_name(cls) -> str:
        return cls.__name__


def test_constructor_folds_public_methods_from_object_instance():
    counter = StatefulCounter(start=4)

    giso = Giso(counter)

    assert giso.stateful_counter.current() == 4
    assert giso.stateful_counter.increment(3) == 7
    assert counter.value == 7
    assert set(giso.operations) == {
        "stateful_counter.current",
        "stateful_counter.increment",
    }


def test_instance_fold_preserves_configured_state_across_calls():
    greeter = ConfiguredGreeter(prefix="hello")
    giso = Giso()

    returned = giso.fold(greeter)

    assert returned is giso
    assert giso.configured_greeter.hello("Ada") == "hello Ada"

    greeter.prefix = "welcome"

    assert giso.configured_greeter.hello("Grace") == "welcome Grace"


def test_instance_fold_ignores_private_methods_attributes_and_properties():
    counter = StatefulCounter(start=2)

    giso = Giso(counter)

    assert "stateful_counter._reset" not in giso.operations
    assert "stateful_counter.label" not in giso.operations
    assert "stateful_counter.value" not in giso.operations
    assert "stateful_counter.explosive" not in giso.operations
    assert not hasattr(giso.stateful_counter, "_reset")
    assert not hasattr(giso.stateful_counter, "explosive")


def test_instance_fold_supports_static_and_class_methods():
    greeter = ConfiguredGreeter(prefix="hi")

    giso = Giso(greeter)

    assert giso.configured_greeter.kind() == "greeter"
    assert giso.configured_greeter.class_name() == "ConfiguredGreeter"


def test_builtin_instances_remain_unsupported_sources():
    for source in (42, 3.5, object()):
        try:
            Giso(source)
        except TypeError as exc:
            assert "Unsupported source type" in str(exc)
        else:
            raise AssertionError(f"built-in instance unexpectedly folded: {source!r}")
