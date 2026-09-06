param(
    [Parameter(Mandatory = $true)][string]$SarImage,
    [Parameter(Mandatory = $true)][string]$AisCsv,
    [Parameter(Mandatory = $true)][string]$ObservationTimeUtc,
    [Parameter(Mandatory = $true)][double]$MinLongitude,
    [Parameter(Mandatory = $true)][double]$MinLatitude,
    [Parameter(Mandatory = $true)][double]$MaxLongitude,
    [Parameter(Mandatory = $true)][double]$MaxLatitude,
    [double]$AgeHours = 19.0,
    [switch]$AnalystApproved,
    [switch]$UseClassicalFallback,
    [string]$ModelCheckpoint = "",
    [string]$CalibrationPath = "",
    [int]$InferenceBatchSize = 4,
    [string]$EnvironmentCache = "",
    [string]$GpuPythonPath = "",
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
if (-not $GpuPythonPath) {
    $GpuPythonPath = Join-Path $env:USERPROFILE "ml\Scripts\python.exe"
}
$DefaultCheckpoint = Join-Path $ProjectRoot "out\ml_training_v6\sar_segmentation_best.pt"
$DefaultCalibration = Join-Path $ProjectRoot "out\ml_calibration_v6\threshold_calibration.json"
if (-not $ModelCheckpoint) { $ModelCheckpoint = $DefaultCheckpoint }
if (-not $CalibrationPath) { $CalibrationPath = $DefaultCalibration }
if (-not $UseClassicalFallback) {
    foreach ($RequiredMlPath in @($ModelCheckpoint, $CalibrationPath)) {
        if (-not (Test-Path -LiteralPath $RequiredMlPath)) {
            throw "Calibrated SAR model input is missing: $RequiredMlPath"
        }
    }
    if (-not (Test-Path -LiteralPath $GpuPythonPath)) {
        throw "The GPU PyTorch environment was not found at '$GpuPythonPath'."
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}
$caseEnvironment = Join-Path $ProjectRoot "out\case_20260825\environment.json"
$copernicusCache = Join-Path $ProjectRoot "data\cache\environment_copernicus.json"
if (-not $EnvironmentCache) {
    $EnvironmentCache = if (Test-Path -LiteralPath $caseEnvironment) { $caseEnvironment } else { $copernicusCache }
}
if (-not (Test-Path -LiteralPath $EnvironmentCache)) {
    throw "A date-matched Copernicus environment cache is missing."
}

$CaseRoot = Join-Path $ProjectRoot "out\real_case"
$SarOutput = Join-Path $CaseRoot "sar"
$DriftOutput = Join-Path $CaseRoot "drift"
$AisOutput = Join-Path $CaseRoot "ais"
$RankingOutput = Join-Path $CaseRoot "ranking"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"

$SarArguments = @(
    "-m", "espada.cli", "sar",
    "--input", $ResolvedSar.Path,
    "--out", $SarOutput,
    "--observation-time", $ObservationTimeUtc,
    "--bbox", $MinLongitude, $MinLatitude, $MaxLongitude, $MaxLatitude
)
if (-not $UseClassicalFallback) {
    $PredictionBundle = Join-Path $SarOutput "model_prediction.npz"
    & $GpuPythonPath -m espada.ml_predict `
        --input $ResolvedSar.Path `
        --checkpoint $ModelCheckpoint `
        --calibration $CalibrationPath `
        --output $PredictionBundle `
        --batch-size $InferenceBatchSize
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $SarArguments += @("--prediction-bundle", $PredictionBundle)
}
if ($AnalystApproved) { $SarArguments += "--analyst-approved" }
& $PythonPath @SarArguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$SarResultPath = Join-Path $SarOutput "sar_result.json"
$SarResult = Get-Content -LiteralPath $SarResultPath -Raw | ConvertFrom-Json
if ($SarResult.status -eq "NO_DETECTION") {
    Write-Output "NO SLICK CANDIDATE: attribution stopped correctly. Review:"
    Write-Output (Join-Path $SarOutput "sar_segmentation_overview.png")
    exit 0
}
if (-not $AnalystApproved) {
    Write-Output "SAR CANDIDATE READY. Review:"
    Write-Output (Join-Path $SarOutput "sar_segmentation_overview.png")
    Write-Output "If the outlined region is a credible slick, rerun this command with -AnalystApproved."
    exit 0
}
& $PythonPath -m espada.cli ais --input $ResolvedAis --out $AisOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli case-check `
    --slick (Join-Path $SarOutput "slick_observation.geojson") `
    --environment-cache $EnvironmentCache `
    --ais (Join-Path $AisOutput "ais_normalized.csv") `
    --age-hours $AgeHours `
    --out (Join-Path $CaseRoot "case_alignment.json")
if ($LASTEXITCODE -ne 0) {
    Write-Error "Case stopped: SAR, environment and AIS dates are not aligned. Review case_alignment.json."
    exit $LASTEXITCODE
}
& $PythonPath -m espada.cli slick `
    --input (Join-Path $SarOutput "slick_observation.geojson") `
    --environment-cache $EnvironmentCache --out $DriftOutput --age-hours $AgeHours
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli rank-ais `
    --ais (Join-Path $AisOutput "ais_normalized.csv") --case $DriftOutput --out $RankingOutput
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "REAL CASE COMPLETE. Review:"
Write-Output (Join-Path $RankingOutput "candidates.json")
Write-Output (Join-Path $RankingOutput "attribution_map.png")
