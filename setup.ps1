# xbt-skills-pack — Windows setup. From the pack's folder, in PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1                              what install-defaults.json says
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -WithDoctrine -WithPlugins   also the doctrine and the plugins
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1 -SkipPlugins                 everything the defaults say but the plugins
#
# What it does with no switches is written in install-defaults.json beside this script. install.py is the one reader
# of that file: this script asks it for the options that apply (install.py --print-options) and stops if it cannot
# read them. It checks what is installed, then runs install.py, which copies the skills into
# %USERPROFILE%\.claude\skills (or into $env:CLAUDE_CONFIG_DIR\skills when that is set). With the doctrine on it also
# adds the doctrine hook to settings.json, a template %USERPROFILE%\.claude\CLAUDE.md if you have none, and `cc` to
# your PowerShell profile (and lets PowerShell load that profile); with the plugins on it installs the plugins.
# -DryRun changes nothing. Safe to run again.

param([switch]$WithDoctrine, [switch]$WithPlugins, [switch]$SkipPlugins, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
$pack = Split-Path -Parent $MyInvocation.MyCommand.Path

# Started as `powershell ...` from a PowerShell 7 window (Windows Terminal's default), Windows PowerShell inherits
# PowerShell 7's module folders in PSModulePath, loads their Microsoft.PowerShell.Security, cannot, and is then left
# with no Get-ExecutionPolicy. Windows PowerShell keeps only its own folders, its own modules' folder ($PSHOME\Modules)
# among them; PowerShell 7 itself is left as it is.
if ($PSVersionTable.PSVersion.Major -le 5) {
    $own = @(($env:PSModulePath -split ';') | Where-Object { $_ -and $_ -notmatch '[\\/]PowerShell[\\/]' })
    $env:PSModulePath = (@($own) + @(Join-Path $PSHOME 'Modules') | Select-Object -Unique) -join ';'
}

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

# The switches, passed to install.py as they are; install.py applies them over install-defaults.json.
$installPy = Join-Path $pack 'install.py'
$flags = @()
if ($WithDoctrine) { $flags += '--with-doctrine' }
if ($WithPlugins) { $flags += '--with-plugins' }
if ($SkipPlugins) { $flags += '--skip-plugins' }

# One reader of the defaults: install.py prints the options that apply, or says what is wrong and exits non-zero.
$printed = & $python $installPy --print-options @flags
if ($LASTEXITCODE -ne 0) {
    $printed | ForEach-Object { Write-Host $_ }
    exit $LASTEXITCODE
}
$options = ($printed | Out-String) | ConvertFrom-Json

# A profile only loads when scripts are allowed to run. RemoteSigned for the current user is the usual
# developer setting: your own scripts run, downloaded ones must be signed. Nothing machine-wide changes.
# This script itself runs under -ExecutionPolicy Bypass, so plain Get-ExecutionPolicy answers "Bypass": work out
# what a normal new window gets from the scopes that outlive this process. None set means Windows' Restricted.
$effective = 'Restricted'; $from = 'the Windows default'
foreach ($scope in @('MachinePolicy', 'UserPolicy', 'CurrentUser', 'LocalMachine')) {
    $p = [string](Get-ExecutionPolicy -Scope $scope)
    if ($p -ne 'Undefined') { $effective = $p; $from = $scope; break }
}
if ($options.doctrine -and @('Restricted', 'AllSigned') -contains $effective) {
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

$installArgs = @($installPy) + $flags
if ($options.doctrine) { $installArgs += @('--profile', $PROFILE.CurrentUserAllHosts) }
if ($DryRun) { $installArgs += '--dry-run' }
& $python @installArgs
exit $LASTEXITCODE
