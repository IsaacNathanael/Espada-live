param(
    [string]$StablePythonPath = "",
    [string]$DatasetId = "cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
$CmemsPython = Join-Path $WorkspaceRoot "work\envs\cmems-py\Scripts\python.exe"
$WindCache = Join-Path $ProjectRoot "data\cache\environment_latest.json"
$RawFile = Join-Path $ProjectRoot "data\raw\copernicus_currents_latest.nc"
$OutputCache = Join-Path $ProjectRoot "data\cache\environment_copernicus.json"

if (-not (Test-Path -LiteralPath $CmemsPython)) {
    throw "Copernicus tools are not ready. First run: powershell -ExecutionPolicy Bypass -File .\scripts\setup_copernicus.ps1"
}
if (-not (Test-Path -LiteralPath $WindCache)) {
    throw "Wind cache is missing. First run: powershell -ExecutionPolicy Bypass -File .\scripts\sync_environment.ps1"
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

$windPayload = Get-Content -LiteralPath $WindCache -Raw | ConvertFrom-Json
$samples = @($windPayload.samples)
if ($samples.Count -lt 2) { throw "The wind cache needs at least two timestamps." }
$StartTime = [string]$samples[0].time_utc
$EndTime = [string]$samples[$samples.Count - 1].time_utc

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
Write-Output "Downloading a small surface-current subset from Copernicus Marine..."
& $CmemsPython -m espada.copernicus download `
    --start $StartTime `
    --end $EndTime `
    --dataset-id $DatasetId `
    --out $RawFile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "Normalizing Copernicus currents and cached wind for Espada..."
& $StablePythonPath -m espada.copernicus normalize `
    --raw $RawFile `
    --cache $OutputCache `
    --wind-cache $WindCache `
    --dataset-id $DatasetId
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
& $StablePythonPath -m espada.cli environment `
    --mode cache `
    --cache $OutputCache `
    --out (Join-Path $ProjectRoot "out\copernicus_environment")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "Copernicus connection verified. The next full run will prefer this cache."
