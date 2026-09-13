param(
    [int]$Cases = 6,
    [int]$Particles = 1500,
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

$CaseRoot = Join-Path $ProjectRoot "out\external_validation\corsica_2018"
$CurrentGrid = Join-Path $CaseRoot "environment\currents.nc"
if (-not (Test-Path -LiteralPath $CurrentGrid)) {
    throw "Copernicus current grid not found. Assemble the Corsica incident first."
}
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
& $PythonPath -m espada.digital_twin `
    --ais (Join-Path $CaseRoot "ais\ais_normalized.csv") `
    --environment (Join-Path $CaseRoot "environment\environment.json") `
    --spatial-current-grid $CurrentGrid `
    --out (Join-Path $ProjectRoot "out\digital_twin") `
    --cases $Cases `
    --particles $Particles
exit $LASTEXITCODE
