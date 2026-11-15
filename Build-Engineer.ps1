$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    py -3 -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3 could not create the virtual environment' }
}
& $python -m pip install -e '.[package,radio,vision]'
if ($LASTEXITCODE -ne 0) { throw 'Packaging dependencies could not be installed' }
$buildOriginalPath = $env:PATH
try {
    # Avoid bundling unrelated DLLs from tools on the build machine's PATH.
    # In particular Windows ICU must resolve to the OS library, not a Poppler copy.
    $env:PATH = "$(Join-Path $PSScriptRoot '.venv\Scripts');$env:WINDIR\System32;$env:WINDIR"
    & $python -m PyInstaller --noconfirm --clean --windowed --onedir --name 'ACC-Race-Engineer' --collect-data acc_engineer --collect-all faster_whisper --collect-all dxcam --hidden-import acc_engineer.app --hidden-import acc_engineer.tools --hidden-import acc_engineer.vision --hidden-import acc_engineer.controller --hidden-import acc_engineer.desktop.shell --hidden-import acc_engineer.desktop.bridge --hidden-import acc_engineer.desktop.pages package_launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
} finally {
    $env:PATH = $buildOriginalPath
}
Write-Host 'Native Windows build created in dist\ACC-Race-Engineer. Validate on a clean Windows machine before distribution.'
