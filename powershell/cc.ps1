# Claude Code seats for PowerShell. xbt-skills-pack's installer (--with-doctrine) loads this from your PowerShell profile.
#
#   cc            engineer: the everyday seat. $CcEngineerModel at low effort, with the short working notes.
#   cc opus       reviewer: Claude Opus 5.5 at high effort, with its working notes. Reviews, hard maths, long builds.
#   cc plain      plain Claude Code: no doctrine injected, no model or effort pinned.
#
# Anything after the seat goes to claude unchanged:  cc opus --continue
# If your plan has no Fable, set the engineer model in your profile, above the line that loads this file:
#   $CcEngineerModel = 'claude-sonnet-5'
# Written for Windows PowerShell 5.1 and PowerShell 7.

if (-not $CcEngineerModel) { $CcEngineerModel = 'claude-fable-5-1' }

function cc {
    $rest = @($args)
    $seat = 'engineer'
    if ($rest.Count -gt 0 -and @('opus', 'reviewer', 'plain', 'fable', 'engineer') -contains $rest[0]) {
        $seat = [string]$rest[0]
        $rest = @($rest | Select-Object -Skip 1)
    }
    # The seat's settings live only for this one claude run: a later bare `claude` must not inherit them.
    $savedDoctrine = $env:DOCTRINE
    $savedModel = $env:DOCTRINE_MODEL
    try {
        if ($seat -eq 'plain') {
            $env:DOCTRINE = 'off'
            Write-Host 'claude seat: plain  (no doctrine; model and effort are whatever claude defaults to)' -ForegroundColor Cyan
            & claude @rest
            return
        }
        if ($seat -eq 'opus' -or $seat -eq 'reviewer') {
            $seatName = 'reviewer'; $model = 'claude-opus-5-5'; $effort = 'high'
        } else {
            $seatName = 'engineer'; $model = $CcEngineerModel; $effort = 'low'
        }
        $env:DOCTRINE = 'on'
        # The hook reads the model from Claude Code when it is sent, and from here when it is not.
        $env:DOCTRINE_MODEL = $model
        Write-Host "claude seat: $seatName  model: $model  effort: $effort  (doctrine on; 'cc plain' for none)" -ForegroundColor Cyan
        & claude --model $model --effort $effort @rest
    } finally {
        if ($null -eq $savedDoctrine) { Remove-Item Env:DOCTRINE -ErrorAction SilentlyContinue } else { $env:DOCTRINE = $savedDoctrine }
        if ($null -eq $savedModel) { Remove-Item Env:DOCTRINE_MODEL -ErrorAction SilentlyContinue } else { $env:DOCTRINE_MODEL = $savedModel }
    }
}
