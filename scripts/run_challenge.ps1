param(
    [string]$Config = "",
    [string]$OutputDirectory = "",
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
if (-not $Config) {
    $Config = Join-Path $ProjectRoot "configs\challenge_balanced.json"
}
$ResolvedConfig = Resolve-Path -LiteralPath $Config -ErrorAction Stop
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $ProjectRoot "out\challenge"
}

$CaseRoot = Join-Path $ProjectRoot "out\external_validation\corsica_2018"
$Ais = Join-Path $CaseRoot "ais\ais_normalized.csv"
$Environment = Join-Path $CaseRoot "environment\environment.json"
$CurrentGrid = Join-Path $CaseRoot "environment\currents.nc"
$LandMask = Join-Path $CaseRoot "environment\land_mask.geojson"
foreach ($Required in @($Ais, $Environment, $CurrentGrid, $LandMask)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Required Corsica evidence is missing: $Required"
    }
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
Write-Host "Running sealed-ground-truth ESPADA challenge..." -ForegroundColor Cyan
& $PythonPath -m espada.challenge `
    --config $ResolvedConfig.Path `
    --ais $Ais `
    --environment $Environment `
    --spatial-current-grid $CurrentGrid `
    --land-mask $LandMask `
    --out ([System.IO.Path]::GetFullPath($OutputDirectory))
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "CHALLENGE COMPLETE" -ForegroundColor Green
Write-Host "Open:" -ForegroundColor Yellow
Write-Host (Join-Path ([System.IO.Path]::GetFullPath($OutputDirectory)) "challenge_report.html")
