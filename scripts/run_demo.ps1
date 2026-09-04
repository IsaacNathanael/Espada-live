param(
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)

if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $PythonPath = $candidate
            break
        }
    }
}

if (-not $PythonPath) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $PythonPath = $pythonCommand.Source
    }
}

if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python environment not found. Pass -PythonPath with a prepared Python 3.11/3.12 environment."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
$outputDirectory = Join-Path $ProjectRoot "out\demo"
$openMeteoCache = Join-Path $ProjectRoot "data\cache\environment_latest.json"
$copernicusCache = Join-Path $ProjectRoot "data\cache\environment_copernicus.json"
$preferredCache = if (Test-Path -LiteralPath $copernicusCache) { $copernicusCache } else { $openMeteoCache }
$environmentMode = if (Test-Path -LiteralPath $preferredCache) { "cache" } else { "synthetic" }

& $PythonPath -m espada.cli demo `
    --out $outputDirectory `
    --environment-mode $environmentMode `
    --environment-cache $preferredCache
exit $LASTEXITCODE
