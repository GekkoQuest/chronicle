"""Reject private data and credentials before publishing a source checkout.

This is a release guard, not a general-purpose secret scanner. Git's tracked
file list is authoritative; outside a Git checkout, inspect the source tree.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {"snapshots", "snapshots_zip", "private", "data", "results", "private-kit", "harness", "eval"}
PRIVATE_SUFFIXES = {".zip", ".sqlite", ".sqlite3", ".db", ".jsonl", ".pdf"}
TEXT_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".txt", ".yml", ".toml", ".json"}


def files() -> list[Path]:
    try:
        result = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=False)
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0 and result.stdout:
        return [ROOT / name for name in result.stdout.decode().split("\0") if name]
    return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts]


def main() -> int:
    problems = []
    checked = files()
    for path in checked:
        relative = path.relative_to(ROOT)
        if PRIVATE_DIRS.intersection(relative.parts) or path.suffix in PRIVATE_SUFFIXES or path.name.startswith(".env"):
            problems.append(f"Private or supplied-data path: {relative}")
        if path.suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
            if re.search(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{24,}", text) or "-----BEGIN PRIVATE KEY-----" in text:
                # The scanner's own pattern is not a credential.
                if path.resolve() != Path(__file__).resolve():
                    problems.append(f"Possible credential: {relative}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"Release audit passed: {len(checked)} files; no supplied datasets, database files, archives, or detected credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
