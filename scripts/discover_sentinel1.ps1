param(
    [string]$StartUtc = "",
    [string]$EndUtc = "",
    [double]$MinLongitude = 70.8,
    [double]$MinLatitude = 17.8,
    [double]$MaxLongitude = 73.0,
    [double]$MaxLatitude = 20.0,
    [double]$TargetLongitude = 71.45,
    [double]$TargetLatitude = 18.7167,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
$AisQuality = Join-Path $ProjectRoot "out\historical_ais\ais_quality.json"
$OutputDirectory = Join-Path $ProjectRoot "out\sentinel1"

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
if (-not $StartUtc -or -not $EndUtc) {
    if (-not (Test-Path -LiteralPath $AisQuality)) {
        throw "Historical AIS quality report is missing. Run scripts/sync_historical_ais.ps1 first."
    }
    $quality = Get-Content -LiteralPath $AisQuality -Raw | ConvertFrom-Json
    if (-not $StartUtc) { $StartUtc = [string]$quality.time_start_utc }
    if (-not $EndUtc) { $EndUtc = [string]$quality.time_end_utc }
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonPath -m espada.cli sar-discover `
    --start $StartUtc --end $EndUtc `
    --bbox $MinLongitude $MinLatitude $MaxLongitude $MaxLatitude `
    --target $TargetLongitude $TargetLatitude `
    --out $OutputDirectory
exit $LASTEXITCODE
