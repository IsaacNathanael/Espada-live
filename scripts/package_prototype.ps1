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
$ValidationShowcase = Join-Path $ProjectRoot "out\validation_showcase\real_world_validation.html"
$ClassicDashboard = Join-Path $ProjectRoot "out\demo\dashboard.html"
foreach ($RequiredArtifact in @($Dossier, $Scorecard, $ValidationShowcase, $ClassicDashboard)) {
    if (-not (Test-Path -LiteralPath $RequiredArtifact)) {
        throw "Required prototype evidence is missing: $RequiredArtifact"
    }
}

$PackageDirectory = Join-Path $ProjectRoot "docs\prototype"
$Dashboard = Join-Path $PackageDirectory "index.html"
$PackagedDossier = Join-Path $PackageDirectory "evidence_dossier.html"
$PackagedScorecard = Join-Path $PackageDirectory "validation_scorecard.html"
$PackagedValidationShowcase = Join-Path $PackageDirectory "real_world_validation.html"
$PackagedClassicDashboard = Join-Path $PackageDirectory "dashboard.html"
New-Item -ItemType Directory -Force -Path $PackageDirectory | Out-Null

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

$Manifest = [ordered]@{
    status = "PASS"
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    entrypoint = "index.html"
    classic_dashboard = "dashboard.html"
    dossier = "evidence_dossier.html"
    validation_scorecard = "validation_scorecard.html"
    real_world_validation = "real_world_validation.html"
    operation = "portable saved-evidence replay"
    requirements = "modern browser only"
    limitation = "The packaged Run button replays validated saved evidence; fresh ranking requires the local Python engine."
}
$Manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PackageDirectory "manifest.json") -Encoding UTF8

Write-Host "PORTABLE ESPADA PROTOTYPE READY" -ForegroundColor Green
Write-Host $Dashboard -ForegroundColor Yellow
