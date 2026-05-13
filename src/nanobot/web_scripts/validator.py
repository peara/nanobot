from __future__ import annotations

import ast
from dataclasses import dataclass


class NanoScriptValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidationResult:
    tree: ast.Module


ALLOWED_IMPORTS = {"__future__", "datetime", "json", "re", "typing", "urllib.parse"}
BLOCKED_IMPORT_ROOTS = {"builtins", "os", "pathlib", "shutil", "socket", "subprocess", "sys"}
BLOCKED_CALL_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "globals",
    "input",
    "locals",
    "open",
}


class NanoScriptValidator(ast.NodeVisitor):
    """Validate NanoScript code before saving or executing it."""

    def validate(self, code: str) -> ValidationResult:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise NanoScriptValidationError(f"Syntax error: {exc.msg}") from exc

        self._validate_top_level(tree)
        self.visit(tree)
        return ValidationResult(tree=tree)

    def _validate_top_level(self, tree: ast.Module) -> None:
        script_defs = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "script"]
        if len(script_defs) != 1:
            raise NanoScriptValidationError("NanoScript requires exactly one top-level async def script(page, params)")

        for node in tree.body:
            is_docstring = (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
            if is_docstring:
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if node is script_defs[0]:
                continue
            if isinstance(node, ast.FunctionDef) and node.name == "script":
                raise NanoScriptValidationError("script must be async def, not def")
            raise NanoScriptValidationError("Only imports, a module docstring, and async def script are allowed")

        script = script_defs[0]
        arg_names = [arg.arg for arg in script.args.args]
        if arg_names[:2] != ["page", "params"]:
            raise NanoScriptValidationError("script must accept page and params as the first two arguments")
        if script.args.vararg or script.args.kwarg or script.args.kwonlyargs or script.args.defaults:
            raise NanoScriptValidationError("script must not use varargs, kwargs, keyword-only args, or default args")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._validate_import_name(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            raise NanoScriptValidationError("Relative imports are not allowed")
        if node.level:
            raise NanoScriptValidationError("Relative imports are not allowed")
        self._validate_import_name(node.module)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise NanoScriptValidationError(f"Dunder names are not allowed: {node.id}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__"):
            raise NanoScriptValidationError(f"Dunder attributes are not allowed: {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = self._call_name(node.func)
        if call_name in BLOCKED_CALL_NAMES:
            raise NanoScriptValidationError(f"Blocked call: {call_name}")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        raise NanoScriptValidationError("global statements are not allowed")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        raise NanoScriptValidationError("nonlocal statements are not allowed")

    def visit_Return(self, node: ast.Return) -> None:
        if node.value is None:
            raise NanoScriptValidationError("script must return a dict")
        if isinstance(node.value, ast.Constant) or isinstance(node.value, (ast.List, ast.Set, ast.Tuple)):
            raise NanoScriptValidationError("script must return a dict")
        self.generic_visit(node)

    def _validate_import_name(self, name: str) -> None:
        root = name.split(".", 1)[0]
        if root in BLOCKED_IMPORT_ROOTS:
            raise NanoScriptValidationError(f"Blocked import: {root}")
        if name not in ALLOWED_IMPORTS:
            raise NanoScriptValidationError(f"Import is not allowed: {name}")

    def _call_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None
