$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$CyBase = [Environment]::GetFolderPath('LocalApplicationData')
$CyData = if ($env:CY_EDIT_LONGVIDEO_DATA) { $env:CY_EDIT_LONGVIDEO_DATA } elseif ($env:LONG_VIDEO_EDITOR_DATA) { $env:LONG_VIDEO_EDITOR_DATA } else { Join-Path $CyBase 'cy-edit-longvideo' }
$CyLegacy = Join-Path $CyBase 'long-video-editor'
if (!$env:CY_EDIT_LONGVIDEO_DATA -and !$env:LONG_VIDEO_EDITOR_DATA -and !(Test-Path -LiteralPath $CyData) -and (Test-Path -LiteralPath $CyLegacy -PathType Container)) { $CyData = $CyLegacy }
for ($i = 0; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq '--state-dir') { $i++; $CyData = $args[$i] }
    elseif ($args[$i] -like '--state-dir=*') { $CyData = $args[$i].Substring(12) }
}
$CyPointer = Join-Path $CyData 'python-path.txt'
$CyPython = if (Test-Path -LiteralPath $CyPointer) { (Get-Content -LiteralPath $CyPointer -Raw -Encoding UTF8).Trim() } else { '' }
if (!$CyPython -and (Test-Path -LiteralPath (Join-Path $CyData 'runtime.json'))) {
    $CyConfig = Get-Content -LiteralPath (Join-Path $CyData 'runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $CyPython = $CyConfig.python
}
if (!$CyPython -or !(Test-Path -LiteralPath $CyPython -PathType Leaf)) {
    $CySetup = @('--state-dir', $CyData, '--backend', 'none')
    if ($args -contains '--offline') { $CySetup += '--offline' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'scripts\bootstrap.ps1') @CySetup
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $CyPython = (Get-Content -LiteralPath $CyPointer -Raw -Encoding UTF8).Trim()
}
$env:CY_EDIT_LONGVIDEO_DATA = $CyData
& $CyPython (Join-Path $PSScriptRoot 'scripts\editor.py') @args
exit $LASTEXITCODE
