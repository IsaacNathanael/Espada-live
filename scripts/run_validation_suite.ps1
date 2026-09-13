param(
    [switch]$UseExistingResults,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
if (-not $PythonPath) {
    foreach ($Candidate in @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $Candidate) { $PythonPath = $Candidate; break }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}

if (-not $UseExistingResults) {
    Write-Host "1/3 Running 24 controlled robustness cases..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "run_evaluation.ps1") -PythonPath $PythonPath | Out-Null
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "2/3 Running the Wakashio known-source replay..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "run_wakashio_replay.ps1") -PythonPath $PythonPath | Out-Null
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "3/3 Running the Princess Empress abstention case..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "run_princess_empress_replay.ps1") | Out-Null
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "Using existing validated case artifacts..." -ForegroundColor Cyan
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$ScorecardDirectory = Join-Path $ProjectRoot "out\system_validation"
& $PythonPath -m espada.system_validation `
    --project-root $ProjectRoot `
    --out $ScorecardDirectory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "SYSTEM VALIDATION COMPLETE" -ForegroundColor Green
Write-Host (Join-Path $ScorecardDirectory "system_scorecard.html")
