"""Injected-fault scenarios F1..F6.

Faults are explicit, labeled transformations applied by the evaluation
harness — never agent improvisation. Each fault names exactly one expected
detector gate. Multi-fault variants are excluded from the measured run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Fault:
    fault_id: str
    task_id: str
    expected_detector: str  # gate name: lint | test-execution | contract-compatibility
    kind: str
    path: str
    params: dict[str, Any]


def load_faults(manifest_path: Path) -> list[Fault]:
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    faults = []
    for entry in data["faults"]:
        faults.append(
            Fault(
                fault_id=entry["fault_id"],
                task_id=entry["task_id"],
                expected_detector=entry["expected_detector"],
                kind=entry["kind"],
                path=entry["path"],
                params={k: v for k, v in entry.items()
                        if k not in ("fault_id", "task_id", "expected_detector",
                                    "kind", "path")},
            )
        )
    return faults


def _navigate(obj: dict, dotted: str) -> tuple[dict, str | None]:
    """Return (parent dict, final key) for a dotted path.

    The special paths "root", "$", and "" address the document itself;
    the final key is None in that case.
    """
    if dotted in ("root", "$", ""):
        return obj, None
    parts = dotted.split(".")
    node = obj
    for part in parts[:-1]:
        node = node[part]
        if not isinstance(node, dict):
            raise KeyError(f"path {dotted!r} does not resolve to an object")
    return node, parts[-1]


def apply_fault(workspace: Path, fault: Fault) -> str:
    """Apply the fault transform to the workspace. Returns a description."""
    target = workspace / fault.path
    kind = fault.kind
    if kind == "append_text":
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(fault.params["text"])
        return f"appended {len(fault.params['text'])} chars to {fault.path}"
    if kind == "replace":
        text = target.read_text(encoding="utf-8")
        find = fault.params["find"]
        if find not in text:
            raise ValueError(f"F{fault.fault_id}: find-string not present in {fault.path}")
        text = text.replace(find, fault.params["replace"], 1)
        target.write_text(text, encoding="utf-8")
        return f"replaced snippet in {fault.path}"
    if kind == "json_remove_required":
        data = json.loads(target.read_text(encoding="utf-8"))
        parent, key = _navigate(data, fault.params["at"])
        node = parent if key is None else parent.get(key, {})
        if not isinstance(node, dict):
            raise KeyError(f"path {fault.params['at']!r} is not an object")
        field_name = fault.params["field"]
        required = node.get("required", [])
        if field_name in required:
            required.remove(field_name)
            node["required"] = required
        props = node.get("properties", {})
        props.pop(field_name, None)
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return f"removed required field {field_name} at {fault.params['at']}"
    if kind == "json_narrow_type":
        data = json.loads(target.read_text(encoding="utf-8"))
        parent, key = _navigate(data, fault.params["at"])
        node = parent if key is None else parent.get(key)
        if not isinstance(node, dict):
            raise KeyError(f"path {fault.params['at']!r} is not an object")
        node["type"] = fault.params["to_type"]
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return f"narrowed type at {fault.params['at']} to {fault.params['to_type']}"
    if kind == "write_outside_allowed":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fault.params["text"], encoding="utf-8")
        return f"wrote out-of-scope file {fault.path}"
    raise ValueError(f"unknown fault kind: {kind}")
