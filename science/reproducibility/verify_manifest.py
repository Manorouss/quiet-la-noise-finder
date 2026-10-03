#!/usr/bin/env python3
"""Verify exact bundle file membership and byte hashes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT / "MANIFEST.json").read_text())
entries = manifest["files"]
expected = {entry["path"] for entry in entries}
actual = {
    path.relative_to(ROOT).as_posix()
    for path in ROOT.rglob("*")
    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    and path.name != "MANIFEST.json"
}
errors = []
if actual != expected:
    errors.append(f"file inventory mismatch: missing={sorted(expected-actual)} extra={sorted(actual-expected)}")
for entry in entries:
    path = ROOT / entry["path"]
    if not path.is_file():
        continue
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != entry["bytes"] or digest != entry["sha256"]:
        errors.append(f"hash/size mismatch: {entry['path']}")
if errors:
    print("\n".join(errors), file=sys.stderr)
    raise SystemExit(1)
print(f"verified {len(entries)} files; {manifest['bundle_bytes']} bytes")
