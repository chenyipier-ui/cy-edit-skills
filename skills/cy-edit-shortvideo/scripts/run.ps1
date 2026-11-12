$ErrorActionPreference = 'Stop'
$skillRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataRoot = if ($env:CY_EDIT_DATA) { $env:CY_EDIT_DATA } else { Join-Path $env:APPDATA 'cy-edit' }
$pyPath = Join-Path $dataRoot 'venv\Scripts\python.exe'
$env:PYTHONUTF8='1'
if (-not (Test-Path $pyPath)) {
  $uvPath=(Get-Command uv -ErrorAction SilentlyContinue).Source
  if (-not $uvPath) { $uvPath=Join-Path $env:USERPROFILE '.local\bin\uv.exe' }
  if (-not (Test-Path $uvPath)) {
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    if (-not (Test-Path $uvPath)) { throw 'uv 安装未完成；检查网络后重新运行' }
  }
  New-Item -ItemType Directory -Force $dataRoot | Out-Null
  & $uvPath venv (Join-Path $dataRoot 'venv') --python 3.12 --seed
  if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 环境创建失败' }
}
& $pyPath (Join-Path $skillRoot 'editor.py') @args
exit $LASTEXITCODE
