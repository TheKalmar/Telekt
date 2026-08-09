param(
    [string]$OutputDirectory = ".backups",
    [string]$ComposeEnvFile = ".env.local",
    [string]$DatabaseUser = "",
    [string]$DatabaseName = ""
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$output = Join-Path $workspace $OutputDirectory
New-Item -ItemType Directory -Force -Path $output | Out-Null
$output = (Resolve-Path -LiteralPath $output).Path

$compose = @(
    "compose", "--env-file", $ComposeEnvFile,
    "-f", "compose.yaml", "-f", "compose.infrastructure.yaml"
)
$container = (& docker @compose ps -q postgres).Trim()
if (-not $container) {
    throw "PostgreSQL container is not running. Start the infrastructure stack first."
}
if (-not $DatabaseUser) { $DatabaseUser = (& docker exec $container printenv POSTGRES_USER).Trim() }
if (-not $DatabaseName) { $DatabaseName = (& docker exec $container printenv POSTGRES_DB).Trim() }
if (-not $DatabaseUser -or -not $DatabaseName) {
    throw "Could not resolve POSTGRES_USER or POSTGRES_DB from the running container."
}

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
$fileName = "telekt-$stamp.dump"
$containerDirectory = "/var/lib/postgresql/data/telekt-backups"
$containerPath = "$containerDirectory/$fileName"
$hostPath = Join-Path $output $fileName

& docker exec $container mkdir -p $containerDirectory
if ($LASTEXITCODE -ne 0) { throw "Could not create the backup directory in PostgreSQL." }
& docker exec $container pg_dump -U $DatabaseUser -d $DatabaseName --format=custom --no-owner --no-privileges --file=$containerPath
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed." }
& docker exec $container pg_restore --list $containerPath | Out-Null
if ($LASTEXITCODE -ne 0) { throw "pg_restore could not validate the generated archive." }
& docker cp "${container}:${containerPath}" $hostPath
if ($LASTEXITCODE -ne 0) { throw "Could not copy the validated backup to the host." }

Write-Host "Validated PostgreSQL backup: $hostPath"
Write-Host "A second copy remains in the postgres_data volume at $containerPath."
