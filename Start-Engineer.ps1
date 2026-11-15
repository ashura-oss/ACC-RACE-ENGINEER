$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$pythonw = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) {
    py -3 -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3 could not create the virtual environment' }
}

# Install the native UI and optional radio/vision capabilities when this checkout
# has not yet been provisioned (or its environment is missing required imports).
& $python -c "import PySide6, sounddevice, faster_whisper, dxcam, PIL"
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -e '.[radio,vision]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
}
if (-not (Test-Path -LiteralPath $pythonw)) { throw "pythonw.exe was not found at $pythonw" }

Start-Process -FilePath $pythonw -ArgumentList @('-m', 'acc_engineer') -WorkingDirectory $PSScriptRoot
