param([string]$PythonPath = "")
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
if (-not $PythonPath) {
    foreach ($candidate in @((Join-Path $ProjectRoot ".venv\Scripts\python.exe"),(Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe"))) {
        if (Test-Path -LiteralPath $candidate) { $PythonPath = $candidate; break }
    }
}
if (-not $PythonPath) { throw "The Espada Python environment was not found." }
$CaseRoot = Join-Path $ProjectRoot "out\wakashio"
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
& $PythonPath -m espada.time_window `
    --slick (Join-Path $CaseRoot "prepared\slick.geojson") `
    --environment-cache (Join-Path $ProjectRoot "data\cache\environment_historical.json") `
    --candidates (Join-Path $CaseRoot "prepared\blinded_candidates.csv") `
    --out (Join-Path $CaseRoot "time_search")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "RELEASE-TIME SEARCH MILESTONE COMPLETE" -ForegroundColor Green
Write-Host (Join-Path $CaseRoot "time_search\release_time_search.html")
