param(
    [string]$StartUtc = "",
    [string]$EndUtc = "",
    [double]$Latitude = 18.7167,
    [double]$Longitude = 71.45,
    [double]$MinLongitude = 70.8,
    [double]$MinLatitude = 17.8,
    [double]$MaxLongitude = 73.0,
    [double]$MaxLatitude = 20.0,
    [string]$DatasetId = "cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i",
    [string]$StablePythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
$CmemsPython = Join-Path $WorkspaceRoot "work\envs\cmems-py\Scripts\python.exe"
$AisQuality = Join-Path $ProjectRoot "out\historical_ais\ais_quality.json"
$WindCache = Join-Path $ProjectRoot "data\cache\wind_historical.json"
$RawFile = Join-Path $ProjectRoot "data\raw\copernicus_currents_historical.nc"
$OutputCache = Join-Path $ProjectRoot "data\cache\environment_historical.json"
$OutputDirectory = Join-Path $ProjectRoot "out\historical_environment"

if (-not (Test-Path -LiteralPath $CmemsPython)) {
    throw "Copernicus tools are not ready. Run scripts/setup_copernicus.ps1 first."
}
if (-not $StablePythonPath) {
    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $StablePythonPath = $candidate; break }
    }
}
if (-not $StablePythonPath -or -not (Test-Path -LiteralPath $StablePythonPath)) {
    throw "The stable Espada Python environment was not found."
}
if (-not $StartUtc -or -not $EndUtc) {
    if (-not (Test-Path -LiteralPath $AisQuality)) {
        throw "Historical AIS quality report is missing. Run scripts/sync_historical_ais.ps1 first."
    }
    $quality = Get-Content -LiteralPath $AisQuality -Raw | ConvertFrom-Json
    if (-not $StartUtc) {
        $StartUtc = [DateTime]::Parse([string]$quality.time_start_utc).ToUniversalTime().AddHours(-6).ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    if (-not $EndUtc) {
        $EndUtc = [DateTime]::Parse([string]$quality.time_end_utc).ToUniversalTime().AddHours(6).ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"

Write-Output "Downloading date-matched historical wind..."
& $StablePythonPath -m espada.cli wind-history `
    --start $StartUtc --end $EndUtc `
    --latitude $Latitude --longitude $Longitude `
    --cache $WindCache --out $OutputDirectory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "Downloading date-matched Copernicus surface currents..."
& $CmemsPython -m espada.copernicus download `
    --start $StartUtc --end $EndUtc `
    --dataset-id $DatasetId `
    --min-lon $MinLongitude --max-lon $MaxLongitude `
    --min-lat $MinLatitude --max-lat $MaxLatitude `
    --out $RawFile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "Combining currents and wind into one case cache..."
& $StablePythonPath -m espada.copernicus normalize `
    --raw $RawFile --cache $OutputCache --wind-cache $WindCache `
    --latitude $Latitude --longitude $Longitude --dataset-id $DatasetId
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $StablePythonPath -m espada.cli environment `
    --mode cache --cache $OutputCache --out $OutputDirectory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "DATE-MATCHED ENVIRONMENT READY. Review:"
Write-Output (Join-Path $OutputDirectory "environment_status.json")
