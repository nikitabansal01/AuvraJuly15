#!/usr/bin/env python3
"""Fail CI when the AUVRA v2 architecture boundaries are bypassed.

This intentionally uses only the Python standard library so the guard runs
before application dependencies are installed.  It protects the clean v2
tree while the legacy implementation remains available as migration evidence.
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = REPOSITORY_ROOT / "app" / "v2"
MAX_FILE_LINES = 800
MAX_FUNCTION_LINES = 100
MAX_COMPLEXITY = 15
MAX_LINE_LENGTH = 100

PROVIDER_MODULES = (
    "cloudinary",
    "firebase_admin",
    "google.generativeai",
    "groq",
    "langchain",
    "langgraph",
    "openai",
    "pinecone",
    "supabase",
)

TRANSACTION_METHODS = {"begin", "commit", "rollback"}
TRANSACTION_RECEIVERS = {
    "connection",
    "conn",
    "db",
    "engine",
    "session",
    "_connection",
    "_session",
}
FIRE_AND_FORGET_METHODS = {"create_task", "ensure_future"}
BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.IfExp,
    ast.Match,
    ast.comprehension,
)


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    rule: str
    message: str

    def render(self) -> str:
        relative = self.path.relative_to(REPOSITORY_ROOT)
        return f"{relative}:{self.line}: [{self.rule}] {self.message}"


def dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.module, node.lineno))
    return imports


def function_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, BRANCH_NODES):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += max(1, len(child.values) - 1)
    return complexity


def is_test_or_generated(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("generated_")


def inspect_style(path: Path) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    sql_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        upper = node.value.upper()
        if any(
            marker in upper
            for marker in ("CREATE ", "DROP ", "SELECT ", "UPDATE ", " IS NULL", " IN (")
        ):
            sql_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    violations: list[Violation] = []
    compound_lines = {
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.OP and token.string == ";"
    }
    for number, line in enumerate(source.splitlines(), start=1):
        allowed_long = number in sql_lines or line.lstrip().startswith(("http://", "https://"))
        if len(line) > MAX_LINE_LENGTH and not allowed_long:
            violations.append(Violation(path, number, "AUVRA007", "line exceeds 100 characters"))
        if number in compound_lines:
            violations.append(
                Violation(path, number, "AUVRA008", "compound semicolon statements are forbidden")
            )
    return violations


def inspect_file(path: Path) -> list[Violation]:
    violations = inspect_style(path)
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    relative = path.relative_to(V2_ROOT)

    if len(lines) > MAX_FILE_LINES and not is_test_or_generated(path):
        violations.append(
            Violation(
                path,
                1,
                "AUVRA001",
                f"handwritten file has {len(lines)} lines; maximum is {MAX_FILE_LINES}",
            )
        )

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [
            Violation(
                path,
                exc.lineno or 1,
                "AUVRA000",
                f"file cannot be parsed: {exc.msg}",
            )
        ]

    for function in (
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if is_test_or_generated(path):
            continue
        function_lines = (function.end_lineno or function.lineno) - function.lineno + 1
        if function_lines > MAX_FUNCTION_LINES:
            violations.append(
                Violation(
                    path,
                    function.lineno,
                    "AUVRA002",
                    f"{function.name} has {function_lines} lines; maximum is {MAX_FUNCTION_LINES}",
                )
            )
        complexity = function_complexity(function)
        if complexity > MAX_COMPLEXITY:
            violations.append(
                Violation(
                    path,
                    function.lineno,
                    "AUVRA003",
                    f"{function.name} complexity is {complexity}; maximum is {MAX_COMPLEXITY}",
                )
            )

    in_infrastructure = relative.parts and relative.parts[0] == "infrastructure"
    for module, line in imported_modules(tree):
        if any(
            module == provider or module.startswith(f"{provider}.") for provider in PROVIDER_MODULES
        ):
            if not in_infrastructure:
                violations.append(
                    Violation(
                        path,
                        line,
                        "AUVRA004",
                        f"provider SDK {module!r} is allowed only in app/v2/infrastructure",
                    )
                )

    is_uow = relative.as_posix() == "persistence/uow.py"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = dotted_name(node.func) or ""
        method = call_name.rsplit(".", 1)[-1]
        if method in FIRE_AND_FORGET_METHODS:
            violations.append(
                Violation(
                    path,
                    node.lineno,
                    "AUVRA005",
                    f"fire-and-forget call {call_name!r} is forbidden for business work",
                )
            )
        receiver = call_name.rsplit(".", 1)[0].rsplit(".", 1)[-1] if "." in call_name else ""
        if method in TRANSACTION_METHODS and receiver in TRANSACTION_RECEIVERS and not is_uow:
            violations.append(
                Violation(
                    path,
                    node.lineno,
                    "AUVRA006",
                    f"transaction method {method!r} may be called only by the Unit of Work",
                )
            )

    return violations


def main() -> int:
    if not V2_ROOT.is_dir():
        print(f"missing v2 source tree: {V2_ROOT}", file=sys.stderr)
        return 2

    violations: list[Violation] = []
    for path in sorted(V2_ROOT.rglob("*.py")):
        if "__pycache__" not in path.parts:
            violations.extend(inspect_file(path))
    style_paths = list((REPOSITORY_ROOT / "alembic" / "recovery_versions").glob("*.py"))
    style_paths.extend((REPOSITORY_ROOT / "tests").glob("test_v2_*.py"))
    style_paths.append(REPOSITORY_ROOT / "scripts" / "check_v2_architecture.py")
    for path in sorted(style_paths):
        violations.extend(inspect_style(path))

    if violations:
        for violation in sorted(
            violations, key=lambda item: (str(item.path), item.line, item.rule)
        ):
            print(violation.render(), file=sys.stderr)
        print(
            f"AUVRA v2 architecture gate failed with {len(violations)} violation(s).",
            file=sys.stderr,
        )
        return 1

    print("AUVRA v2 architecture gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
