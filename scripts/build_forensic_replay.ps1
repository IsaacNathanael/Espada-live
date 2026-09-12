param(
    [string]$CaseDirectory = "",
    [string]$OutputDirectory = "",
    [int]$Frames = 64,
    [int]$Fps = 14,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
if (-not $CaseDirectory) { $CaseDirectory = Join-Path $ProjectRoot "out\demo" }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $ProjectRoot "out\forensic_replay" }
if (-not $PythonPath) {
    foreach ($candidate in @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $WorkspaceRoot "work\envs\espada-py\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate) { $PythonPath = $candidate; break }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {
    throw "The Espada Python environment was not found."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".mpl-cache"
Write-Host "Rendering the ESPADA forensic replay from computed evidence..." -ForegroundColor Cyan
& $PythonPath -m espada.cli replay `
    --case ([System.IO.Path]::GetFullPath($CaseDirectory)) `
    --out ([System.IO.Path]::GetFullPath($OutputDirectory)) `
    --frames $Frames `
    --fps $Fps
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "FORENSIC REPLAY READY" -ForegroundColor Green
Write-Host (Join-Path ([System.IO.Path]::GetFullPath($OutputDirectory)) "espada_forensic_replay.gif")
