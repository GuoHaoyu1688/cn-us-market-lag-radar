from __future__ import annotations

from pathlib import Path, PurePosixPath


def resolve_within(root: Path, reference: str) -> Path:
    """Resolve a repository-relative reference without allowing root escape."""
    normalized = str(reference or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    relative = PurePosixPath(normalized)
    if not normalized or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe relative path: {reference!r}")
    base = root.resolve()
    candidate = (base / Path(*relative.parts)).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError(f"path escapes root: {reference!r}")
    return candidate
