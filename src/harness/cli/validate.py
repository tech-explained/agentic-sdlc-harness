"""Fixture validation: manifests, fault references, content integrity."""

from __future__ import annotations

import json
from pathlib import Path

REQUIRED_MANIFEST_KEYS = (
    "task_id",
    "category",
    "goal",
    "allowed_paths",
    "needs",
    "commands",
    "contract",
)

CATEGORIES = ("local-logic", "error-handling", "consumer-schema", "provider-schema")


def validate_fixtures(fixtures_root: Path) -> bool:
    fixtures_root = Path(fixtures_root)
    ok = True

    def fail(msg: str) -> None:
        nonlocal ok
        ok = False
        print(f"INVALID: {msg}")

    tasks_dir = fixtures_root / "tasks"
    task_ids: list[str] = []
    if not tasks_dir.is_dir():
        fail(f"tasks directory missing: {tasks_dir}")
        return False
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        manifest_path = task_dir / "manifest.json"
        if not manifest_path.is_file():
            fail(f"{task_id}: manifest.json missing")
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            fail(f"{task_id}: manifest.json is not valid JSON: {exc}")
            continue
        for key in REQUIRED_MANIFEST_KEYS:
            if key not in manifest:
                fail(f"{task_id}: manifest missing key {key!r}")
        if manifest.get("task_id") != task_id:
            fail(f"{task_id}: manifest task_id mismatch")
        if manifest.get("category") not in CATEGORIES:
            fail(f"{task_id}: unknown category {manifest.get('category')!r}")
        if not manifest.get("goal"):
            fail(f"{task_id}: empty goal")
        for part in ("seed", "solution"):
            if not (task_dir / part).is_dir():
                fail(f"{task_id}: {part}/ missing")
        seed_impl = task_dir / "seed" / "impl.py"
        sol_impl = task_dir / "solution" / "impl.py"
        seed_tests = list((task_dir / "seed" / "tests").glob("test_*.py"))
        if not seed_impl.is_file():
            fail(f"{task_id}: seed/impl.py missing")
        if not sol_impl.is_file():
            fail(f"{task_id}: solution/impl.py missing")
        if not seed_tests:
            fail(f"{task_id}: seed/tests has no test files")
        for name in ("baseline.json",):
            p = task_dir / "seed" / "schemas" / name
            if not p.is_file():
                fail(f"{task_id}: seed/schemas/{name} missing")
                continue
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except ValueError as exc:
                fail(f"{task_id}: seed/schemas/{name} invalid JSON: {exc}")
        for name in ("baseline.json", "candidate.json"):
            p = task_dir / "solution" / "schemas" / name
            if not p.is_file():
                fail(f"{task_id}: solution/schemas/{name} missing")
                continue
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except ValueError as exc:
                fail(f"{task_id}: solution/schemas/{name} invalid JSON: {exc}")
        commands = manifest.get("commands", {})
        for cmd_name in ("lint", "test"):
            cmd = commands.get(cmd_name)
            if not isinstance(cmd, list) or not cmd:
                fail(f"{task_id}: commands.{cmd_name} must be a non-empty list")
        contract = manifest.get("contract", {})
        for ckey in ("baseline", "candidate"):
            if ckey not in contract:
                fail(f"{task_id}: contract.{ckey} missing")
        task_ids.append(task_id)

    counts: dict[str, int] = {}
    for task_id in task_ids:
        manifest = json.loads(
            (tasks_dir / task_id / "manifest.json").read_text(encoding="utf-8")
        )
        counts[manifest["category"]] = counts.get(manifest["category"], 0) + 1
    expected = {
        "local-logic": 4,
        "error-handling": 3,
        "consumer-schema": 3,
        "provider-schema": 2,
    }
    if counts != expected:
        fail(f"category counts {counts} != expected {expected}")

    # Fault manifest.
    fault_path = fixtures_root / "faults" / "manifest.json"
    if not fault_path.is_file():
        fail("faults/manifest.json missing")
    else:
        try:
            faults = json.loads(fault_path.read_text(encoding="utf-8"))["faults"]
        except (ValueError, KeyError) as exc:
            fail(f"faults/manifest.json invalid: {exc}")
            faults = []
        seen_ids: set[str] = set()
        expected_detectors = {"lint", "test-execution", "contract-compatibility"}
        for f in faults:
            fid = f.get("fault_id")
            if fid in seen_ids:
                fail(f"duplicate fault_id {fid}")
            seen_ids.add(fid)
            for key in ("fault_id", "task_id", "expected_detector", "kind", "path"):
                if key not in f:
                    fail(f"fault {fid}: missing key {key!r}")
            if f.get("task_id") not in task_ids:
                fail(f"fault {fid}: unknown task {f.get('task_id')!r}")
            if f.get("expected_detector") not in expected_detectors:
                fail(f"fault {fid}: unknown detector {f.get('expected_detector')!r}")
            kind = f.get("kind")
            task_id = f.get("task_id")
            if kind == "append_text" and "text" not in f:
                fail(f"fault {fid}: append_text needs 'text'")
            if kind == "replace":
                target = tasks_dir / task_id / "solution" / f.get("path", "")
                if target.is_file():
                    if f.get("find", "") not in target.read_text(encoding="utf-8"):
                        fail(f"fault {fid}: find-string not present in solution file")
                else:
                    fail(f"fault {fid}: target {f.get('path')!r} not in solution/")
            if kind in ("json_remove_required", "json_narrow_type"):
                target = tasks_dir / task_id / "solution" / f.get("path", "")
                if not target.is_file():
                    fail(f"fault {fid}: target {f.get('path')!r} not in solution/")
            if kind == "write_outside_allowed" and "text" not in f:
                fail(f"fault {fid}: write_outside_allowed needs 'text'")
        if seen_ids != {"F1", "F2", "F3", "F4", "F5", "F6"}:
            fail(f"fault ids {sorted(seen_ids)} != expected F1..F6")

    if ok:
        print(f"fixtures valid: {len(task_ids)} tasks, category counts {counts}, "
              f"{len(faults) if 'faults' in dir() else 0} faults")
    return ok
