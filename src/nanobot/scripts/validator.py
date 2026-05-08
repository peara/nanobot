from __future__ import annotations

import ast
from dataclasses import dataclass

_BLOCKED_CALLS = {
    "eval",
    "exec",
    "open",
    "compile",
    "__import__",
    "input",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
}

_BLOCKED_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.ClassDef,
    ast.Lambda,
    ast.Global,
    ast.Nonlocal,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Raise,
    ast.Delete,
    ast.Yield,
    ast.YieldFrom,
    ast.Await,
    ast.AsyncFunctionDef,
)

_ALLOWED_NODE_TYPES = {
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.Assign,
    ast.AnnAssign,
    ast.Expr,
    ast.For,
    ast.While,
    ast.If,
    ast.Break,
    ast.Continue,
    ast.Compare,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.Dict,
    ast.List,
    ast.Tuple,
    ast.Subscript,
    ast.Attribute,
    ast.ListComp,
    ast.comprehension,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.keyword,
    ast.IfExp,
    ast.Slice,
    ast.Pass,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.FloorDiv,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.USub,
    ast.UAdd,
}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]


class NanoScriptAstValidator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[str] = []

    def validate(self, code: str) -> ValidationResult:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return ValidationResult(ok=False, errors=[f"syntax error: {exc}"])

        self._validate_script_function_shape(tree)
        self.visit(tree)
        return ValidationResult(ok=not self.errors, errors=self.errors)

    def _validate_script_function_shape(self, tree: ast.Module) -> None:
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        if len(functions) != 1:
            self.errors.append("script must define exactly one function")
            return
        function = functions[0]
        if function.name != "script":
            self.errors.append("function must be named 'script'")
        args = [arg.arg for arg in function.args.args]
        if args != ["browser", "params"]:
            self.errors.append("function signature must be script(browser, params)")
        if function.args.vararg is not None or function.args.kwarg is not None:
            self.errors.append("script signature must not use *args/**kwargs")
        if tree.body != [function]:
            self.errors.append("top-level code is not allowed")

    def visit(self, node: ast.AST) -> None:
        if type(node) not in _ALLOWED_NODE_TYPES and not isinstance(node, _BLOCKED_NODES):
            self.errors.append(f"node type not allowed: {type(node).__name__}")
        super().visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        del node
        self.errors.append("import is not allowed")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        del node
        self.errors.append("import is not allowed")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node
        self.errors.append("class definition is not allowed")

    def visit_Global(self, node: ast.Global) -> None:
        del node
        self.errors.append("global is not allowed")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        del node
        self.errors.append("nonlocal is not allowed")

    def visit_With(self, node: ast.With) -> None:
        del node
        self.errors.append("with is not allowed")

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        del node
        self.errors.append("with is not allowed")

    def visit_Lambda(self, node: ast.Lambda) -> None:
        del node
        self.errors.append("lambda is not allowed")

    def visit_Try(self, node: ast.Try) -> None:
        del node
        self.errors.append("try/except is not allowed")

    def visit_Raise(self, node: ast.Raise) -> None:
        del node
        self.errors.append("raise is not allowed")

    def visit_Delete(self, node: ast.Delete) -> None:
        del node
        self.errors.append("delete is not allowed")

    def visit_Await(self, node: ast.Await) -> None:
        del node
        self.errors.append("await is not allowed")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        del node
        self.errors.append("async def is not allowed")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__"):
            self.errors.append("dunder attribute access is not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = self._call_name(node)
        if name in _BLOCKED_CALLS:
            self.errors.append(f"blocked call: {name}")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            self.errors.append("while True is not allowed")
        if not self._contains_loop_guard_call(node.test):
            self.errors.append("while condition must use browser.loop_guard(...)")
        self.generic_visit(node)

    def _contains_loop_guard_call(self, test: ast.AST) -> bool:
        for item in ast.walk(test):
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute):
                if isinstance(item.func.value, ast.Name) and item.func.value.id == "browser":
                    if item.func.attr == "loop_guard":
                        return True
        return False

    def _call_name(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None
