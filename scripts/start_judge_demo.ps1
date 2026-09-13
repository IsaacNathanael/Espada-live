param(
    [int]$Port = 4173,
    [switch]$NoOpen,
    [switch]$RecomputeValidation,
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

$ScorecardJson = Join-Path $ProjectRoot "out\system_validation\system_scorecard.json"
$ScorecardHtml = Join-Path $ProjectRoot "out\system_validation\system_scorecard.html"
if ($RecomputeValidation) {
    & (Join-Path $PSScriptRoot "run_validation_suite.ps1") -PythonPath $PythonPath | Out-Null
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
elseif (-not ((Test-Path -LiteralPath $ScorecardJson) -and (Test-Path -LiteralPath $ScorecardHtml))) {
    & (Join-Path $PSScriptRoot "run_validation_suite.ps1") -PythonPath $PythonPath -UseExistingResults | Out-Null
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
else {
    Write-Host "Using the existing validated system scorecard."
}

$DemoEvidence = Join-Path $ProjectRoot "out\demo\candidates.json"
if (-not (Test-Path -LiteralPath $DemoEvidence)) {
    & (Join-Path $PSScriptRoot "run_demo.ps1") -PythonPath $PythonPath | Out-Null
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$HubPath = Join-Path $ProjectRoot "out\judge_demo\index.html"
& $PythonPath -m espada.judge_demo --project-root $ProjectRoot --output $HubPath | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& (Join-Path $PSScriptRoot "run_showcase.ps1") -PythonPath $PythonPath -Port $Port -NoOpen -UseExistingDashboard | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$DemoUrl = "http://127.0.0.1:$Port/out/judge_demo/index.html"
Write-Host "ESPADA DEMO READY" -ForegroundColor Green
Write-Host $DemoUrl -ForegroundColor Yellow
if (-not $NoOpen) {
    try { Start-Process -FilePath $DemoUrl }
    catch { Write-Host "Copy the address above into Chrome or Edge." -ForegroundColor Yellow }
}
