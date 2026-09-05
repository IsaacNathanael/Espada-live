param(
    [string]$CatalogPath = "",
    [double]$MinLongitude = 71.25,
    [double]$MinLatitude = 18.55,
    [double]$MaxLongitude = 71.65,
    [double]$MaxLatitude = 18.90,
    [int]$Width = 1536,
    [int]$Height = 1400,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
. (Join-Path $PSScriptRoot "_load_env.ps1")
Import-EspadaEnv -Path (Join-Path $ProjectRoot ".env")

if (-not $CatalogPath) {
    $CatalogPath = Join-Path $ProjectRoot "out\sentinel1_candidate\sentinel1_catalog.json"
}
if (-not (Test-Path -LiteralPath $CatalogPath)) {
    throw "A verified Sentinel-1 catalogue result is required. Run scene discovery first."
}
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
    throw "The stable Espada Python environment was not found."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
& $PythonPath -m espada.cli sar-download `
    --catalog $CatalogPath `
    --bbox $MinLongitude $MinLatitude $MaxLongitude $MaxLatitude `
    --width $Width --height $Height `
    --out (Join-Path $ProjectRoot "out\sentinel1_case")
exit $LASTEXITCODE
