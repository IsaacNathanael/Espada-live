param([string]$PythonPath = "")

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

$Base = Join-Path $ProjectRoot "out\external_validation\corsica_2018"
$OriginalRun = Join-Path $Base "run"
$Experiment = Join-Path $Base "counterfactual_ais"
$HybridAis = Join-Path $Experiment "hybrid_ais.csv"
$Registry = Join-Path $Experiment "sealed_synthetic_target.json"
$Run = Join-Path $Experiment "run"
$CurrentGrid = Join-Path $Base "environment\currents.nc"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

Write-Host "1/3 Adding one sealed synthetic source track to the real AIS background..." -ForegroundColor Cyan
& $PythonPath -m espada.counterfactual_validation prepare `
    --background-ais (Join-Path $Base "ais\ais_normalized.csv") `
    --release-estimate (Join-Path $OriginalRun "drift\release_estimate.json") `
    --output $HybridAis `
    --registry $Registry
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "2/3 Running the normal pipeline without opening the sealed target..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "run_approved_slick_case.ps1") `
    -SlickGeoJson (Join-Path $OriginalRun "sar\slick_observation.geojson") `
    -AisCsv $HybridAis `
    -EnvironmentCache (Join-Path $Base "environment\environment.json") `
    -SpatialCurrentGrid $CurrentGrid `
    -AgeHours 10 `
    -CandidateAgesHours @(6, 8, 10, 12, 16, 20, 24, 30) `
    -OutputDirectory $Run `
    -PythonPath $PythonPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "3/3 Opening the synthetic answer key after ranking..." -ForegroundColor Cyan
& $PythonPath -m espada.counterfactual_validation reveal `
    --ranking (Join-Path $Run "ranking\candidates.json") `
    --registry $Registry `
    --output (Join-Path $Experiment "counterfactual_result.json")
exit $LASTEXITCODE
