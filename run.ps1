$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw '가상환경이 없습니다. README의 개발 환경 설치 절차를 먼저 실행하세요.'
}
Start-Process -FilePath $taskPython -ArgumentList 'main.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
