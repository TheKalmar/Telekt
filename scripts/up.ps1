param(
    [ValidateSet("Bundled", "Existing", "Cloud")]
    [string]$Mode = "Bundled",
    [switch]$Gpu,
    [switch]$Mail,
    [switch]$Lightweight,
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

docker info *> $null

$composeArgs = @("compose", "-f", "compose.yaml")
if (Test-Path ".env.local") {
    $composeArgs = @("compose", "--env-file", ".env.local", "-f", "compose.yaml")
}
if ($Mode -eq "Bundled") {
    $composeArgs += @("-f", "compose.local.yaml")
}
if ($Gpu -and $Mode -eq "Bundled") {
    $composeArgs += @("-f", "compose.gpu.yaml")
}
if ($Mail) {
    $composeArgs += @("-f", "compose.email.yaml")
}
if (-not $Lightweight) {
    # Independent agents require durable per-agent Temporal workflows and the
    # PostgreSQL canonical store. Keep the SQLite/polling stack as an explicit
    # lightweight developer fallback, not the default installation path.
    $composeArgs += @("-f", "compose.infrastructure.yaml")
}
$composeArgs += @("up", "-d")
if ($Build) {
    $composeArgs += "--build"
}

& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed with exit code $LASTEXITCODE"
}

$publishedPort = if ($env:PORT) { $env:PORT } else { "8421" }
Write-Host "Digital Company is starting at http://127.0.0.1:$publishedPort"
if (-not $Lightweight) {
    Write-Host "Durable multi-agent infrastructure: PostgreSQL + self-hosted Temporal (no cloud fee)."
    Write-Host "Temporal UI: http://127.0.0.1:8080"
} else {
    Write-Host "Lightweight SQLite/polling mode enabled; independent agent Start requires the durable stack."
}
if ($Mode -eq "Bundled") {
    Write-Host "Bundled Ollama is enabled. Follow model setup with: docker compose -f compose.yaml -f compose.local.yaml logs -f ollama-init"
} elseif ($Mode -eq "Existing") {
    $ollamaUrl = if ($env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL } else { "http://host.docker.internal:11434/v1" }
    Write-Host "Using an existing Ollama server at $ollamaUrl. Set OLLAMA_BASE_URL in .env if this is not correct."
} else {
    Write-Host "Cloud-only stack started; no local model was downloaded or started."
}
if ($Mail) {
    $mailpitPort = if ($env:MAILPIT_PORT) { $env:MAILPIT_PORT } else { "8025" }
    Write-Host "Local approval inbox: http://127.0.0.1:$mailpitPort"
}
