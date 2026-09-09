"""Network-isolated, per-company build and Git checkpoint runtime.

The API accepts structured commands rather than shell strings. It deliberately
cannot clone, fetch, push, deploy, or reach the public internet in the default
Compose topology. External publication remains a separate governed action.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Literal

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Telekt Isolated Execution Runtime")
DATA_DIR = Path(os.getenv("EXECUTION_DATA_DIR", "/workspaces")).resolve()
TOKEN = os.getenv("EXECUTION_RUNTIME_TOKEN", "")
COMMANDS_ENABLED = os.getenv("EXECUTION_COMMANDS_ENABLED", "false").lower() == "true"
COMPANY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
BRANCH_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$")
ALLOWED_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 128 * 1024
locks: dict[str, threading.Lock] = {}
locks_guard = threading.Lock()


class RuntimeFile(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=MAX_FILE_BYTES)


class RepositoryCheckpointIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    files: list[RuntimeFile] = Field(min_length=1, max_length=50)
    branch: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=160)


class CommandIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    executable: Literal["python", "pytest", "node", "npm", "git"]
    args: list[str] = Field(default_factory=list, max_length=40)
    cwd: str = Field(default=".", max_length=300)
    timeout_seconds: int = Field(default=60, ge=1, le=120)


def _authorize(value: str | None) -> None:
    if TOKEN and (not value or not hmac.compare_digest(value, TOKEN)):
        raise HTTPException(401, "Invalid execution-runtime token")


def _workspace(company_id: str) -> Path:
    if not COMPANY_RE.fullmatch(company_id):
        raise HTTPException(400, "Invalid company ID")
    target = (DATA_DIR / company_id).resolve()
    if target.parent != DATA_DIR:
        raise HTTPException(400, "Invalid company workspace")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _relative(root: Path, value: str, *, require_suffix: bool = False) -> Path:
    if not value or "\\" in value:
        raise HTTPException(400, "Paths must be portable and relative")
    logical = PurePosixPath(value)
    if logical.is_absolute() or ".." in logical.parts:
        raise HTTPException(400, "Path escapes the company workspace")
    target = (root / Path(*logical.parts)).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(400, "Path escapes the company workspace")
    if require_suffix and target.suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"File type is not allowed: {target.suffix or '(none)'}")
    return target


def _lock(company_id: str) -> threading.Lock:
    with locks_guard:
        return locks.setdefault(company_id, threading.Lock())


def _fingerprint(payload: BaseModel) -> str:
    body = payload.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _cache_path(root: Path, category: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()
    target = root / ".telekt" / category / f"{digest}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _cached(cache: Path, fingerprint: str) -> dict | None:
    if not cache.exists():
        return None
    value = json.loads(cache.read_text(encoding="utf-8"))
    if value["fingerprint"] != fingerprint:
        raise HTTPException(409, "Idempotency key was already used for different input")
    return {**value["result"], "cached": True}


def _store_cache(cache: Path, fingerprint: str, result: dict) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".result-", dir=cache.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"fingerprint": fingerprint, "result": result}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, cache)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_file(root: Path, item: RuntimeFile) -> dict:
    content = item.content.encode("utf-8")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(400, "File exceeds the 2 MiB runtime limit")
    target = _relative(root, item.path, require_suffix=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".telekt-file-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        "path": item.path,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=check,
        timeout=30,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(root / ".home")},
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "network_policy": "internal-only",
        "commands_enabled": COMMANDS_ENABLED,
        "commands": ["python", "pytest", "node", "npm", "git"] if COMMANDS_ENABLED else [],
    }


@app.post("/workspaces/{company_id}/repository/checkpoints")
def checkpoint_repository(
    company_id: str,
    payload: RepositoryCheckpointIn,
    x_telekt_execution_token: str | None = Header(default=None),
):
    _authorize(x_telekt_execution_token)
    root = _workspace(company_id)
    fingerprint = _fingerprint(payload)
    cache = _cache_path(root, "checkpoints", payload.idempotency_key)
    with _lock(company_id):
        if cached := _cached(cache, fingerprint):
            return cached
        if not BRANCH_RE.fullmatch(payload.branch) or ".." in payload.branch.split("/"):
            raise HTTPException(400, "Invalid Git branch")
        files = [_write_file(root, item) for item in payload.files]
        if not (root / ".git").is_dir():
            _git(root, "init", "-b", "main")
            info = root / ".git" / "info" / "exclude"
            info.write_text(".telekt/\n.home/\n", encoding="utf-8")
        _git(root, "config", "user.name", "Telekt AI Company")
        _git(root, "config", "user.email", "agent@telekt.local")
        _git(root, "checkout", "-B", payload.branch)
        _git(root, "add", "--all")
        changed = _git(root, "diff", "--cached", "--quiet", check=False).returncode != 0
        if changed:
            _git(root, "commit", "-m", payload.message)
        head = _git(root, "rev-parse", "HEAD", check=False)
        commit = head.stdout.strip() if head.returncode == 0 else None
        result = {
            "status": "committed" if changed else "unchanged",
            "branch": payload.branch,
            "commit": commit,
            "files": files,
            "cached": False,
        }
        _store_cache(cache, fingerprint, result)
        return result


def _validate_command(payload: CommandIn) -> None:
    if any(not isinstance(arg, str) or len(arg) > 300 or "\x00" in arg for arg in payload.args):
        raise HTTPException(400, "Invalid command argument")
    args = payload.args
    if payload.executable == "python":
        valid = bool(args) and (
            (len(args) >= 2 and args[:2] == ["-m", "pytest"])
            or (not args[0].startswith("-") and args[0].endswith(".py"))
        )
    elif payload.executable == "pytest":
        valid = all(
            not arg.startswith("/") and ".." not in PurePosixPath(arg).parts for arg in args
        )
    elif payload.executable == "node":
        valid = bool(args) and not args[0].startswith("-") and args[0].endswith(".js")
    elif payload.executable == "npm":
        valid = bool(args) and args[0] in {"test", "run", "ci"}
    else:
        safe_git = {
            "init",
            "status",
            "add",
            "commit",
            "branch",
            "switch",
            "checkout",
            "log",
            "diff",
            "rev-parse",
        }
        valid = bool(args) and args[0] in safe_git
    if not valid:
        raise HTTPException(400, f"Command is outside the {payload.executable} policy")


@app.post("/workspaces/{company_id}/commands")
def execute_command(
    company_id: str,
    payload: CommandIn,
    x_telekt_execution_token: str | None = Header(default=None),
):
    _authorize(x_telekt_execution_token)
    if not COMMANDS_ENABLED:
        raise HTTPException(
            503,
            "General commands are disabled until a per-job process sandbox is configured",
        )
    _validate_command(payload)
    root = _workspace(company_id)
    cwd = _relative(root, payload.cwd)
    if not cwd.is_dir():
        raise HTTPException(400, "Command working directory does not exist")
    fingerprint = _fingerprint(payload)
    cache = _cache_path(root, "commands", payload.idempotency_key)
    with _lock(company_id):
        if cached := _cached(cache, fingerprint):
            return cached
        try:
            completed = subprocess.run(
                [payload.executable, *payload.args],
                cwd=cwd,
                text=False,
                capture_output=True,
                timeout=payload.timeout_seconds,
                check=False,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(root / ".home"),
                    "CI": "true",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            result = {
                "status": "completed" if completed.returncode == 0 else "failed",
                "exit_code": completed.returncode,
                "stdout": completed.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
                "stderr": completed.stderr[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
                "timed_out": False,
                "cached": False,
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "status": "timed_out",
                "exit_code": None,
                "stdout": (exc.stdout or b"")[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
                "stderr": (exc.stderr or b"")[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
                "timed_out": True,
                "cached": False,
            }
        _store_cache(cache, fingerprint, result)
        return result


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        "digital_company.execution_runtime:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8440")),
    )


if __name__ == "__main__":
    main()
