param([string]$PythonPath = "")

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $PythonPath = $candidate; break }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}
$CaseRoot = Join-Path $ProjectRoot "out\wakashio"
$required = @(
    (Join-Path $CaseRoot "prepared\slick.geojson"),
    (Join-Path $CaseRoot "prepared\blinded_candidates.csv"),
    (Join-Path $CaseRoot "prepared\sealed_truth.json"),
    (Join-Path $ProjectRoot "data\cache\environment_historical.json")
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Historical replay input missing. Run scripts/run_wakashio_replay.ps1 first. Missing: $path"
    }
}
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
& $PythonPath -m espada.sensitivity `
    --slick (Join-Path $CaseRoot "prepared\slick.geojson") `
    --environment-cache (Join-Path $ProjectRoot "data\cache\environment_historical.json") `
    --candidates (Join-Path $CaseRoot "prepared\blinded_candidates.csv") `
    --truth (Join-Path $CaseRoot "prepared\sealed_truth.json") `
    --out (Join-Path $CaseRoot "sensitivity")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "ROBUSTNESS MILESTONE COMPLETE" -ForegroundColor Green
Write-Host (Join-Path $CaseRoot "sensitivity\sensitivity_report.html")
