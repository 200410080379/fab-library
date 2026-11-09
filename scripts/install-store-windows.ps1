param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\fab-library'))
$ErrorActionPreference = 'Stop'
$Version = '0.1.0'
$Url = "https://github.com/zirklerite/FabCLI/releases/download/v$Version/fabcli-v$Version-windows64.zip"
$ExpectedSha256 = '67e0eb68d62589a3282a4af12085a40246625fa7c57ffdd5189d5814b5c0a383'
if (-not [Environment]::Is64BitOperatingSystem) { throw 'FabCLI needs 64-bit Windows.' }
$TempDir = Join-Path ([IO.Path]::GetTempPath()) ('fab-store-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $TempDir | Out-Null
try {
    $Archive = Join-Path $TempDir 'fabcli.zip'
    Invoke-WebRequest -Uri $Url -OutFile $Archive
    if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedSha256) {
        throw 'FabCLI archive checksum mismatch. No executable installed.'
    }
    $Expanded = Join-Path $TempDir 'expanded'
    Expand-Archive -LiteralPath $Archive -DestinationPath $Expanded
    $Executables = @(Get-ChildItem -LiteralPath $Expanded -Recurse -File -Filter 'fabcli.exe')
    if ($Executables.Count -ne 1) { throw 'Unexpected FabCLI release archive layout.' }
    $Bin = Join-Path $InstallRoot 'bin'
    New-Item -ItemType Directory -Path $Bin -Force | Out-Null
    $Target = Join-Path $Bin 'fabcli.exe'
    if (Test-Path $Target) {
        Copy-Item -LiteralPath $Target -Destination ($Target + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    }
    Copy-Item -LiteralPath $Executables[0].FullName -Destination $Target -Force
    $Licenses = Join-Path $InstallRoot 'fabcli-notices'
    New-Item -ItemType Directory -Path $Licenses -Force | Out-Null
    Get-ChildItem -LiteralPath $Expanded -Recurse -File | Where-Object { $_.Name -match 'LICENSE|README|THIRD.PARTY' } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Licenses $_.Name) -Force
    }
    & $Target --version
    if ($LASTEXITCODE -ne 0) { throw 'FabCLI version check failed.' }
    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($UserPath -split ';') -notcontains $Bin) {
        $NewPath = if ([string]::IsNullOrWhiteSpace($UserPath)) { $Bin } else { $UserPath.TrimEnd(';') + ';' + $Bin }
        [Environment]::SetEnvironmentVariable('Path', $NewPath, 'User')
    }
    $env:Path = $Bin + ';' + $env:Path
    Write-Host 'FabCLI store backend installed. Next: fab-library store login'
    Write-Host 'Sign in locally in the Epic/Fab window. WebView2 Runtime is required.'
} finally {
    Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue
}
