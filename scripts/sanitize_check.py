#!/usr/bin/env python3
"""Fail when files that should never be published appear in the repository."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache"}
BLOCKED_PARTS = {".ibkr_gateway", "logs", "snapshots", "account_snapshot", "position_snapshot"}
TEXT_SUFFIXES = {
    ".css",
    ".env",
    ".example",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
}
PATTERNS = {
    "absolute macOS user path": re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    "absolute Windows user path": re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s]+\\\\"),
    "email address": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "OpenAI token": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "broker account id": re.compile(r"\bU\d{7,}\b"),
    "nonempty secret assignment": re.compile(
        r"(?im)^(?:OPENAI_API_KEY|X_BEARER_TOKEN|TUSHARE_TOKEN|API_KEY|SECRET|PASSWORD)[ \t]*=[ \t]*[^\s#]+"
    ),
}


def is_text(path: Path) -> bool:
    return path.name == ".env.example" or path.suffix.lower() in TEXT_SUFFIXES


def main() -> int:
    failures: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            failures.append(f"symbolic link: {relative}")
            continue
        lowered = [part.lower() for part in relative.parts]
        if path.is_file() and any(
            blocked in part for blocked in BLOCKED_PARTS for part in lowered
        ):
            failures.append(f"blocked path: {relative}")
            continue
        if not path.is_file() or not is_text(path):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                failures.append(f"{label}: {relative}")
    if failures:
        print("Sanitization check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Sanitization check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
