param(
    [double]$MinLongitude = 70.8,
    [double]$MinLatitude = 17.8,
    [double]$MaxLongitude = 73.0,
    [double]$MaxLatitude = 20.0,
    [string]$StartUtc = "",
    [string]$EndUtc = "",
    [string]$CasePath = "",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
. (Join-Path $PSScriptRoot "_load_env.ps1")
Import-EspadaEnv -Path (Join-Path $ProjectRoot ".env")
if (-not $env:GFW_API_ACCESS_TOKEN) {
    throw "GFW_API_ACCESS_TOKEN is empty. Add a Global Fishing Watch API token to the private .env file."
}
if (-not $EndUtc) {
    $EndUtc = [DateTime]::UtcNow.AddDays(-5).ToString("yyyy-MM-ddTHH:mm:ssZ")
}
if (-not $StartUtc) {
    $StartUtc = [DateTime]::Parse($EndUtc).ToUniversalTime().AddHours(-24).ToString("yyyy-MM-ddTHH:mm:ssZ")
}
if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { $PythonPath = $candidate; break }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
$arguments = @(
    "-m", "espada.cli", "ais-history",
    "--bbox", $MinLongitude, $MinLatitude, $MaxLongitude, $MaxLatitude,
    "--start", $StartUtc,
    "--end", $EndUtc,
    "--out", (Join-Path $ProjectRoot "out\historical_ais")
)
if ($CasePath) {
    $resolvedCase = Resolve-Path -LiteralPath $CasePath -ErrorAction Stop
    $arguments += @(
        "--case", $resolvedCase,
        "--rank-out", (Join-Path $ProjectRoot "out\historical_ais_ranking")
    )
}
& $PythonPath @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "Historical AIS download complete. Review:"
Write-Output (Join-Path $ProjectRoot "out\historical_ais\historical_ais_status.json")
