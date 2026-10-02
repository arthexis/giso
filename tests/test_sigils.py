from giso import Giso, Sigil


def math__double(value):
    return value * 2


def test_single_brackets_resolve_now():
    giso = Giso(math__double)

    operation = giso["math.double"]

    assert operation(4) == 8


def test_double_brackets_return_lazy_sigil():
    giso = Giso()
    sigil = giso[["math.double"]]

    assert isinstance(sigil, Sigil)
    assert sigil.root is giso
    assert sigil.path == "math.double"

    giso.ingest(math__double)

    assert sigil.value(5) == 10


def test_sigil_call_invokes_callable_when_arguments_are_given():
    giso = Giso(math__double)
    sigil = giso[["math.double"]]

    assert sigil(6) == 12


def test_sigil_without_arguments_returns_current_value():
    giso = Giso()
    giso.answer = 42
    sigil = giso[["answer"]]

    assert sigil() == 42


def test_sigil_tracks_live_root_state():
    giso = Giso()
    giso.answer = 1
    sigil = giso[["answer"]]

    assert sigil.value == 1

    giso.answer = 2

    assert sigil.value == 2
