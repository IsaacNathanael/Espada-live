param(
    [switch]$RefreshSources,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
. (Join-Path $PSScriptRoot "_load_env.ps1")
Import-EspadaEnv -Path (Join-Path $ProjectRoot ".env")

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
if (-not $env:GFW_API_ACCESS_TOKEN) {
    throw "GFW_API_ACCESS_TOKEN is empty. Add it to the private .env file."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
$CaseRoot = Join-Path $ProjectRoot "out\wakashio"
$DatasetRoot = Join-Path $ProjectRoot "data\datasets\wakashio_unosat_2888"
$AisFile = Join-Path $CaseRoot "ais\ais_normalized.csv"
$EnvironmentFile = Join-Path $ProjectRoot "data\cache\environment_historical.json"
$SlickShape = Join-Path $DatasetRoot "extracted\ST2_20200806_OilSpillExtent_ReefPointeEsny.shp"

if ($RefreshSources -or -not (Test-Path -LiteralPath $SlickShape)) {
    Write-Host "1/7 Downloading the 0.9 MB official UNOSAT vector bundle..." -ForegroundColor Cyan
    & $PythonPath -m espada.historical_case download-unosat --out $DatasetRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if ($RefreshSources -or -not (Test-Path -LiteralPath $AisFile)) {
    Write-Host "2/7 Downloading delayed historical AIS evidence..." -ForegroundColor Cyan
    & $PythonPath -m espada.cli ais-history `
        --bbox 57.60 -20.55 57.85 -20.25 `
        --start 2020-08-05T00:00:00Z --end 2020-08-06T12:00:00Z `
        --out (Join-Path $CaseRoot "ais")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if ($RefreshSources -or -not (Test-Path -LiteralPath $EnvironmentFile)) {
    Write-Host "3/7 Downloading date-matched currents and wind..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "sync_case_environment.ps1") `
        -DatasetId "cmems_mod_glo_phy_my_0.083deg_P1D-m" `
        -StartUtc "2020-08-05T00:00:00Z" -EndUtc "2020-08-11T00:00:00Z" `
        -Latitude -20.40 -Longitude 57.73 `
        -MinLongitude 57.60 -MinLatitude -20.55 `
        -MaxLongitude 57.85 -MaxLatitude -20.25
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "4/7 Preparing blinded historical candidates..." -ForegroundColor Cyan
& $PythonPath -m espada.historical_case prepare `
    --unosat-shp $SlickShape --gfw-ais $AisFile --out (Join-Path $CaseRoot "prepared")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "5/7 Running reverse drift and alignment gates..." -ForegroundColor Cyan
& $PythonPath -m espada.cli case-check `
    --slick (Join-Path $CaseRoot "prepared\slick.geojson") `
    --environment-cache $EnvironmentFile `
    --ais (Join-Path $CaseRoot "prepared\blinded_candidates.csv") `
    --age-hours 1.5 --out (Join-Path $CaseRoot "case_alignment.json")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli slick `
    --input (Join-Path $CaseRoot "prepared\slick.geojson") `
    --environment-cache $EnvironmentFile --out (Join-Path $CaseRoot "drift") `
    --age-hours 1.5 --particles 4000 --members 30
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "6/7 Ranking pseudonymized candidates..." -ForegroundColor Cyan
& $PythonPath -m espada.cli rank-ais `
    --ais (Join-Path $CaseRoot "prepared\blinded_candidates.csv") `
    --case (Join-Path $CaseRoot "drift") --out (Join-Path $CaseRoot "ranking")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "7/7 Opening the sealed identity and scoring the answer..." -ForegroundColor Cyan
& $PythonPath -m espada.historical_case reveal `
    --ranking (Join-Path $CaseRoot "ranking\candidates.json") `
    --truth (Join-Path $CaseRoot "prepared\sealed_truth.json") `
    --out (Join-Path $CaseRoot "evaluation")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli replay `
    --case $CaseRoot --out (Join-Path $CaseRoot "replay") --frames 64 --fps 14
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "HISTORICAL KNOWN-SOURCE MILESTONE COMPLETE" -ForegroundColor Green
Write-Host "Open:" -ForegroundColor Yellow
Write-Host (Join-Path $CaseRoot "evaluation\historical_evaluation_report.html")
Write-Host (Join-Path $CaseRoot "replay\espada_forensic_replay.gif")
