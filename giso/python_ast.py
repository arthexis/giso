from __future__ import annotations

import ast
import hashlib
from types import ModuleType
from typing import Any

from .construction import Giso as ConstructionGiso


class Giso(ConstructionGiso):
    """A Giso that can fold native Python AST objects."""

    def fold(self, *sources: Any) -> "Giso":
        remaining: list[Any] = []
        for source in sources:
            if isinstance(source, ast.Module):
                self._fold_ast_module(source)
            elif isinstance(source, ast.Expression):
                self._fold_ast_expression(source)
            else:
                remaining.append(source)
        if remaining:
            super().fold(*remaining)
        return self

    def _fold_ast_module(self, tree: ast.Module) -> None:
        tree = ast.fix_missing_locations(tree)
        identity = self._ast_identity(tree)
        module_name = f"_giso_ast_{identity}"
        filename = f"<giso-ast:{identity}>"
        module = ModuleType(module_name)
        module.__file__ = filename
        code = compile(tree, filename, "exec")
        exec(code, module.__dict__)
        self._fold_imported_module(module)

    def _fold_ast_expression(self, tree: ast.Expression) -> None:
        tree = ast.fix_missing_locations(tree)
        identity = self._ast_identity(tree)
        filename = f"<giso-ast:{identity}>"
        code = compile(tree, filename, "eval")
        value = eval(code, {"__builtins__": __builtins__})
        self.fold(value)

    @staticmethod
    def _ast_identity(tree: ast.AST) -> str:
        serialized = ast.dump(tree, annotate_fields=True, include_attributes=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
