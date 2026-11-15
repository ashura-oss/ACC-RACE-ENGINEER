$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    & '.venv\Scripts\python.exe' -m pip install -e '.[radio]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
}
Start-Process 'http://localhost:8000'
& '.venv\Scripts\python.exe' -m acc_engineer
