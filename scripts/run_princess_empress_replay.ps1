param(
    [switch]$RefreshOfficialSlick
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CaseRoot = Join-Path $ProjectRoot "out\incidents\princess_empress_2023"
$SlickPath = Join-Path $CaseRoot "official_slick\wwf_possible_slick.geojson"
$AisPath = Join-Path $CaseRoot "ais\ais_normalized.csv"
$EnvironmentPath = Join-Path $CaseRoot "environment\environment.json"
$RunPath = Join-Path $CaseRoot "run_official"
$LayerUrl = "https://services1.arcgis.com/RTK5Unh1Z71JKIiR/ArcGIS/rest/services/Mindoro_Oil_Spills_Monitoring_Map_WFL1/FeatureServer/1"
$Source = "WWF Philippines possible oil slick mapping from Copernicus Sentinel-1, published in the Philippine Mindoro Oil Spills Monitoring Map"

if ($RefreshOfficialSlick -or -not (Test-Path -LiteralPath $SlickPath)) {
    Write-Host "Refreshing the published WWF Philippines slick map..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "import_arcgis_slick.ps1") `
        -LayerUrl $LayerUrl `
        -ObservationTimeUtc "2023-03-06T21:47:35Z" `
        -Source $Source `
        -Output $SlickPath `
        -Confidence 0.70 `
        -ReviewStatus "external_expert_mapping" | Out-Null
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

foreach ($RequiredInput in @($SlickPath, $AisPath, $EnvironmentPath)) {
    if (-not (Test-Path -LiteralPath $RequiredInput)) {
        throw "Missing cached case input: $RequiredInput. Reassemble the Princess Empress incident inputs first."
    }
}

Write-Host "Running the complete Princess Empress evidence pipeline..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "run_approved_slick_case.ps1") `
    -SlickGeoJson $SlickPath `
    -AisCsv $AisPath `
    -EnvironmentCache $EnvironmentPath `
    -AgeHours 0 `
    -OutputDirectory $RunPath | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& (Join-Path $PSScriptRoot "build_validation_showcase.ps1") | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$DecisionPath = Join-Path $RunPath "decision\decision_gate.json"
$DossierPath = Join-Path $RunPath "dossier\evidence_dossier.html"
$Decision = Get-Content -LiteralPath $DecisionPath -Raw | ConvertFrom-Json
$SlickDocument = Get-Content -LiteralPath $SlickPath -Raw | ConvertFrom-Json
$SlickProperties = $SlickDocument.features[0].properties
$CandidateCount = ($Decision.checks | Where-Object { $_.gate -eq "candidate_competition" }).observed
$RetainedArea = if ($null -ne $SlickProperties.retained_area_fraction) {
    $SlickProperties.retained_area_fraction
} else {
    $SlickProperties.selected_area_fraction
}
$RetainedArea = [Math]::Min([Math]::Max([double]$RetainedArea, 0.0), 1.0)
$Summary = [ordered]@{
    status = "PASS"
    case = "MT Princess Empress (2023)"
    slick_source = "WWF Philippines / Copernicus Sentinel-1 expert mapping"
    slick_regions_retained = $SlickProperties.connected_components
    mapped_area_retained = $RetainedArea
    candidates_compared = $CandidateCount
    operational_decision = $Decision.decision
    top_candidate = $Decision.candidate.blinded_id
    comparative_score = $Decision.candidate.comparative_score
    forward_error_km = $Decision.candidate.forward_error_km
    data_quality = $Decision.candidate.data_quality
    dossier = $DossierPath
    two_case_showcase = Join-Path $ProjectRoot "out\validation_showcase\real_world_validation.html"
    interpretation = "Candidate scores prioritize analyst review; they are not guilt probabilities. Abstention is a valid safety result."
}
Write-Host "PRINCESS EMPRESS REPLAY COMPLETE" -ForegroundColor Green
$Summary | ConvertTo-Json -Depth 4
