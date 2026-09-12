param(
    [Parameter(Mandatory = $true)][string]$SlickGeoJson,
    [Parameter(Mandatory = $true)][string]$AisCsv,
    [Parameter(Mandatory = $true)][string]$EnvironmentCache,
    [double]$AgeHours = 19.0,
    [string]$OutputDirectory = "",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
$ResolvedSlick = Resolve-Path -LiteralPath $SlickGeoJson -ErrorAction Stop
$ResolvedAis = Resolve-Path -LiteralPath $AisCsv -ErrorAction Stop
$ResolvedEnvironment = Resolve-Path -LiteralPath $EnvironmentCache -ErrorAction Stop

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
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $ProjectRoot "out\approved_slick_case" }

$CaseRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$AisOutput = Join-Path $CaseRoot "ais"
$DriftOutput = Join-Path $CaseRoot "drift"
$RankingOutput = Join-Path $CaseRoot "ranking"
$DossierOutput = Join-Path $CaseRoot "dossier"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"

Write-Host "1/5 Auditing AIS data..." -ForegroundColor Cyan
& $PythonPath -m espada.cli ais --input $ResolvedAis.Path --out $AisOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "2/5 Checking incident-time alignment..." -ForegroundColor Cyan
& $PythonPath -m espada.cli case-check `
    --slick $ResolvedSlick.Path `
    --environment-cache $ResolvedEnvironment.Path `
    --ais (Join-Path $AisOutput "ais_normalized.csv") `
    --age-hours $AgeHours `
    --out (Join-Path $CaseRoot "case_alignment.json")
if ($LASTEXITCODE -ne 0) {
    Write-Error "SAFE STOP: the inputs do not cover the same incident window."
    exit $LASTEXITCODE
}

Write-Host "3/5 Reconstructing the probable release zone..." -ForegroundColor Cyan
& $PythonPath -m espada.cli slick `
    --input $ResolvedSlick.Path `
    --environment-cache $ResolvedEnvironment.Path `
    --out $DriftOutput `
    --age-hours $AgeHours
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "4/5 Ranking and forward-verifying vessel tracks..." -ForegroundColor Cyan
& $PythonPath -m espada.cli rank-ais `
    --ais (Join-Path $AisOutput "ais_normalized.csv") `
    --case $DriftOutput `
    --out $RankingOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "5/5 Building the portable evidence dossier..." -ForegroundColor Cyan
& $PythonPath -m espada.cli dossier --case-root $CaseRoot --out $DossierOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "" 
Write-Host "APPROVED-SLICK CASE COMPLETE" -ForegroundColor Green
Write-Host "Open:" -ForegroundColor Yellow
Write-Host (Join-Path $DossierOutput "evidence_dossier.html")
