"""Verify a transferred candidate using a separately recorded manifest digest.

Uses only Python's standard library. This checks integrity, not provenance,
licensing or safety, and assumes this verifier itself is trusted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

MANIFEST = "SOURCE_MANIFEST.json"


def verify(root, expected):
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Invalid directory or expected fingerprint")
    manifest_path = root / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Candidate manifest is unavailable")
    raw = manifest_path.read_bytes()
    if len(raw) > 131072 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Candidate manifest fingerprint does not match")
    document = json.loads(raw)
    if (not isinstance(document, dict) or set(document) != {"format", "version", "files"}
            or type(document["format"]) is not int or document["format"] != 1
            or not isinstance(document["version"], str) or not isinstance(document["files"], dict)
            or not 1 <= len(document["files"]) <= 256):
        raise ValueError("Invalid candidate manifest")
    all_paths = list(root.rglob("*"))
    if any(p.is_symlink() for p in all_paths):
        raise ValueError("Candidate contains a link")
    actual = {p.relative_to(root).as_posix() for p in all_paths if p.is_file()}
    if actual != set(document["files"]) | {MANIFEST}:
        raise ValueError("Candidate files were added or removed")
    for name, item in document["files"].items():
        if (not isinstance(name, str) or "\\" in name or name == MANIFEST
                or PurePosixPath(name).is_absolute()
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or not isinstance(item, dict) or set(item) != {"size", "sha256"}
                or type(item["size"]) is not int or not 0 <= item["size"] <= 128 * 1024 * 1024
                or not isinstance(item["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError("Invalid candidate file description")
        path = root / name
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Candidate file is outside its folder")
        if path.stat().st_size != item["size"]:
            raise ValueError("Candidate file size changed")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise ValueError("Candidate file fingerprint changed")
    component = json.loads((root / "custom_components/eufy_nvr/manifest.json").read_bytes())
    if component.get("version") != document["version"]:
        raise ValueError("Candidate integration version changed")
    return document["version"], len(document["files"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        version, count = verify(args.directory, args.expected_sha256)
    except (OSError, ValueError, KeyError, TypeError):
        print("Candidate verification failed; do not build or install this folder.", file=sys.stderr)
        return 1
    print(f"Verified {count} candidate files for {version}. No files were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
