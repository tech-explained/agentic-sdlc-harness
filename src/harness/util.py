"""Shared small utilities: canonical JSON, hashing, timestamps, file digests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def canonical_json(obj) -> str:
    """Deterministic JSON: sorted keys, compact separators, ASCII."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_of(text: str) -> str:
    """sha256 digest with algorithm prefix, per the canonicalization rules."""
    return "sha256:" + sha256_hex(text)


def content_digest(mapping: dict[str, str]) -> str:
    """Digest over a {posix relpath: hex-digest} mapping."""
    return digest_of(canonical_json(mapping))


def utcnow_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_canonical_json(path: Path, obj) -> None:
    path.write_text(canonical_json(obj) + "\n", encoding="utf-8")


def read_canonical_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def tree_digest(root: Path) -> str:
    """Digest of a directory tree: {posix relpath: file digest} for all files."""
    mapping: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if "__pycache__" in rel or rel.endswith((".pyc", ".pyo")):
                continue
            mapping[rel] = file_sha256(p)
    return content_digest(mapping)


def artifact_digest(workspace: Path, relpaths: list[str]) -> str:
    """Digest over the agent-declared artifact set (changed paths only)."""
    mapping: dict[str, str] = {}
    for rel in sorted(relpaths):
        p = workspace / rel
        if not p.is_file():
            raise FileNotFoundError(f"declared artifact missing: {rel}")
        mapping[rel] = file_sha256(p)
    return content_digest(mapping)
