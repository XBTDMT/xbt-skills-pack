# xbt-skills-pack — Windows setup. From the pack's folder, in PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1                              the skills and the doc check
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -WithDoctrine -WithPlugins   everything
#
# It checks what is installed, then runs install.py, which copies the skills into %USERPROFILE%\.claude\skills
# (or into $env:CLAUDE_CONFIG_DIR\skills when that is set).
# -WithDoctrine also adds the doctrine hook to settings.json, a template %USERPROFILE%\.claude\CLAUDE.md if you
# have none, and `cc` to your PowerShell profile (and lets PowerShell load that profile); -WithPlugins installs
# the plugins. -DryRun changes nothing. Safe to run again.

param([switch]$WithDoctrine, [switch]$WithPlugins, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
$pack = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Python {
    # `python` can be the Microsoft Store stub, which opens the Store instead of running anything, so every
    # candidate has to actually print a version before it counts.
    foreach ($candidate in @(@('py', '-3'), @('python'), @('python3'))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $extra = @($candidate | Select-Object -Skip 1)
        try {
            $path = & $exe @extra -c "import sys; print(sys.executable if sys.version_info >= (3, 9) else '')" 2>$null
        } catch { continue }
        if ($LASTEXITCODE -eq 0 -and $path -and (Test-Path $path)) { return $path.Trim() }
    }
    return $null
}

$missing = @()
$python = Find-Python
if (-not $python) { $missing += 'Python 3.9+   https://www.python.org/downloads/  (tick "Add python.exe to PATH")' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $missing += 'Git for Windows   https://git-scm.com/download/win' }
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { $missing += 'Claude Code   irm https://claude.ai/install.ps1 | iex' }
if ($missing.Count -gt 0) {
    Write-Host 'Install these first, then open a NEW PowerShell window and run setup again:' -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  - $_" }
    exit 2
}
Write-Host "Python: $python"

# A profile only loads when scripts are allowed to run. RemoteSigned for the current user is the usual
# developer setting: your own scripts run, downloaded ones must be signed. Nothing machine-wide changes.
# This script itself runs under -ExecutionPolicy Bypass, so plain Get-ExecutionPolicy answers "Bypass": work out
# what a normal new window gets from the scopes that outlive this process. None set means Windows' Restricted.
$effective = 'Restricted'; $from = 'the Windows default'
foreach ($scope in @('MachinePolicy', 'UserPolicy', 'CurrentUser', 'LocalMachine')) {
    $p = [string](Get-ExecutionPolicy -Scope $scope)
    if ($p -ne 'Undefined') { $effective = $p; $from = $scope; break }
}
if ($WithDoctrine -and @('Restricted', 'AllSigned') -contains $effective) {
    if (@('MachinePolicy', 'UserPolicy') -contains $from) {
        Write-Host "Group Policy sets scripts to '$effective', so your profile will not load and 'cc' will not exist." -ForegroundColor Yellow
        Write-Host "Claude Code itself still works: start it with 'claude'."
    } elseif (-not $DryRun) {
        Write-Host "New PowerShell windows would not load your profile ('$effective' from $from); allowing your own scripts (RemoteSigned, this user only)."
        Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
    } else {
        Write-Host "Dry run: would set this user's script policy to RemoteSigned (now '$effective' from $from)."
    }
}

$installArgs = @((Join-Path $pack 'install.py'))
if ($WithDoctrine) { $installArgs += @('--with-doctrine', '--profile', $PROFILE.CurrentUserAllHosts) }
if ($WithPlugins) { $installArgs += '--with-plugins' }
if ($DryRun) { $installArgs += '--dry-run' }
& $python @installArgs
exit $LASTEXITCODE
