"""Deterministic static-analysis checker (the harness's declared linter).

Runs as a subprocess via ``python -m harness.gates.lint_check`` and is also
importable for unit tests. Checks, in order:

1. Scope: every file in the workspace must be tracked (seed file, declared
   artifact) or explicitly allowed; anything else is SCOPE.VIOLATION.
2. Syntax: py_compile over changed Python files (LINT.SYNTAX_ERROR).
3. Unused imports via AST (LINT.UNUSED_IMPORT).
4. Line length > 120 (LINT.LINE_TOO_LONG, low severity: noted, not failing).

Exit code 0 when no finding reaches medium severity or above, else 1.
Findings are always emitted as JSON on stdout.
"""

from __future__ import annotations

import argparse
import ast
import json
import py_compile
import sys
from pathlib import Path

MAX_LINE_LENGTH = 120


def _check_scope(workspace: Path, tracked: set[str], allowed: list[str]) -> list[dict]:
    findings = []
    for p in sorted(workspace.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(workspace).as_posix()
        if "__pycache__" in rel or rel.endswith((".pyc", ".pyo")):
            continue
        if ".pytest_cache" in rel:
            continue
        if rel in tracked:
            continue
        if any(rel == a or rel.startswith(a.rstrip("/") + "/") for a in allowed):
            continue
        findings.append(
            {
                "rule_id": "SCOPE.VIOLATION",
                "severity": "high",
                "path": rel,
                "message": f"file outside allowed paths: {rel}",
            }
        )
    return findings


def _check_syntax(workspace: Path, changed: list[str]) -> list[dict]:
    findings = []
    for rel in changed:
        if not rel.endswith(".py"):
            continue
        p = workspace / rel
        if not p.is_file():
            findings.append(
                {
                    "rule_id": "LINT.MISSING_FILE",
                    "severity": "high",
                    "path": rel,
                    "message": "declared changed file is missing",
                }
            )
            continue
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as exc:
            findings.append(
                {
                    "rule_id": "LINT.SYNTAX_ERROR",
                    "severity": "critical",
                    "path": rel,
                    "message": f"parse error: {exc.msg.splitlines()[0] if exc.msg else exc}",
                }
            )
    return findings


def _check_ast(workspace: Path, changed: list[str]) -> list[dict]:
    findings = []
    for rel in changed:
        if not rel.endswith(".py"):
            continue
        p = workspace / rel
        if not p.is_file():
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, OSError):
            continue  # syntax errors are reported by _check_syntax
        imported: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = (alias.asname or alias.name).split(".")[0]
                    imported.setdefault(name, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    name = (alias.asname or alias.name).split(".")[0]
                    imported.setdefault(name, node.lineno)
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                n = node
                while isinstance(n, ast.Attribute):
                    n = n.value
                if isinstance(n, ast.Name):
                    used.add(n.id)
        for name, lineno in sorted(imported.items(), key=lambda kv: kv[1]):
            if name not in used:
                findings.append(
                    {
                        "rule_id": "LINT.UNUSED_IMPORT",
                        "severity": "medium",
                        "path": f"{rel}:{lineno}",
                        "message": f"unused import: {name}",
                    }
                )
    return findings


def _check_line_length(workspace: Path, changed: list[str]) -> list[dict]:
    findings = []
    for rel in changed:
        p = workspace / rel
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, start=1):
            if len(line) > MAX_LINE_LENGTH:
                findings.append(
                    {
                        "rule_id": "LINT.LINE_TOO_LONG",
                        "severity": "low",
                        "path": f"{rel}:{i}",
                        "message": f"line length {len(line)} > {MAX_LINE_LENGTH}",
                    }
                )
    return findings


def check(
    workspace: Path, allowed: list[str], changed: list[str], tracked: set[str]
) -> list[dict]:
    """Run all checks; return finding dicts."""
    findings: list[dict] = []
    findings.extend(_check_scope(workspace, tracked, allowed))
    findings.extend(_check_syntax(workspace, changed))
    findings.extend(_check_ast(workspace, changed))
    findings.extend(_check_line_length(workspace, changed))
    return findings


_SEV_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="harness lint checker")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--allowed", nargs="*", default=[])
    parser.add_argument("--changed", nargs="*", default=[])
    parser.add_argument("--tracked", nargs="*", default=[])
    args = parser.parse_args(argv)
    ws = Path(args.workspace).resolve()
    findings = check(ws, args.allowed, args.changed, set(args.tracked))
    worst = "none"
    for f in findings:
        if _SEV_RANK[f["severity"]] > _SEV_RANK[worst]:
            worst = f["severity"]
    print(json.dumps({"findings": findings, "worst_severity": worst}, indent=1))
    return 0 if _SEV_RANK[worst] < _SEV_RANK["medium"] else 1


if __name__ == "__main__":
    sys.exit(main())
