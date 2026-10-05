from __future__ import annotations

import ast
import hashlib
import marshal
from types import CodeType, ModuleType
from typing import Any

from .construction import Giso as ConstructionGiso


class Giso(ConstructionGiso):
    """A Giso that can fold native Python AST and compiled code objects."""

    @classmethod
    def compile(cls, source: str, *, name: str = "giso") -> "Giso":
        """Build a Giso from Python statement/module source text."""
        if not isinstance(source, str):
            raise TypeError("Giso.compile() source must be a string")
        return cls(ast.parse(source, mode="exec"), name=name)

    @classmethod
    def eval(cls, expression: str, *, name: str = "giso") -> "Giso":
        """Build a Giso from one Python expression and fold its resulting value."""
        if not isinstance(expression, str):
            raise TypeError("Giso.eval() expression must be a string")
        return cls(ast.parse(expression, mode="eval"), name=name)

    def fold(self, *sources: Any) -> "Giso":
        for source in self._flatten(sources):
            if source is None:
                continue
            if isinstance(source, ast.Module):
                self._fold_ast_module(source)
            elif isinstance(source, ast.Expression):
                self._fold_ast_expression(source)
            elif isinstance(source, CodeType):
                self._fold_code(source)
            else:
                super().fold(source)
        return self

    def _fold_ast_module(self, tree: ast.Module) -> None:
        tree = ast.fix_missing_locations(tree)
        identity = self._ast_identity(tree)
        filename = f"<giso-ast:{identity}>"
        code = compile(tree, filename, "exec")
        self._fold_code(code, identity=identity)

    def _fold_ast_expression(self, tree: ast.Expression) -> None:
        tree = ast.fix_missing_locations(tree)
        identity = self._ast_identity(tree)
        filename = f"<giso-ast:{identity}>"
        code = compile(tree, filename, "eval")
        self._fold_code(code, identity=identity)

    def _fold_code(self, code: CodeType, *, identity: str | None = None) -> None:
        if code.co_argcount or code.co_posonlyargcount or code.co_kwonlyargcount:
            raise TypeError("Code objects requiring arguments are not standalone fold sources")
        if code.co_freevars:
            raise TypeError("Code objects with free variables are not standalone fold sources")

        identity = identity or self._code_identity(code)
        module_name = f"_giso_code_{identity}"
        filename = code.co_filename or f"<giso-code:{identity}>"
        module = ModuleType(module_name)
        module.__file__ = filename
        value = eval(code, module.__dict__)
        if value is None:
            self._fold_imported_module(module)
        else:
            self.fold(value)

    @staticmethod
    def _ast_identity(tree: ast.AST) -> str:
        serialized = ast.dump(tree, annotate_fields=True, include_attributes=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _code_identity(code: CodeType) -> str:
        return hashlib.sha256(marshal.dumps(code)).hexdigest()[:16]
