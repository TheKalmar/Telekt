"""Confined per-company artifact workspace.

This is intentionally not a general shell. It gives autonomous agents a narrow,
auditable filesystem boundary while the broader sandboxed execution runtime is
still under development.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import ClassVar


class _HTMLProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()

    def handle_starttag(self, tag: str, attrs) -> None:
        self.tags.add(tag.lower())


class WorkspaceRuntime:
    """Write and inspect only safe files below one company's artifact root."""

    ALLOWED_SUFFIXES: ClassVar[set[str]] = {
        ".html",
        ".css",
        ".js",
        ".json",
        ".md",
        ".txt",
    }
    MAX_FILE_BYTES = 2 * 1024 * 1024

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str) -> Path:
        """Resolve a portable relative path and reject traversal or odd file types."""
        if not relative_path or "\\" in relative_path:
            raise ValueError("Artifact path must be a portable relative path")
        logical = PurePosixPath(relative_path)
        if logical.is_absolute() or ".." in logical.parts or "." in logical.parts:
            raise ValueError("Artifact path escapes the company workspace")
        target = (self.root / Path(*logical.parts)).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError("Artifact path escapes the company workspace")
        if target.suffix.lower() not in self.ALLOWED_SUFFIXES:
            raise ValueError(f"Artifact type is not allowed: {target.suffix or '(none)'}")
        return target

    def write_text(self, relative_path: str, content: str) -> dict:
        """Atomically write UTF-8 content and return verifiable file metadata."""
        encoded = content.encode("utf-8")
        if len(encoded) > self.MAX_FILE_BYTES:
            raise ValueError("Artifact exceeds the 2 MiB workspace limit")
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".artifact-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise
        checks = self.validate(relative_path, content)
        return {
            "path": relative_path,
            "size_bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "checks": checks,
        }

    def validate(self, relative_path: str, content: str | None = None) -> dict:
        """Run deterministic checks suitable for the current single-file MVP."""
        target = self.resolve(relative_path)
        body = target.read_text(encoding="utf-8") if content is None else content
        checks = {"non_empty": bool(body.strip()), "utf8": True}
        if target.suffix.lower() == ".html":
            probe = _HTMLProbe()
            try:
                probe.feed(body)
                checks.update(
                    {
                        "html_document": "html" in probe.tags,
                        "has_body": "body" in probe.tags,
                        "has_script": "script" in probe.tags,
                    }
                )
            except Exception:
                checks["parseable_html"] = False
        checks["passed"] = all(checks.values())
        return checks

    def inventory(self) -> list[dict]:
        """List safe artifact metadata without exposing file bodies."""
        files = []
        for target in sorted(path for path in self.root.rglob("*") if path.is_file()):
            relative = target.relative_to(self.root).as_posix()
            if target.suffix.lower() not in self.ALLOWED_SUFFIXES:
                continue
            content = target.read_bytes()
            files.append(
                {
                    "path": relative,
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        return files
