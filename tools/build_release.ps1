param([string]$Version = '1.0.0')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$cacheRoot = Join-Path $repoRoot '.build-cache'
$runtimeRoot = Join-Path $repoRoot 'runtime'
$binaryRoot = Join-Path $repoRoot 'bin'
$licensesRoot = Join-Path $repoRoot 'licenses'
$distRoot = Join-Path $repoRoot 'dist'
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid version.' }
$payloadVersion = (Get-Content -LiteralPath (Join-Path $repoRoot 'payload/manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json).patch_version
if ($payloadVersion -ne $Version) { throw 'Release version must match payload/manifest.json.' }
New-Item -ItemType Directory -Path $cacheRoot,$runtimeRoot,$binaryRoot,$licensesRoot,$distRoot -Force | Out-Null

function Get-CheckedDownload([string]$Url, [string]$Sha256, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Destination)) {
        Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
    }
    if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
        throw "Checksum mismatch: $Destination"
    }
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$pythonArchive = Join-Path $cacheRoot 'python-3.13.17-embed-amd64.zip'
Get-CheckedDownload 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip' '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38' $pythonArchive
Expand-Archive -LiteralPath $pythonArchive -DestinationPath $runtimeRoot -Force

$cecilArchive = Join-Path $cacheRoot 'mono.cecil.0.11.6.nupkg'
Get-CheckedDownload 'https://api.nuget.org/v3-flatcontainer/mono.cecil/0.11.6/mono.cecil.0.11.6.nupkg' 'd2a23832aaa948ba9a01acc42b5726e34c5f995958f1b30d45c0e7c70b3a72d5' $cecilArchive
Add-Type -AssemblyName System.IO.Compression.FileSystem
$package = [IO.Compression.ZipFile]::OpenRead($cecilArchive)
try {
    $dllEntry = $package.GetEntry('lib/net40/Mono.Cecil.dll')
    if (-not $dllEntry) { throw 'Mono.Cecil binary missing in package.' }
    [IO.Compression.ZipFileExtensions]::ExtractToFile($dllEntry, (Join-Path $binaryRoot 'Mono.Cecil.dll'), $true)
} finally { $package.Dispose() }
$cecilLicense = Join-Path $licensesRoot 'Mono.Cecil.txt'
if (-not (Test-Path -LiteralPath $cecilLicense)) { throw 'Tracked Mono.Cecil license is missing.' }
if ((Get-FileHash -LiteralPath $cecilLicense -Algorithm SHA256).Hash.ToLowerInvariant() -ne '13d58ecbed676511c0c4c4009bff107025e50007ff5e7901dc562700ec62509e') { throw 'Mono.Cecil license checksum mismatch.' }

$compilerPath = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (-not (Test-Path -LiteralPath $compilerPath)) { throw '.NET Framework compiler not found.' }
Push-Location -LiteralPath $repoRoot
try {
    & $compilerPath /nologo /target:exe /platform:anycpu /codepage:65001 /optimize+ /out:bin\AssemblyPatcher.exe /reference:bin\Mono.Cecil.dll /reference:System.Web.Extensions.dll src\AssemblyPatcher.cs
    if ($LASTEXITCODE -ne 0) { throw 'Assembly patcher compilation failed.' }
    & (Join-Path $runtimeRoot 'python.exe') -I -X utf8 -m py_compile patch.py
    if ($LASTEXITCODE -ne 0) { throw 'Python source verification failed.' }
} finally { Pop-Location }

$releaseName = "DEADLINE-RU-v$Version-windows-x64"
$buildStamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
$releaseRoot = Join-Path (Join-Path $cacheRoot "release-$buildStamp") $releaseName
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
foreach ($name in @('patch.py','INSTALL.cmd','RESTORE.cmd','CHECK.cmd','README.md','LICENSE','THIRD_PARTY_NOTICES.md')) {
    Copy-Item -LiteralPath (Join-Path $repoRoot $name) -Destination (Join-Path $releaseRoot $name)
}
foreach ($name in @('payload','bin','runtime','licenses','src')) {
    Copy-Item -LiteralPath (Join-Path $repoRoot $name) -Destination (Join-Path $releaseRoot $name) -Recurse
}
$archivePath = Join-Path $distRoot "$releaseName.zip"
Compress-Archive -LiteralPath $releaseRoot -DestinationPath $archivePath -CompressionLevel Optimal -Force
$checksum = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $distRoot 'SHA256SUMS.txt'), "$checksum  $releaseName.zip`n", (New-Object Text.UTF8Encoding($false)))
Write-Host "Portable release: $archivePath"
Write-Host "SHA-256: $checksum"
