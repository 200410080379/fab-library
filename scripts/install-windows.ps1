param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\fab-library'),
    [switch]$SkipSkill
)
$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path $PSScriptRoot -Parent
$WheelDir = Join-Path $PackageRoot 'wheels'
if (-not (Test-Path $WheelDir)) { throw 'Missing wheels directory. Extract the full release zip first.' }
$Launcher = Get-Command py -ErrorAction SilentlyContinue
$PythonArgs = @('-3')
if (-not $Launcher) {
    $Launcher = Get-Command python -ErrorAction SilentlyContinue
    $PythonArgs = @()
}
if (-not $Launcher) { throw 'Install Python 3.11 or newer for Windows from python.org, then run this script again.' }
& $Launcher.Source @PythonArgs -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
$Venv = Join-Path $InstallRoot 'venv'
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
    & $Launcher.Source @PythonArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
$Python = Join-Path $Venv 'Scripts\python.exe'
& $Python -m pip install --no-index --find-links $WheelDir --upgrade 'fab-library==0.1.0'
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed.' }
$Bin = Join-Path $InstallRoot 'bin'
New-Item -ItemType Directory -Path $Bin -Force | Out-Null
$CommandExe = Join-Path $Venv 'Scripts\fab-library.exe'
$Wrapper = "@echo off`r`n`"$CommandExe`" %*`r`n"
Set-Content -LiteralPath (Join-Path $Bin 'fab-library.cmd') -Value $Wrapper -Encoding ASCII
Copy-Item (Join-Path $PackageRoot 'README.md') (Join-Path $InstallRoot 'README.md') -Force
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($UserPath -split ';') -notcontains $Bin) {
    $NewPath = if ([string]::IsNullOrWhiteSpace($UserPath)) { $Bin } else { $UserPath.TrimEnd(';') + ';' + $Bin }
    [Environment]::SetEnvironmentVariable('Path', $NewPath, 'User')
}
$env:Path = $Bin + ';' + $env:Path
if (-not $SkipSkill) {
    $CodexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $SkillDir = Join-Path $CodexRoot 'skills\fab-library'
    if (Test-Path $SkillDir) {
        $Backup = $SkillDir + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
        Move-Item -LiteralPath $SkillDir -Destination $Backup
        Write-Host "Previous skill saved at $Backup"
    }
    New-Item -ItemType Directory -Path $SkillDir -Force | Out-Null
    Copy-Item (Join-Path $PackageRoot 'skills\fab-library\SKILL.md') $SkillDir
    Write-Host "AI skill installed at $SkillDir"
}
& $CommandExe --json doctor
if ($LASTEXITCODE -ne 0) { throw 'Post-install diagnostic failed.' }
Write-Host 'Installed. Open a NEW terminal or restart your AI app to load the updated PATH and skill.'
Write-Host 'Next: fab-library auth login'
