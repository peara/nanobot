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

# Canonical set of Python builtins available in the NanoScript sandbox.
# runner.py constructs its __builtins__ dict from this set; the validator
# checks against it.  Keep in sync: add here first, then update runner.py.
SAFE_BUILTIN_NAMES = frozenset(
    {
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "Exception",
        "float",
        "int",
        "isinstance",
        "len",
        "list",
        "max",
        "min",
        "next",
        "range",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
    }
)

# Names available in the script runtime beyond SAFE_BUILTIN_NAMES.
# These are injected by NanoScriptRunner._compile or are Python keywords
# that appear as names in type annotations.
RUNTIME_NAMES = frozenset({"Any", "Page"})


class NanoScriptValidator(ast.NodeVisitor):
    """Validate NanoScript code before saving or executing it."""

    def __init__(self, *, safe_builtins: frozenset[str] | None = None) -> None:
        self._safe_builtins: frozenset[str] = safe_builtins if safe_builtins is not None else SAFE_BUILTIN_NAMES
        self._local_names: frozenset[str] = frozenset()

    def validate(self, code: str) -> ValidationResult:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise NanoScriptValidationError(f"Syntax error: {exc.msg}") from exc

        self._validate_top_level(tree)

        # Collect locally-defined names before the full walk so visit_Name
        # can distinguish them from undefined/unsafe references.
        self._local_names = _collect_local_names(tree)
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
        # Only check names in Load context — Store context means the name is
        # being assigned, which is fine.  But since we pre-collect local names,
        # we check Load names against the allowlist to catch builtin usage
        # that would fail at runtime.
        if isinstance(node.ctx, ast.Load):
            if not self._is_name_allowed(node.id):
                allowed = sorted(self._safe_builtins | RUNTIME_NAMES)
                raise NanoScriptValidationError(
                    f"Name '{node.id}' is not available. Allowed builtins and runtime names: {', '.join(allowed)}"
                )

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

    def _is_name_allowed(self, name: str) -> bool:
        if name in self._local_names:
            return True
        if name in self._safe_builtins:
            return True
        if name in RUNTIME_NAMES:
            return True
        return False

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


def _collect_local_names(tree: ast.Module) -> frozenset[str]:
    """Collect all names defined locally within the script function body.

    Includes: function args, assignment targets, loop variables, comprehension
    variables, import bindings, and exception handler names.  These names are
    safe to reference even though they aren't in SAFE_BUILTINS.
    """
    names: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname if alias.asname else alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname if alias.asname else alias.name)

    script_defs = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "script"]
    if not script_defs:
        return frozenset(names)
    script = script_defs[0]
    for arg in script.args.args:
        names.add(arg.arg)

    for child in ast.walk(script):
        if isinstance(child, ast.Assign):
            for target in child.targets:
                names.update(_target_names(target))
        elif isinstance(child, ast.AugAssign):
            names.update(_target_names(child.target))
        elif isinstance(child, (ast.For, ast.AsyncFor)):
            names.update(_target_names(child.target))
        elif isinstance(child, ast.comprehension):
            names.update(_target_names(child.target))
        elif isinstance(child, ast.ExceptHandler):
            if child.name:
                names.add(child.name)

        if isinstance(child, ast.NamedExpr):
            names.update(_target_names(child.target))

    return frozenset(names)


def _target_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            names.update(_target_names(elt))
    elif isinstance(node, ast.Starred):
        names.update(_target_names(node.value))
    return names
