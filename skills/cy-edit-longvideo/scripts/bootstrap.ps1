$ErrorActionPreference = 'Stop'
$LveBase = [Environment]::GetFolderPath('LocalApplicationData')
$LveData = if ($env:CY_EDIT_LONGVIDEO_DATA) { $env:CY_EDIT_LONGVIDEO_DATA } elseif ($env:LONG_VIDEO_EDITOR_DATA) { $env:LONG_VIDEO_EDITOR_DATA } else { Join-Path $LveBase 'cy-edit-longvideo' }
$LveLegacy = Join-Path $LveBase 'long-video-editor'
if (!$env:CY_EDIT_LONGVIDEO_DATA -and !$env:LONG_VIDEO_EDITOR_DATA -and !(Test-Path -LiteralPath $LveData) -and (Test-Path -LiteralPath $LveLegacy -PathType Container)) { $LveData = $LveLegacy }
$LveOffline = $false
for ($i = 0; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq '--plan') { Write-Output 'Automatic: uv -> Python 3.12 -> private runtime -> FFmpeg -> ASR model -> setup record. This plan does not install.'; exit 0 }
    if ($args[$i] -eq '--offline') { $LveOffline = $true }
    if ($args[$i] -eq '--state-dir') { $i++; $LveData = $args[$i] }
    elseif ($args[$i] -like '--state-dir=*') { $LveData = $args[$i].Substring(12) }
}
$LveData = [IO.Path]::GetFullPath($LveData)
$LveBin = Join-Path $LveData 'bootstrap\bin'
New-Item -ItemType Directory -Force $LveBin | Out-Null
$env:CY_EDIT_LONGVIDEO_DATA = $LveData
$env:UV_PYTHON_INSTALL_DIR = Join-Path $LveData 'bootstrap\python'
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:UV_CACHE_DIR = Join-Path $LveData 'bootstrap\cache'
$LveUv = Join-Path $LveBin 'uv.exe'
if (!(Test-Path -LiteralPath $LveUv)) {
    $ExistingUv = Get-Command uv -ErrorAction SilentlyContinue
    if ($ExistingUv) { $LveUv = $ExistingUv.Source }
    else {
        if ($LveOffline) { throw 'Offline setup needs cached uv and Python; provide network or an offline bundle.' }
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $Installer = Join-Path $LveData 'bootstrap\uv-installer.ps1'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/0.13.7/install.ps1' -OutFile $Installer
        $env:UV_UNMANAGED_INSTALL = $LveBin
        $env:UV_NO_MODIFY_PATH = '1'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Installer
        if ($LASTEXITCODE -ne 0) { throw 'uv installation failed; retry the same entry after resolving network/policy.' }
    }
}
if ($LveOffline) { $LvePython = & $LveUv python find 3.12 --offline }
else {
    & $LveUv python install 3.12
    if ($LASTEXITCODE -ne 0 -and !$env:UV_PYTHON_INSTALL_MIRROR) {
        # Mainland China networks: retry the Python download through a mirror unless the user set one.
        Write-Warning 'Python download failed; retrying through a mirror.'
        $env:UV_PYTHON_INSTALL_MIRROR = 'https://registry.npmmirror.com/-/binary/python-build-standalone'
        & $LveUv python install 3.12
    }
    if ($LASTEXITCODE -ne 0) { throw 'Managed Python installation failed.' }
    $LvePython = & $LveUv python find 3.12
}
if ($LASTEXITCODE -ne 0) { throw 'Managed Python not available.' }
& $LvePython (Join-Path $PSScriptRoot 'bootstrap.py') --python $LvePython @args
exit $LASTEXITCODE
