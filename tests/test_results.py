from giso import Giso, Results


def math__double(value):
    return value * 2


def math__triple(value):
    return value * 3


def text__upper(value):
    return value.upper()


def test_giso_accumulates_operation_results_and_returns_values():
    g = Giso(math__double, math__triple)

    assert isinstance(g.results, Results)
    assert g.math.double(2) == 4
    assert g.math.triple(3) == 9

    assert g.results["math.double"] == 4
    assert g.results["math.triple"] == 9
    assert g.results.last == 9
    assert g.results.history == (
        ("math.double", 4),
        ("math.triple", 9),
    )


def test_repeated_calls_preserve_history_and_latest_value():
    g = Giso(math__double)

    assert g.math.double(2) == 4
    assert g.math.double(5) == 10

    assert g.results["math.double"] == 10
    assert g.results.history == (
        ("math.double", 4),
        ("math.double", 10),
    )


def test_derived_giso_has_independent_results():
    g = Giso(math__double)
    child = g + math__triple

    assert child.math.double(4) == 8
    assert g.results.history == ()
    assert child.results.history == (("math.double", 8),)


def test_folding_giso_replays_source_results_into_target():
    source = Giso(math__double)
    target = Giso(text__upper)

    source.math.double(2)
    source.math.double(5)
    target.text.upper("before")

    target += source

    assert target.results.history == (
        ("text.upper", "BEFORE"),
        ("math.double", 4),
        ("math.double", 10),
    )
    assert target.results["math.double"] == 10
    assert source.results.history == (
        ("math.double", 4),
        ("math.double", 10),
    )


def test_plus_combines_result_histories_without_sharing_results():
    left = Giso(math__double)
    right = Giso(text__upper)

    left.math.double(3)
    right.text.upper("giso")

    combined = left + right

    assert combined.results.history == (
        ("math.double", 6),
        ("text.upper", "GISO"),
    )
    assert combined.results is not left.results
    assert combined.results is not right.results

    combined.math.double(4)

    assert combined.results.history[-1] == ("math.double", 8)
    assert left.results.history == (("math.double", 6),)
    assert right.results.history == (("text.upper", "GISO"),)
