param([int]$Port = 5000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$parkingPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $parkingPython)) {
    throw 'Create the environment first: python -m venv .venv, then .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
}
& $parkingPython app.py --port $Port
exit $LASTEXITCODE
