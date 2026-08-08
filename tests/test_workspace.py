from pathlib import Path

import pytest

from digital_company.workspace import WorkspaceRuntime


def test_workspace_atomically_writes_and_validates_html(tmp_path: Path):
    workspace = WorkspaceRuntime(tmp_path / "artifacts")

    metadata = workspace.write_text(
        "mvp/index.html",
        "<!doctype html><html><body><h1>MVP</h1><script>const ready=true</script></body></html>",
    )

    assert metadata["checks"]["passed"] is True
    assert metadata["size_bytes"] > 20
    assert len(metadata["sha256"]) == 64
    assert workspace.inventory() == [{
        "path": "mvp/index.html",
        "size_bytes": metadata["size_bytes"],
        "sha256": metadata["sha256"],
    }]


@pytest.mark.parametrize("path", ["../secret.txt", "/etc/passwd", "mvp\\index.html", "app.exe"])
def test_workspace_rejects_unsafe_or_executable_paths(tmp_path: Path, path: str):
    workspace = WorkspaceRuntime(tmp_path / "artifacts")

    with pytest.raises(ValueError):
        workspace.write_text(path, "unsafe")


def test_workspace_rejects_oversized_artifact(tmp_path: Path):
    workspace = WorkspaceRuntime(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="2 MiB"):
        workspace.write_text("mvp/index.html", "x" * (workspace.MAX_FILE_BYTES + 1))
