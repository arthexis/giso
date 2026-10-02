from giso import Giso


def make__tool(name: str):
    def tool():
        return name

    tool.__name__ = name
    return tool


def make__pair(name: str, *, suffix: str = ""):
    def tool():
        return f"{name}{suffix}"

    tool.__name__ = f"{name}{suffix}"
    return tool


def test_self_fold_executes_operation_folds_result_and_returns_it():
    g = Giso(make__tool)

    ingredient = g << "make tool spice"

    assert callable(ingredient)
    assert ingredient.__name__ == "spice"
    assert hasattr(g, "spice")
    assert g.results.history == ()
    assert g.spice() == "spice"


def test_self_fold_does_not_record_transient_operation_result():
    g = Giso(make__tool)

    g << "make tool herb"

    assert "make.tool" not in g.results
    assert g.results.history == ()


def test_self_fold_supports_quoted_arguments_and_flags():
    g = Giso(make__pair)

    ingredient = g << 'make pair "red pepper" --suffix=-oil'

    assert ingredient.__name__ == "red pepper-oil"
    assert getattr(g, "red pepper-oil")() == "red pepper-oil"


def test_self_fold_unknown_operation_fails_without_mutation():
    g = Giso(make__tool)
    before = tuple(g.operations)

    try:
        g << "missing operation"
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")

    assert tuple(g.operations) == before
    assert g.results.history == ()
