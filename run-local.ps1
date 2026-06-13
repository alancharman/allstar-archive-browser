$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sampleRoot = Join-Path $repoRoot "sample-archive\67146"
$pythonExe = Join-Path $repoRoot ".venv-local\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw ".venv-local is missing. Create it first with: python -m venv .venv-local; .\.venv-local\Scripts\python -m pip install -r requirements.txt"
}

if (-not (Test-Path $sampleRoot)) {
    throw "Sample archive folder not found at $sampleRoot"
}

$env:ARCHIVE_ROOT = $sampleRoot
$env:BIND_PORT = "5002"

Set-Location $repoRoot
& $pythonExe archive_browser.py
