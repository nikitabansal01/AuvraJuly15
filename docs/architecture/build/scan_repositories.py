#!/usr/bin/env python3
"""Create a reproducible, content-free repository evidence snapshot.

The scanner reads committed Git objects rather than the mutable working tree. It
records counts, paths, and hashes only; it never copies source or configuration
values into the architecture package.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXCLUDED_PARTS = {
    ".git",
    ".expo",
    "node_modules",
    "android",
    "ios",
    "venv",
    ".venv",
    "__pycache__",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def tracked_files(repo: Path, ref: str) -> list[str]:
    listing = git(repo, "ls-tree", "-r", "--name-only", ref)
    return [p for p in listing.splitlines() if not EXCLUDED_PARTS.intersection(Path(p).parts)]


def object_text(repo: Path, ref: str, path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        stderr=subprocess.DEVNULL,
    ).decode("utf-8", errors="replace")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    return ""


def backend_snapshot(repo: Path, ref: str) -> dict[str, Any]:
    files = tracked_files(repo, ref)
    py_files = [p for p in files if p.endswith(".py")]
    app_py_files = [p for p in py_files if p.startswith("app/")]
    line_counts: dict[str, int] = {}
    tables: list[dict[str, str]] = []
    routes: list[dict[str, Any]] = []
    create_task_hits = 0
    transaction_calls = {"commit": 0, "rollback": 0, "begin": 0}
    evidence_hashes: dict[str, str] = {}

    for path in py_files:
        source = object_text(repo, ref, path)
        line_counts[path] = len(source.splitlines())
        if path in {
            "app/main.py",
            "app/api/v1/api.py",
            "app/core/config.py",
            "app/core/database.py",
            "docs/DATABASE_RECOVERY.md",
        }:
            evidence_hashes[path] = sha256_text(source)
        create_task_hits += len(re.findall(r"\basyncio\.create_task\s*\(", source))
        for key in transaction_calls:
            transaction_calls[key] += len(re.findall(rf"\.\s*{key}\s*\(", source))
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                table_name = None
                for statement in node.body:
                    if (
                        isinstance(statement, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in statement.targets)
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str)
                    ):
                        table_name = statement.value.value
                if table_name:
                    tables.append({"table": table_name, "class": node.name, "source": path})
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = []
                for decorator in node.decorator_list:
                    name = dotted_name(decorator)
                    method = name.rsplit(".", 1)[-1].lower()
                    if method in {"get", "post", "put", "patch", "delete", "head"}:
                        route_path = None
                        if isinstance(decorator, ast.Call) and decorator.args:
                            first = decorator.args[0]
                            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                                route_path = first.value
                        decorators.append((method.upper(), route_path or ""))
                if decorators:
                    function_text = ast.get_source_segment(source, node) or ""
                    auth_markers = (
                        "Depends(get_current_user",
                        "Depends(get_current_active_user",
                        "Depends(security",
                        "verify_id_token",
                    )
                    protected = any(marker in function_text for marker in auth_markers)
                    for method, route_path in decorators:
                        routes.append(
                            {
                                "method": method,
                                "path_fragment": route_path,
                                "function": node.name,
                                "source": path,
                                "syntactic_auth_guard": protected,
                            }
                        )

    route_mutations = [r for r in routes if r["method"] in {"POST", "PUT", "PATCH", "DELETE"}]
    route_unprotected = [r for r in routes if not r["syntactic_auth_guard"]]
    mutation_unprotected = [r for r in route_mutations if not r["syntactic_auth_guard"]]
    test_files = [p for p in py_files if Path(p).name.startswith("test_")]
    for path in ("docs/DATABASE_RECOVERY.md", "requirements.txt", "alembic.ini"):
        if path in files:
            evidence_hashes[path] = sha256_text(object_text(repo, ref, path))

    return {
        "repository": str(repo),
        "ref": ref,
        "commit": git(repo, "rev-parse", ref),
        "commit_date": git(repo, "show", "-s", "--format=%aI", ref),
        "subject": git(repo, "show", "-s", "--format=%s", ref),
        "working_tree_status_at_build": git(repo, "status", "--short"),
        "tracked_files": len(files),
        "python_lines_all": sum(line_counts.values()),
        "python_lines_app": sum(line_counts[p] for p in app_py_files),
        "largest_python_files": [
            {"path": path, "lines": lines}
            for path, lines in sorted(line_counts.items(), key=lambda item: item[1], reverse=True)[:15]
        ],
        "orm_tables": sorted(tables, key=lambda item: item["table"]),
        "orm_table_count": len(tables),
        "route_operation_count": len(routes),
        "mutation_operation_count": len(route_mutations),
        "syntactically_unguarded_operation_count": len(route_unprotected),
        "syntactically_unguarded_mutation_count": len(mutation_unprotected),
        "route_guard_methodology": (
            "Static AST/lexical evidence only: an operation is marked guarded when its function "
            "contains a recognized Depends(...) or Firebase verification marker. Manual review remains required."
        ),
        "asyncio_create_task_hits": create_task_hits,
        "transaction_call_hits": transaction_calls,
        "test_file_count": len(test_files),
        "test_files": sorted(test_files),
        "evidence_sha256": evidence_hashes,
    }


def mobile_snapshot(repo: Path, ref: str) -> dict[str, Any]:
    files = tracked_files(repo, ref)
    code_files = [p for p in files if Path(p).suffix in {".ts", ".tsx", ".js", ".jsx"}]
    line_counts: dict[str, int] = {}
    fetch_count = 0
    fetch_files: list[str] = []
    url_files: list[str] = []
    async_storage_files: list[str] = []
    password_storage_hits: list[dict[str, Any]] = []
    evidence_hashes: dict[str, str] = {}

    for path in code_files:
        source = object_text(repo, ref, path)
        line_counts[path] = len(source.splitlines())
        if path in {"App.tsx", "app.json", "package.json", "services/authService.ts"}:
            evidence_hashes[path] = sha256_text(source)
        hits = len(re.findall(r"\bfetch\s*\(", source))
        fetch_count += hits
        if hits:
            fetch_files.append(path)
        if re.search(r"EXPO_PUBLIC_API_URL|API_BASE_URL|BASE_URL|localhost:8000", source):
            url_files.append(path)
        if "AsyncStorage" in source:
            async_storage_files.append(path)
        for line_no, line in enumerate(source.splitlines(), start=1):
            if "AsyncStorage" in source and re.search(r"saved[_A-Z]*password|SAVED_PASSWORD", line, re.I):
                password_storage_hits.append({"path": path, "line": line_no})

    test_files = [
        p for p in code_files if re.search(r"(^|/)(?:__tests__/|[^/]+\.(?:test|spec)\.)", p)
    ]
    duplicate_markers = [
        p for p in files if re.search(r"(^|/)(?:old_|temp_|backup|copy)", Path(p).name, re.I)
    ]
    for path in ("App.tsx", "app.json", "package.json", "services/authService.ts"):
        if path in files:
            evidence_hashes[path] = sha256_text(object_text(repo, ref, path))

    return {
        "repository": str(repo),
        "ref": ref,
        "commit": git(repo, "rev-parse", ref),
        "commit_date": git(repo, "show", "-s", "--format=%aI", ref),
        "subject": git(repo, "show", "-s", "--format=%s", ref),
        "working_tree_status_at_build": git(repo, "status", "--short"),
        "tracked_files": len(files),
        "typescript_javascript_lines": sum(line_counts.values()),
        "largest_code_files": [
            {"path": path, "lines": lines}
            for path, lines in sorted(line_counts.items(), key=lambda item: item[1], reverse=True)[:15]
        ],
        "raw_fetch_call_count": fetch_count,
        "raw_fetch_files": sorted(fetch_files),
        "api_url_implementation_files": sorted(url_files),
        "api_url_implementation_file_count": len(set(url_files)),
        "async_storage_files": sorted(async_storage_files),
        "plaintext_password_storage_reference_count": len(password_storage_hits),
        "plaintext_password_storage_references": password_storage_hits,
        "test_file_count": len(test_files),
        "test_files": sorted(test_files),
        "duplicate_or_temporary_source_markers": sorted(duplicate_markers),
        "evidence_sha256": evidence_hashes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--backend-ref", required=True)
    parser.add_argument("--mobile-root", type=Path, required=True)
    parser.add_argument("--mobile-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    snapshot = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Committed source evidence; no secret values or health-data contents captured.",
        "backend": backend_snapshot(args.backend_root.resolve(), args.backend_ref),
        "mobile": mobile_snapshot(args.mobile_root.resolve(), args.mobile_ref),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
