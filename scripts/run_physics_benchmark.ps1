param(
    [string]$PythonPath = "",
    [double]$MaximumSeparationM = 250.0
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

$CurrentFile = Join-Path $ProjectRoot "out\external_validation\corsica_2018\environment\currents.nc"
if (-not (Test-Path -LiteralPath $CurrentFile)) {
    throw "The Corsica Copernicus current subset is missing. Run the Corsica case preparation first."
}
$Output = Join-Path $ProjectRoot "out\physics_benchmark"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"

& $PythonPath -m espada.physics_benchmark `
    --current-file $CurrentFile `
    --out $Output `
    --start-time "2018-10-07T19:00:00+00:00" `
    --duration-hours 10 `
    --step-minutes 60 `
    --maximum-separation-m $MaximumSeparationM
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "PHYSICS PARITY BENCHMARK COMPLETE" -ForegroundColor Green
Write-Host (Join-Path $Output "physics_benchmark.html") -ForegroundColor Yellow
