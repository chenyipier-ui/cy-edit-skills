param([Parameter(Mandatory=$true)][string]$DownloadDirectory)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
New-Item -ItemType Directory -Force $DownloadDirectory | Out-Null
$Package = Join-Path $DownloadDirectory 'vc_redist.x64.exe'
Invoke-WebRequest -UseBasicParsing -Uri 'https://aka.ms/vc14/vc_redist.x64.exe' -OutFile $Package
$Signature = Get-AuthenticodeSignature -LiteralPath $Package
if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { throw 'Microsoft runtime signature validation failed.' }
# Windows may require an actual administrator confirmation; do not bypass UAC.
$Process = Start-Process -FilePath $Package -ArgumentList '/install','/passive','/norestart' -Wait -PassThru -Verb RunAs
if ($Process.ExitCode -notin @(0,3010,1638)) { throw ('Visual C++ runtime installation failed: ' + $Process.ExitCode) }
Write-Output $Process.ExitCode
