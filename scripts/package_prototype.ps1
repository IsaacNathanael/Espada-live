param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)

if (-not $PythonPath) {
    foreach ($Candidate in @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $Candidate) {
            $PythonPath = $Candidate
            break
        }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}

$Dossier = Join-Path $ProjectRoot "out\challenge\dossier\evidence_dossier.html"
$Scorecard = Join-Path $ProjectRoot "out\system_validation\system_scorecard.html"
$ScorecardData = Join-Path $ProjectRoot "out\system_validation\system_scorecard.json"
$ValidationShowcase = Join-Path $ProjectRoot "out\validation_showcase\real_world_validation.html"
$ClassicDashboard = Join-Path $ProjectRoot "out\demo\dashboard.html"
$KnownSourceDossier = Join-Path $ProjectRoot "out\wakashio\dossier\evidence_dossier.html"
$KnownSourceData = Join-Path $ProjectRoot "out\wakashio\evaluation\historical_evaluation.json"
$AbstentionDossier = Join-Path $ProjectRoot "out\incidents\princess_empress_2023\run_official\dossier\evidence_dossier.html"
$DigitalTwinData = Join-Path $ProjectRoot "out\digital_twin\digital_twin_summary.json"
$DigitalTwinCases = Join-Path $ProjectRoot "out\digital_twin\digital_twin_cases.csv"
$DigitalTwinFigure = Join-Path $ProjectRoot "out\digital_twin\digital_twin_overview.png"
$ReplayGif = Join-Path $ProjectRoot "out\wakashio\replay\espada_forensic_replay.gif"
$ReplayFrame = Join-Path $ProjectRoot "out\wakashio\replay\final_frame.png"
$LiveCaseId = "S1D_IW_GRDH_1SDV_20260904T224745_20260904T224810_004434_008374_C3D5_COG"
$LiveCaseRoot = Join-Path $ProjectRoot "out\live_operations\analysis\$LiveCaseId"
$LiveCaseDossier = Join-Path $LiveCaseRoot "response\evidence_dossier.html"
$LiveCaseSummary = Join-Path $LiveCaseRoot "response\response_summary.json"
$LiveCaseIntegrity = Join-Path $LiveCaseRoot "response\integrity_verification.json"
$LiveAttributionMap = Join-Path $LiveCaseRoot "attribution\ranking\attribution_map.png"
$LiveReverseDrift = Join-Path $LiveCaseRoot "attribution\drift\slick_reverse_analysis.png"
$PublicSource = Join-Path $ProjectRoot "operator\public_site"
foreach ($RequiredArtifact in @($Dossier, $Scorecard, $ScorecardData, $ValidationShowcase, $ClassicDashboard, $KnownSourceDossier, $KnownSourceData, $AbstentionDossier, $DigitalTwinData, $DigitalTwinCases, $DigitalTwinFigure, $ReplayGif, $ReplayFrame, $LiveCaseDossier, $LiveCaseSummary, $LiveCaseIntegrity, $LiveAttributionMap, $LiveReverseDrift, (Join-Path $PublicSource "index.html"))) {
    if (-not (Test-Path -LiteralPath $RequiredArtifact)) {
        throw "Required prototype evidence is missing: $RequiredArtifact"
    }
}

$PackageDirectory = Join-Path $ProjectRoot "docs\prototype"
$Dashboard = Join-Path $PackageDirectory "investigate.html"
$PackagedDossier = Join-Path $PackageDirectory "evidence_dossier.html"
$PackagedScorecard = Join-Path $PackageDirectory "validation_scorecard.html"
$PackagedValidationShowcase = Join-Path $PackageDirectory "real_world_validation.html"
$PackagedClassicDashboard = Join-Path $PackageDirectory "dashboard.html"
New-Item -ItemType Directory -Force -Path $PackageDirectory | Out-Null
$AssetsDirectory = Join-Path $PackageDirectory "assets"
New-Item -ItemType Directory -Force -Path $AssetsDirectory | Out-Null

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $PythonPath -m espada.operations_dashboard `
    --project-root $ProjectRoot `
    --output $Dashboard `
    --dossier-href "evidence_dossier.html"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Copy-Item -LiteralPath $Dossier -Destination $PackagedDossier -Force
Copy-Item -LiteralPath $Scorecard -Destination $PackagedScorecard -Force
Copy-Item -LiteralPath $ValidationShowcase -Destination $PackagedValidationShowcase -Force
Copy-Item -LiteralPath $ClassicDashboard -Destination $PackagedClassicDashboard -Force
Copy-Item -LiteralPath (Join-Path $PublicSource "index.html") -Destination (Join-Path $PackageDirectory "index.html") -Force
Copy-Item -LiteralPath (Join-Path $PublicSource "evidence.html") -Destination (Join-Path $PackageDirectory "evidence.html") -Force
Copy-Item -LiteralPath (Join-Path $PublicSource "lab.html") -Destination (Join-Path $PackageDirectory "lab.html") -Force
Copy-Item -LiteralPath (Join-Path $PublicSource "judge.html") -Destination (Join-Path $PackageDirectory "judge.html") -Force
Copy-Item -LiteralPath (Join-Path $PublicSource "site.css") -Destination (Join-Path $AssetsDirectory "site.css") -Force
Copy-Item -LiteralPath (Join-Path $PublicSource "site.js") -Destination (Join-Path $AssetsDirectory "site.js") -Force
Copy-Item -LiteralPath $KnownSourceDossier -Destination (Join-Path $PackageDirectory "known_source_dossier.html") -Force
Copy-Item -LiteralPath $AbstentionDossier -Destination (Join-Path $PackageDirectory "abstention_dossier.html") -Force
Copy-Item -LiteralPath $LiveCaseDossier -Destination (Join-Path $PackageDirectory "live_case_dossier.html") -Force
Copy-Item -LiteralPath $LiveAttributionMap -Destination (Join-Path $AssetsDirectory "live_attribution_map.png") -Force
Copy-Item -LiteralPath $LiveReverseDrift -Destination (Join-Path $AssetsDirectory "live_reverse_drift.png") -Force
$PublicLiveDossier = Join-Path $PackageDirectory "live_case_dossier.html"
$LiveDossierHtml = Get-Content -Raw -LiteralPath $PublicLiveDossier
$LiveDossierHtml = $LiveDossierHtml.Replace("../attribution/ranking/attribution_map.png", "assets/live_attribution_map.png")
$LiveDossierHtml = $LiveDossierHtml.Replace("../attribution/drift/slick_reverse_analysis.png", "assets/live_reverse_drift.png")
Set-Content -LiteralPath $PublicLiveDossier -Value $LiveDossierHtml -Encoding UTF8
Copy-Item -LiteralPath $DigitalTwinFigure -Destination (Join-Path $AssetsDirectory "digital_twin_overview.png") -Force
Copy-Item -LiteralPath $ReplayGif -Destination (Join-Path $AssetsDirectory "wakashio_replay.gif") -Force
Copy-Item -LiteralPath $ReplayFrame -Destination (Join-Path $AssetsDirectory "wakashio_final_frame.png") -Force

$SitePayload = [ordered]@{
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    scorecard = Get-Content -Raw -LiteralPath $ScorecardData | ConvertFrom-Json
    knownSource = Get-Content -Raw -LiteralPath $KnownSourceData | ConvertFrom-Json
    digitalTwin = Get-Content -Raw -LiteralPath $DigitalTwinData | ConvertFrom-Json
    digitalTwinCases = @(Import-Csv -LiteralPath $DigitalTwinCases)
    liveCase = Get-Content -Raw -LiteralPath $LiveCaseSummary | ConvertFrom-Json
    integrity = Get-Content -Raw -LiteralPath $LiveCaseIntegrity | ConvertFrom-Json
}
$SiteJson = $SitePayload | ConvertTo-Json -Depth 20 -Compress
Set-Content -LiteralPath (Join-Path $AssetsDirectory "site-data.js") -Value "window.ESPADA_SITE_DATA = $SiteJson;" -Encoding UTF8

$Manifest = [ordered]@{
    status = "PASS"
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    entrypoint = "index.html"
    working_prototype = "investigate.html"
    evidence_page = "evidence.html"
    controlled_stress_lab = "lab.html"
    judge_mode = "judge.html"
    classic_dashboard = "dashboard.html"
    dossier = "evidence_dossier.html"
    validation_scorecard = "validation_scorecard.html"
    real_world_validation = "real_world_validation.html"
    operation = "multi-page public evidence demonstration with portable saved-evidence replay"
    requirements = "modern browser only"
    limitation = "The packaged Run button replays validated saved evidence; fresh ranking requires the local Python engine."
}
$Manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PackageDirectory "manifest.json") -Encoding UTF8

Write-Host "PORTABLE ESPADA PROTOTYPE READY" -ForegroundColor Green
Write-Host (Join-Path $PackageDirectory "index.html") -ForegroundColor Yellow
