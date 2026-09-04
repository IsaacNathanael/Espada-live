param(
    [double]$MinLongitude = 72.4,
    [double]$MinLatitude = 18.5,
    [double]$MaxLongitude = 73.2,
    [double]$MaxLatitude = 19.5,
    [int]$DurationSeconds = 300,
    [int]$WindowHours = 72,
    [string]$CasePath = "",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
. (Join-Path $PSScriptRoot "_load_env.ps1")
Import-EspadaEnv -Path (Join-Path $ProjectRoot ".env")
if (-not $env:AISSTREAM_API_KEY) {
    throw "AISSTREAM_API_KEY is empty. Add it to the private .env file or set it in this terminal."
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
    "-m", "espada.cli", "ais-live",
    "--bbox", $MinLongitude, $MinLatitude, $MaxLongitude, $MaxLatitude,
    "--duration-seconds", $DurationSeconds,
    "--window-hours", $WindowHours,
    "--cache", (Join-Path $ProjectRoot "data\cache\ais_live.csv"),
    "--out", (Join-Path $ProjectRoot "out\live_ais")
)
if ($CasePath) {
    $resolvedCase = Resolve-Path -LiteralPath $CasePath -ErrorAction Stop
    $arguments += @(
        "--case", $resolvedCase,
        "--rank-out", (Join-Path $ProjectRoot "out\live_ais_ranking")
    )
}
& $PythonPath @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "Live AIS capture complete. Review:"
Write-Output (Join-Path $ProjectRoot "out\live_ais\live_run_result.json")
