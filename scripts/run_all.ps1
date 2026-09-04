param([string]$PythonPath = "")
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $PythonPath = $candidate; break }
    }
}
if (-not $PythonPath) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) { $PythonPath = $pythonCommand.Source }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python environment not found."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
$cachePath = Join-Path $ProjectRoot "data\cache\environment_latest.json"
$copernicusCachePath = Join-Path $ProjectRoot "data\cache\environment_copernicus.json"

& $PythonPath -m espada.cli environment --mode auto --cache $cachePath --out (Join-Path $ProjectRoot "out\environment")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $PythonPath -m espada.cli evaluate --out (Join-Path $ProjectRoot "out\evaluation")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$preferredCachePath = if (Test-Path -LiteralPath $copernicusCachePath) { $copernicusCachePath } else { $cachePath }
$environmentMode = if (Test-Path -LiteralPath $preferredCachePath) { "cache" } else { "synthetic" }
& $PythonPath -m espada.cli environment --mode $environmentMode --cache $preferredCachePath --out (Join-Path $ProjectRoot "out\environment")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $PythonPath -m espada.cli demo --out (Join-Path $ProjectRoot "out\demo") --environment-mode $environmentMode --environment-cache $preferredCachePath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($environmentMode -eq "cache") {
    & $PythonPath -m espada.cli slick `
        --input (Join-Path $ProjectRoot "out\demo\slick_observation.geojson") `
        --environment-cache $preferredCachePath `
        --out (Join-Path $ProjectRoot "out\slick")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $PythonPath -m espada.cli sar-demo --out (Join-Path $ProjectRoot "out\sar")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonPath -m espada.cli dashboard --out (Join-Path $ProjectRoot "out\demo")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output ""
Write-Output "ESPADA COMPLETE. Open:"
Write-Output (Join-Path $ProjectRoot "out\demo\dashboard.html")
exit 0
