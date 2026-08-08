param(
    [ValidateSet("Bundled", "Existing", "Cloud")]
    [string]$Mode = "Bundled",
    [switch]$Gpu,
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

docker info *> $null

$composeArgs = @("compose", "-f", "compose.yaml")
if ($Mode -eq "Bundled") {
    $composeArgs += @("-f", "compose.local.yaml")
}
if ($Gpu -and $Mode -eq "Bundled") {
    $composeArgs += @("-f", "compose.gpu.yaml")
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
if ($Mode -eq "Bundled") {
    Write-Host "Bundled Ollama is enabled. Follow model setup with: docker compose -f compose.yaml -f compose.local.yaml logs -f ollama-init"
} elseif ($Mode -eq "Existing") {
    $ollamaUrl = if ($env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL } else { "http://host.docker.internal:11434/v1" }
    Write-Host "Using an existing Ollama server at $ollamaUrl. Set OLLAMA_BASE_URL in .env if this is not correct."
} else {
    Write-Host "Cloud-only stack started; no local model was downloaded or started."
}
