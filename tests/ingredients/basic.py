from __future__ import annotations

from giso import Giso


def math__double(value: int) -> int:
    return value * 2


def text__upper(value: str) -> str:
    return value.upper()


class Greeter:
    def hello(self, name: str) -> str:
        return f"hello {name}"


def kitchen__make_extra() -> Giso:
    def spice__paprika(value: str) -> str:
        return f"{value}:paprika"

    extra = Giso(spice__paprika)
    extra.spice.paprika("seed")
    return extra
