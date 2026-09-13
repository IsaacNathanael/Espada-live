param(
    [Parameter(Mandatory = $true)][string]$CaseFile,
    [string]$PythonPath = "",
    [string]$GpuPythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ResolvedCaseFile = Resolve-Path -LiteralPath $CaseFile -ErrorAction Stop
$Case = Get-Content -LiteralPath $ResolvedCaseFile.Path -Raw | ConvertFrom-Json

if ([int]$Case.schema_version -ne 1) { throw "Unsupported case schema_version. Expected 1." }
$CaseId = [string]$Case.case_id
if ($CaseId -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$') {
    throw "case_id must contain 2-64 letters, numbers, underscores or hyphens."
}
$Mode = [string]$Case.mode
if ($Mode -notin @("approved_slick", "sar")) {
    throw "mode must be 'approved_slick' or 'sar'."
}

function Resolve-CasePath([object]$Value, [string]$Label) {
    $Text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($Text)) { throw "Missing case field: $Label" }
    if ([System.IO.Path]::IsPathRooted($Text)) { return [System.IO.Path]::GetFullPath($Text) }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Text))
}

$AisCsv = Resolve-CasePath $Case.inputs.ais_csv "inputs.ais_csv"
$EnvironmentCache = Resolve-CasePath $Case.inputs.environment_cache "inputs.environment_cache"
$SpatialCurrentGrid = if ($Case.inputs.spatial_current_grid) {
    Resolve-CasePath $Case.inputs.spatial_current_grid "inputs.spatial_current_grid"
} else { "" }
foreach ($RequiredPath in @($AisCsv, $EnvironmentCache)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) { throw "Required case input does not exist: $RequiredPath" }
}

$OutputDirectory = if ($Case.output_directory) {
    Resolve-CasePath $Case.output_directory "output_directory"
} else {
    Join-Path $ProjectRoot ("out\cases\" + $CaseId)
}
$AgeHours = if ($null -ne $Case.analysis.age_hours) { [double]$Case.analysis.age_hours } else { 0.0 }
$CandidateAges = if ($Case.analysis.candidate_ages_hours) {
    [double[]]$Case.analysis.candidate_ages_hours
} else {
    [double[]]@(1.5, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24)
}
if ($CandidateAges.Count -eq 0 -or ($CandidateAges | Where-Object { $_ -le 0 })) {
    throw "analysis.candidate_ages_hours must contain only positive numbers."
}

$StartedAt = [DateTime]::UtcNow.ToString("o")
Write-Host ("ESPADA CASE {0} - {1}" -f $CaseId, $Mode.ToUpperInvariant()) -ForegroundColor Cyan

if ($Mode -eq "approved_slick") {
    $SlickGeoJson = Resolve-CasePath $Case.inputs.slick_geojson "inputs.slick_geojson"
    if (-not (Test-Path -LiteralPath $SlickGeoJson)) { throw "Required case input does not exist: $SlickGeoJson" }
    $Arguments = @{
        SlickGeoJson = $SlickGeoJson
        AisCsv = $AisCsv
        EnvironmentCache = $EnvironmentCache
        AgeHours = $AgeHours
        CandidateAgesHours = $CandidateAges
        OutputDirectory = $OutputDirectory
    }
    if ($PythonPath) { $Arguments.PythonPath = $PythonPath }
    if ($SpatialCurrentGrid) { $Arguments.SpatialCurrentGrid = $SpatialCurrentGrid }
    & (Join-Path $PSScriptRoot "run_approved_slick_case.ps1") @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $InputFiles = @($SlickGeoJson, $AisCsv, $EnvironmentCache) + @($SpatialCurrentGrid | Where-Object { $_ })
} else {
    $SarImage = Resolve-CasePath $Case.inputs.sar_image "inputs.sar_image"
    if (-not (Test-Path -LiteralPath $SarImage)) { throw "Required case input does not exist: $SarImage" }
    if (-not $Case.observation_time_utc) { throw "Missing case field: observation_time_utc" }
    $BoundingBox = @($Case.bbox)
    if ($BoundingBox.Count -ne 4) { throw "bbox must contain [min longitude, min latitude, max longitude, max latitude]." }
    $Arguments = @{
        SarImage = $SarImage
        AisCsv = $AisCsv
        ObservationTimeUtc = [string]$Case.observation_time_utc
        MinLongitude = [double]$BoundingBox[0]
        MinLatitude = [double]$BoundingBox[1]
        MaxLongitude = [double]$BoundingBox[2]
        MaxLatitude = [double]$BoundingBox[3]
        EnvironmentCache = $EnvironmentCache
        AgeHours = $AgeHours
        CandidateAgesHours = $CandidateAges
        OutputDirectory = $OutputDirectory
    }
    if ($PythonPath) { $Arguments.PythonPath = $PythonPath }
    if ($GpuPythonPath) { $Arguments.GpuPythonPath = $GpuPythonPath }
    if ($SpatialCurrentGrid) { $Arguments.SpatialCurrentGrid = $SpatialCurrentGrid }
    if ($Case.analysis.analyst_approved) { $Arguments.AnalystApproved = $true }
    if ($Case.analysis.use_classical_fallback) { $Arguments.UseClassicalFallback = $true }
    if ($Case.analysis.model_checkpoint) {
        $Arguments.ModelCheckpoint = Resolve-CasePath $Case.analysis.model_checkpoint "analysis.model_checkpoint"
    }
    if ($Case.analysis.calibration_path) {
        $Arguments.CalibrationPath = Resolve-CasePath $Case.analysis.calibration_path "analysis.calibration_path"
    }
    & (Join-Path $PSScriptRoot "run_real_case.ps1") @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $InputFiles = @($SarImage, $AisCsv, $EnvironmentCache) + @($SpatialCurrentGrid | Where-Object { $_ })
}

$DossierPath = Join-Path $OutputDirectory "dossier\evidence_dossier.html"
$SarStatusPath = Join-Path $OutputDirectory "sar\sar_result.json"
$RunStatus = if (Test-Path -LiteralPath $DossierPath) { "COMPLETE" } elseif (Test-Path -LiteralPath $SarStatusPath) {
    $SarStatus = Get-Content -LiteralPath $SarStatusPath -Raw | ConvertFrom-Json
    "SAFE_STOP_" + [string]$SarStatus.status
} else { "INCOMPLETE" }
$InputEvidence = @($InputFiles | ForEach-Object {
    [ordered]@{ path = $_; sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant() }
})
$RunManifest = [ordered]@{
    schema_version = 1
    case_id = $CaseId
    mode = $Mode
    status = $RunStatus
    started_at_utc = $StartedAt
    finished_at_utc = [DateTime]::UtcNow.ToString("o")
    case_file = $ResolvedCaseFile.Path
    case_file_sha256 = (Get-FileHash -LiteralPath $ResolvedCaseFile.Path -Algorithm SHA256).Hash.ToLowerInvariant()
    inputs = $InputEvidence
    output_directory = $OutputDirectory
    dossier = if (Test-Path -LiteralPath $DossierPath) { $DossierPath } else { $null }
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$RunManifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $OutputDirectory "case_run_manifest.json") -Encoding UTF8
Write-Host ("CASE STATUS: {0}" -f $RunStatus) -ForegroundColor Green
