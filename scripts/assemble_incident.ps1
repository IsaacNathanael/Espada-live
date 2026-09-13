param(
    [Parameter(Mandatory = $true)][string]$CaseId,
    [Parameter(Mandatory = $true)][string]$ObservationTimeUtc,
    [Parameter(Mandatory = $true)][double]$MinLongitude,
    [Parameter(Mandatory = $true)][double]$MinLatitude,
    [Parameter(Mandatory = $true)][double]$MaxLongitude,
    [Parameter(Mandatory = $true)][double]$MaxLatitude,
    [double]$TargetLongitude = [double]::NaN,
    [double]$TargetLatitude = [double]::NaN,
    [double]$SearchWindowHours = 36,
    [double]$MaximumSlickAgeHours = 24,
    [string]$ExistingAisCsv = "",
    [string]$DatasetId = "",
    [int]$Width = 1536,
    [int]$Height = 1400,
    [string]$OutputDirectory = "",
    [string]$PythonPath = "",
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)

if ($CaseId -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$') {
    throw "CaseId must contain 2-64 letters, numbers, underscores or hyphens."
}
if (-not (-180 -le $MinLongitude -and $MinLongitude -lt $MaxLongitude -and $MaxLongitude -le 180 -and -90 -le $MinLatitude -and $MinLatitude -lt $MaxLatitude -and $MaxLatitude -le 90)) {
    throw "The bounding box is invalid."
}
if (($MaxLongitude - $MinLongitude) -gt 2.5 -or ($MaxLatitude - $MinLatitude) -gt 2.5) {
    throw "The bounding box is too large for a useful synchronous SAR subset; keep each span at or below 2.5 degrees."
}
if ($SearchWindowHours -le 0 -or $MaximumSlickAgeHours -le 0) {
    throw "SearchWindowHours and MaximumSlickAgeHours must be positive."
}
if ($ObservationTimeUtc -notmatch '(Z|[+-]\d{2}:\d{2})$') {
    throw "ObservationTimeUtc must include Z or an explicit UTC offset."
}
try { $RequestedTime = [DateTimeOffset]::Parse($ObservationTimeUtc).ToUniversalTime() }
catch { throw "ObservationTimeUtc must be a valid timezone-aware UTC timestamp." }
if ([double]::IsNaN($TargetLongitude)) { $TargetLongitude = ($MinLongitude + $MaxLongitude) / 2 }
if ([double]::IsNaN($TargetLatitude)) { $TargetLatitude = ($MinLatitude + $MaxLatitude) / 2 }
if ($TargetLongitude -lt $MinLongitude -or $TargetLongitude -gt $MaxLongitude -or $TargetLatitude -lt $MinLatitude -or $TargetLatitude -gt $MaxLatitude) {
    throw "The target point must be inside the bounding box."
}
if ($Width -lt 32 -or $Width -gt 2500 -or $Height -lt 32 -or $Height -gt 2500) {
    throw "Width and Height must each be between 32 and 2500 pixels."
}

$CaseRoot = if ($OutputDirectory) { [System.IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $ProjectRoot ("out\incidents\" + $CaseId) }
$CatalogDirectory = Join-Path $CaseRoot "sentinel_catalog"
$SarDirectory = Join-Path $CaseRoot "sar_input"
$AisDirectory = Join-Path $CaseRoot "ais"
$EnvironmentDirectory = Join-Path $CaseRoot "environment"
$SearchStart = $RequestedTime.AddHours(-$SearchWindowHours).ToString("yyyy-MM-ddTHH:mm:ssZ")
$SearchEnd = $RequestedTime.AddHours($SearchWindowHours).ToString("yyyy-MM-ddTHH:mm:ssZ")
$Bbox = @($MinLongitude, $MinLatitude, $MaxLongitude, $MaxLatitude)

New-Item -ItemType Directory -Force -Path $CaseRoot | Out-Null
$Plan = [ordered]@{
    schema_version = 1
    case_id = $CaseId
    status = if ($PlanOnly) { "PLAN_ONLY" } else { "ASSEMBLING" }
    requested_observation_time_utc = $RequestedTime.ToString("yyyy-MM-ddTHH:mm:ssZ")
    target = @($TargetLongitude, $TargetLatitude)
    bbox = $Bbox
    sentinel_search_window_utc = @($SearchStart, $SearchEnd)
    maximum_slick_age_hours = $MaximumSlickAgeHours
    ais_source = if ($ExistingAisCsv) { "provided CSV" } else { "Global Fishing Watch historical vessel presence" }
    providers = @(
        "Copernicus Data Space Sentinel-1 GRD catalogue and Process API",
        "Copernicus Marine surface currents",
        "Open-Meteo historical forecast wind",
        $(if ($ExistingAisCsv) { "user-provided AIS CSV" } else { "Global Fishing Watch vessel presence" })
    )
    output_directory = $CaseRoot
}
$PlanPath = Join-Path $CaseRoot "assembly_plan.json"
$Plan | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $PlanPath -Encoding UTF8
if ($PlanOnly) {
    Write-Host "INCIDENT ASSEMBLY PLAN READY - no network calls were made." -ForegroundColor Green
    Write-Output $PlanPath
    exit 0
}

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

. (Join-Path $PSScriptRoot "_load_env.ps1")
Import-EspadaEnv -Path (Join-Path $ProjectRoot ".env")
if (-not $env:CDSE_CLIENT_ID -or -not $env:CDSE_CLIENT_SECRET) {
    throw "CDSE_CLIENT_ID and CDSE_CLIENT_SECRET are required in the private .env file."
}
if (-not $ExistingAisCsv -and -not $env:GFW_API_ACCESS_TOKEN) {
    throw "GFW_API_ACCESS_TOKEN is required in .env unless ExistingAisCsv is supplied."
}
$ResolvedExistingAis = ""
if ($ExistingAisCsv) {
    $ResolvedExistingAis = (Resolve-Path -LiteralPath $ExistingAisCsv -ErrorAction Stop).Path
}
$CmemsPython = Join-Path $WorkspaceRoot "work\envs\cmems-py\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $CmemsPython)) {
    throw "Copernicus Marine tools are missing. Run scripts/setup_copernicus.ps1 first."
}
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"

Write-Host "1/5 Discovering a Sentinel-1 scene that covers the target..." -ForegroundColor Cyan
& $PythonPath -m espada.cli sar-discover `
    --start $SearchStart --end $SearchEnd --bbox @Bbox `
    --target $TargetLongitude $TargetLatitude --out $CatalogDirectory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$CatalogPath = Join-Path $CatalogDirectory "sentinel1_catalog.json"
$Catalog = Get-Content -LiteralPath $CatalogPath -Raw | ConvertFrom-Json
if ($Catalog.status -ne "PASS") {
    Write-Warning ("SAFE STOP: no suitable target-covering Sentinel-1 scene. {0}" -f $Catalog.recommendation)
    exit 2
}
$AcquisitionTime = [DateTimeOffset]::Parse([string]$Catalog.recommended_scene.acquisition_time_utc).ToUniversalTime()
$AcquisitionUtc = $AcquisitionTime.ToString("yyyy-MM-ddTHH:mm:ssZ")
$EvidenceStart = $AcquisitionTime.AddHours(-$MaximumSlickAgeHours - 2).ToString("yyyy-MM-ddTHH:mm:ssZ")
$EvidenceEnd = $AcquisitionTime.AddHours(2).ToString("yyyy-MM-ddTHH:mm:ssZ")

Write-Host "2/5 Collecting vessel evidence for the aligned time window..." -ForegroundColor Cyan
if ($ExistingAisCsv) {
    $AisCsv = $ResolvedExistingAis
} else {
    if ($AcquisitionTime -gt [DateTimeOffset]::UtcNow.AddHours(-96)) {
        throw "Global Fishing Watch historical data is delayed by about 96 hours. Supply -ExistingAisCsv for a recent incident."
    }
    & $PythonPath -m espada.cli ais-history `
        --bbox @Bbox --start $EvidenceStart --end $EvidenceEnd --out $AisDirectory
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $AisCsv = Join-Path $AisDirectory "ais_normalized.csv"
}

Write-Host "3/5 Downloading date-matched wind and ocean currents..." -ForegroundColor Cyan
$WindCache = Join-Path $EnvironmentDirectory "wind.json"
$CurrentFile = Join-Path $EnvironmentDirectory "currents.nc"
$EnvironmentCache = Join-Path $EnvironmentDirectory "environment.json"
& $PythonPath -m espada.cli wind-history `
    --start $EvidenceStart --end $EvidenceEnd --latitude $TargetLatitude --longitude $TargetLongitude `
    --cache $WindCache --out $EnvironmentDirectory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not $DatasetId) {
    $DatasetId = if ($AcquisitionTime -lt [DateTimeOffset]::UtcNow.AddDays(-60)) {
        "cmems_mod_glo_phy_my_0.083deg_P1D-m"
    } else {
        "cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i"
    }
}
$CurrentStart = $EvidenceStart
$CurrentEnd = $EvidenceEnd
if ($DatasetId -match 'P1D') {
    # Daily datasets timestamp each field at midnight. Expand to complete UTC
    # days so a partial-day request cannot collapse to only one time sample.
    $CurrentStart = $AcquisitionTime.AddHours(-$MaximumSlickAgeHours - 2).UtcDateTime.Date.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $CurrentEnd = $AcquisitionTime.UtcDateTime.Date.AddDays(1).ToString("yyyy-MM-ddTHH:mm:ssZ")
}
& $CmemsPython -m espada.copernicus download `
    --start $CurrentStart --end $CurrentEnd --dataset-id $DatasetId `
    --min-lon $MinLongitude --max-lon $MaxLongitude --min-lat $MinLatitude --max-lat $MaxLatitude `
    --out $CurrentFile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.copernicus normalize `
    --raw $CurrentFile --cache $EnvironmentCache --wind-cache $WindCache `
    --latitude $TargetLatitude --longitude $TargetLongitude --dataset-id $DatasetId
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli environment --mode cache --cache $EnvironmentCache --out $EnvironmentDirectory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "4/5 Downloading a calibrated Sentinel-1 VV subset..." -ForegroundColor Cyan
& $PythonPath -m espada.cli sar-download `
    --catalog $CatalogPath --bbox @Bbox --width $Width --height $Height --out $SarDirectory
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "5/5 Writing the executable case definition..." -ForegroundColor Cyan
$RunDirectory = Join-Path $CaseRoot "run"
$CaseDefinition = [ordered]@{
    schema_version = 1
    case_id = $CaseId
    mode = "sar"
    observation_time_utc = $AcquisitionUtc
    bbox = $Bbox
    inputs = [ordered]@{
        sar_image = Join-Path $SarDirectory "sentinel1_vv.tif"
        ais_csv = $AisCsv
        environment_cache = $EnvironmentCache
    }
    analysis = [ordered]@{
        analyst_approved = $false
        use_classical_fallback = $false
        age_hours = 0
        candidate_ages_hours = @(1.5, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 30, 36, 48, 72) | Where-Object { $_ -le $MaximumSlickAgeHours }
    }
    output_directory = $RunDirectory
}
$CaseDefinitionPath = Join-Path $CaseRoot "case.json"
$CaseDefinition | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $CaseDefinitionPath -Encoding UTF8
$Result = [ordered]@{
    status = "READY_FOR_SAR_REVIEW"
    case_id = $CaseId
    selected_scene = $Catalog.recommended_scene
    evidence_window_utc = @($EvidenceStart, $EvidenceEnd)
    current_download_window_utc = @($CurrentStart, $CurrentEnd)
    dataset_id = $DatasetId
    case_file = $CaseDefinitionPath
    quicklook = Join-Path $SarDirectory "sentinel1_vv_quicklook.png"
    next_command = "powershell -ExecutionPolicy Bypass -File .\scripts\run_case.ps1 -CaseFile `"$CaseDefinitionPath`""
}
$ResultPath = Join-Path $CaseRoot "assembly_result.json"
$Result | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $ResultPath -Encoding UTF8
Write-Host "INCIDENT INPUTS READY FOR SAR REVIEW" -ForegroundColor Green
Write-Output (Join-Path $SarDirectory "sentinel1_vv_quicklook.png")
Write-Output $CaseDefinitionPath
Write-Output $Result.next_command
