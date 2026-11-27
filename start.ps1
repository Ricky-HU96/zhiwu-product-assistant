$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $projectPython) {
    & $projectPython (Join-Path $PSScriptRoot 'server.py') @args
} else {
    Write-Host '未找到项目虚拟环境，将使用系统 Python。浏览器辅助读取需要安装 requirements-browser.txt。'
    python (Join-Path $PSScriptRoot 'server.py') @args
}
