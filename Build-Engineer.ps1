$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    py -3 -m venv .venv
}
& $python -m pip install -e '.[package,radio,vision]'
if ($LASTEXITCODE -ne 0) { throw 'Packaging dependencies could not be installed' }
& $python -m PyInstaller --noconfirm --clean --onedir --name 'ACC-Race-Engineer' --collect-data acc_engineer --collect-all faster_whisper --collect-all dxcam --hidden-import acc_engineer.app --hidden-import acc_engineer.tools --hidden-import acc_engineer.vision --hidden-import acc_engineer.controller package_launcher.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
Write-Host 'Build created in dist\ACC-Race-Engineer. This optional package still needs clean-machine validation.'
