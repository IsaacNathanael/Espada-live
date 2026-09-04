param(
    [Parameter(Mandatory = $true)][string]$SarImage,
    [Parameter(Mandatory = $true)][string]$AisCsv,
    [Parameter(Mandatory = $true)][string]$ObservationTimeUtc,
    [Parameter(Mandatory = $true)][double]$MinLongitude,
    [Parameter(Mandatory = $true)][double]$MinLatitude,
    [Parameter(Mandatory = $true)][double]$MaxLongitude,
    [Parameter(Mandatory = $true)][double]$MaxLatitude,
    [double]$AgeHours = 19.0,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
$ResolvedSar = Resolve-Path -LiteralPath $SarImage -ErrorAction Stop
$ResolvedAis = Resolve-Path -LiteralPath $AisCsv -ErrorAction Stop
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
$copernicusCache = Join-Path $ProjectRoot "data\cache\environment_copernicus.json"
if (-not (Test-Path -LiteralPath $copernicusCache)) {
    throw "Copernicus cache is missing. Run scripts/sync_copernicus.ps1 first."
}

$CaseRoot = Join-Path $ProjectRoot "out\real_case"
$SarOutput = Join-Path $CaseRoot "sar"
$DriftOutput = Join-Path $CaseRoot "drift"
$AisOutput = Join-Path $CaseRoot "ais"
$RankingOutput = Join-Path $CaseRoot "ranking"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"

& $PythonPath -m espada.cli sar --input $ResolvedSar --out $SarOutput `
    --observation-time $ObservationTimeUtc `
    --bbox $MinLongitude $MinLatitude $MaxLongitude $MaxLatitude
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli ais --input $ResolvedAis --out $AisOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli case-check `
    --slick (Join-Path $SarOutput "slick_observation.geojson") `
    --environment-cache $copernicusCache `
    --ais (Join-Path $AisOutput "ais_normalized.csv") `
    --age-hours $AgeHours `
    --out (Join-Path $CaseRoot "case_alignment.json")
if ($LASTEXITCODE -ne 0) {
    Write-Error "Case stopped: SAR, environment and AIS dates are not aligned. Review case_alignment.json."
    exit $LASTEXITCODE
}
& $PythonPath -m espada.cli slick `
    --input (Join-Path $SarOutput "slick_observation.geojson") `
    --environment-cache $copernicusCache --out $DriftOutput --age-hours $AgeHours
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli rank-ais `
    --ais (Join-Path $AisOutput "ais_normalized.csv") --case $DriftOutput --out $RankingOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "REAL CASE COMPLETE. Review:"
Write-Output (Join-Path $RankingOutput "candidates.json")
Write-Output (Join-Path $RankingOutput "attribution_map.png")
