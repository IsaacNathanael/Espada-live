param(
    [Parameter(Mandatory = $true)][string]$SarImage,
    [Parameter(Mandatory = $true)][string]$AisCsv,
    [Parameter(Mandatory = $true)][string]$ObservationTimeUtc,
    [Parameter(Mandatory = $true)][double]$MinLongitude,
    [Parameter(Mandatory = $true)][double]$MinLatitude,
    [Parameter(Mandatory = $true)][double]$MaxLongitude,
    [Parameter(Mandatory = $true)][double]$MaxLatitude,
    [double]$AgeHours = 0.0,
    [double[]]$CandidateAgesHours = @(1.5, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24),
    [switch]$AnalystApproved,
    [switch]$UseClassicalFallback,
    [string]$ModelCheckpoint = "",
    [string]$CalibrationPath = "",
    [int]$InferenceBatchSize = 4,
    [string]$EnvironmentCache = "",
    [string]$SpatialCurrentGrid = "",
    [string]$OutputDirectory = "",
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
$ResolvedEnvironment = Resolve-Path -LiteralPath $EnvironmentCache -ErrorAction Stop

$CaseRoot = if ($OutputDirectory) { [System.IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $ProjectRoot "out\real_case" }
$SarOutput = Join-Path $CaseRoot "sar"
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
$ApprovedRunner = Join-Path $PSScriptRoot "run_approved_slick_case.ps1"
$ApprovedArguments = @{
    SlickGeoJson = Join-Path $SarOutput "slick_observation.geojson"
    AisCsv = $ResolvedAis.Path
    EnvironmentCache = $ResolvedEnvironment.Path
    AgeHours = $AgeHours
    CandidateAgesHours = $CandidateAgesHours
    OutputDirectory = $CaseRoot
    PythonPath = $PythonPath
}
if ($SpatialCurrentGrid) { $ApprovedArguments.SpatialCurrentGrid = $SpatialCurrentGrid }
& $ApprovedRunner @ApprovedArguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "REAL SAR-TO-DOSSIER CASE COMPLETE. Review:"
Write-Output (Join-Path $CaseRoot "dossier\evidence_dossier.html")
Write-Output (Join-Path $CaseRoot "decision\decision_gate.html")
