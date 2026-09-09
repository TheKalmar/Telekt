[CmdletBinding()]
param(
    [switch]$SkipDependencyAudit
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Create .venv and install -e `".[dev]`" first."
}

function Invoke-QualityGate {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [scriptblock]$Command
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

Push-Location $RepoRoot
try {
    Invoke-QualityGate "Static analysis" { & $Python -m ruff check src tests scripts evals }
    Invoke-QualityGate "Formatting" { & $Python -m ruff format --check src tests scripts evals }
    Invoke-QualityGate "Test suite" { & $Python -m pytest --cov --cov-report=term-missing }
    Invoke-QualityGate "Behavior eval validation" { & $Python evals\run_local.py --validate-only }
    Invoke-QualityGate "Security scan" { & $Python -m bandit -r src -q -ll }

    if (-not $SkipDependencyAudit) {
        Invoke-QualityGate "Dependency audit" { & $Python -m pip_audit --skip-editable }
    }

    Write-Host "All Telekt quality gates passed." -ForegroundColor Green
}
finally {
    Pop-Location
}
