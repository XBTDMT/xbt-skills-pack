# Claude Code seats for PowerShell. xbt-skills-pack's installer (--with-doctrine) loads this from your PowerShell profile.
#
#   cc [seat] [1|2] [claude args ...]
#
#   cc           engineer: the everyday seat (`cc eng` too). $CcEngineerModel at medium effort, with its working notes.
#   cc opus      reviewer: Claude Opus 5.5 at high effort, with its working notes. Reviews, hard maths, long builds.
#   cc fable     Claude Fable 5.1 at low effort, with its working notes, on Fable's own usage window.
#   cc sonnet    helper: Claude Sonnet 5.5 at high effort, the pack's skills and hooks, no working notes: on eight tasks
#                none of the notes were written from, it did as well without notes as with Opus 5.5's. Cheap side jobs.
#   cc skills    Claude Code's default model, the pack's skills and hooks, no doctrine (no model or effort pinned).
#   cc default   stock Claude Code (claude --safe-mode): no doctrine, CLAUDE.md, skills, plugins, hooks or MCP.
#
#   1 = your primary account (the default): Claude Code's own folder, %USERPROFILE%\.claude, or CLAUDE_CONFIG_DIR
#       when this shell already sets it.
#   2 = a second account: the Claude Code folder you name in your profile, above the line that loads this file:
#         $CcSecondaryDir = "$HOME\.claude-second"
#       `cc 2` runs claude with CLAUDE_CONFIG_DIR set to that folder (log in once there; to give it the pack, run
#       `python install.py --claude-dir <that folder>`). `primary` and `secondary` work as well as 1 and 2.
#
# The engineer, reviewer, fable and helper seats pin their models to exact ids, never aliases or "latest": a new model
# is measured first, then its pin moves here. `cc skills` and `cc default` run Claude Code's default model.
# Effort is the seat's default; change it with --effort or /effort. Anything after the seat and account words goes
# to claude unchanged:  cc opus 2 --continue
# The engineer seat is Claude Opus 5.5: on eight tasks none of the working notes were written from, on its notes of
# 29 September, it matched Fable 5.1 at low effort at half the cost. To run it on another model, set it in your profile,
# above the line that loads this file:
#   $CcEngineerModel = 'claude-sonnet-5'
# Written for Windows PowerShell 5.1 and PowerShell 7.

if (-not $CcEngineerModel) { $CcEngineerModel = 'claude-opus-5-5' }
# Set here, not in your profile: the helper seat is on since it was measured without working notes.
$CcSonnetReady = $true

function cc {
    $seat = 'engineer'; $account = 'primary'; $model = $CcEngineerModel; $effort = 'medium'
    $rest = @($args)
    while ($rest.Count -gt 0) {
        $word = [string]$rest[0]
        if (@('opus', 'reviewer') -contains $word) { $seat = 'reviewer'; $model = 'claude-opus-5-5'; $effort = 'high' }
        elseif (@('eng', 'engineer') -contains $word) { $seat = 'engineer'; $model = $CcEngineerModel; $effort = 'medium' }
        elseif ($word -eq 'fable') { $seat = 'fable'; $model = 'claude-fable-5-1'; $effort = 'low' }
        elseif (@('sonnet', 'helper') -contains $word) { $seat = 'helper'; $model = 'claude-sonnet-5-5'; $effort = 'high' }
        elseif ($word -eq 'skills') { $seat = 'skills' }
        elseif ($word -eq 'default') { $seat = 'default' }
        elseif (@('1', 'primary') -contains $word) { $account = 'primary' }
        elseif (@('2', 'secondary') -contains $word) { $account = 'secondary' }
        else { break }
        $rest = @($rest | Select-Object -Skip 1)
    }
    if ($seat -eq 'helper' -and -not $CcSonnetReady) {
        [Console]::Error.WriteLine("cc sonnet is not enabled. Use cc, cc opus or cc fable.")
        $global:LASTEXITCODE = 2
        return
    }
    if ($account -eq 'secondary' -and -not $CcSecondaryDir) {
        [Console]::Error.WriteLine('cc: account 2 needs $CcSecondaryDir, the Claude Code folder of your second login. Set it in your profile, above the line that loads cc.ps1, e.g. $CcSecondaryDir = "$HOME\.claude-second"')
        $global:LASTEXITCODE = 2
        return
    }
    # The seat's settings live only for this one claude run: a later bare `claude` must not inherit them.
    $savedDoctrine = $env:DOCTRINE
    $savedModel = $env:DOCTRINE_MODEL
    $savedConfig = $env:CLAUDE_CONFIG_DIR
    try {
        if ($account -eq 'secondary') { $env:CLAUDE_CONFIG_DIR = $CcSecondaryDir }
        if ($seat -eq 'skills') {
            $env:DOCTRINE = 'off'
            $cmd = @(); $what = "Claude Code's default model, pack skills and hooks, no doctrine"
        } elseif ($seat -eq 'helper') {
            $env:DOCTRINE = 'off'
            $cmd = @('--model', $model, '--effort', $effort); $what = "$model, effort $effort, pack skills and hooks, no doctrine"
        } elseif ($seat -eq 'default') {
            $cmd = @('--safe-mode'); $what = 'stock Claude Code (--safe-mode): no doctrine, skills, plugins or hooks'
        } else {
            $env:DOCTRINE = 'on'
            # The hook reads the model from Claude Code when it is sent, and from here when it is not.
            $env:DOCTRINE_MODEL = $model
            $cmd = @('--model', $model, '--effort', $effort); $what = "$model, effort $effort, doctrine on"
        }
        Write-Host "claude seat: $seat  account: $account  -  $what" -ForegroundColor Cyan
        & claude @cmd @rest
    } finally {
        if ($null -eq $savedDoctrine) { Remove-Item Env:DOCTRINE -ErrorAction SilentlyContinue } else { $env:DOCTRINE = $savedDoctrine }
        if ($null -eq $savedModel) { Remove-Item Env:DOCTRINE_MODEL -ErrorAction SilentlyContinue } else { $env:DOCTRINE_MODEL = $savedModel }
        if ($null -eq $savedConfig) { Remove-Item Env:CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue } else { $env:CLAUDE_CONFIG_DIR = $savedConfig }
    }
}
