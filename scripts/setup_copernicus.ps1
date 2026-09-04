param([switch]$SkipLogin)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)
$CmemsEnvironment = Join-Path $WorkspaceRoot "work\envs\cmems-py"
$CmemsPython = Join-Path $CmemsEnvironment "Scripts\python.exe"
$CmemsCommand = Join-Path $CmemsEnvironment "Scripts\copernicusmarine.exe"

if (-not (Test-Path -LiteralPath $CmemsPython)) {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & $pyLauncher.Source -3.12 -m venv $CmemsEnvironment
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { throw "Python 3.12 was not found." }
        & $pythonCommand.Source -m venv $CmemsEnvironment
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Output "Installing the official Copernicus Marine Toolbox in an isolated environment..."
& $CmemsPython -m pip install "copernicusmarine>=2.0,<3"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipLogin) {
    Write-Output "Copernicus login will now ask for your credentials securely in this terminal."
    Write-Output "Do not paste your password into source files or chat."
    & $CmemsCommand login
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Output "Copernicus setup complete."
Write-Output "Next command: powershell -ExecutionPolicy Bypass -File .\scripts\sync_copernicus.ps1"
