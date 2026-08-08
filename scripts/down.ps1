$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

# Named volumes are intentionally preserved. Use `docker compose down --volumes`
# manually only when company state and downloaded models should be erased.
docker compose down --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed with exit code $LASTEXITCODE"
}
