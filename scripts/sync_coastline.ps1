param(
    [Parameter(Mandatory = $true)][double]$MinLongitude,
    [Parameter(Mandatory = $true)][double]$MinLatitude,
    [Parameter(Mandatory = $true)][double]$MaxLongitude,
    [Parameter(Mandatory = $true)][double]$MaxLatitude,
    [string]$OutputPath = "",
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
if (-not $OutputPath) { $OutputPath = Join-Path $ProjectRoot "out\coastline\land_mask.geojson" }
$CachePath = Join-Path $ProjectRoot "data\cache\natural_earth\ne_10m_land.zip"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonPath -m espada.coast_download `
    --bbox $MinLongitude $MinLatitude $MaxLongitude $MaxLatitude `
    --output $OutputPath --cache $CachePath
exit $LASTEXITCODE
