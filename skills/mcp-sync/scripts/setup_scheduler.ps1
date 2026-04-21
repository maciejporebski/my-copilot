<#
.SYNOPSIS
    Set up a Windows Task Scheduler task to periodically sync Copilot configs
    (MCP servers, agents, skills) between VS Code and CLI.

.PARAMETER IntervalMinutes
    How often to run the sync (default: 60 minutes).

.PARAMETER TaskName
    Name of the scheduled task (default: CopilotSync).

.PARAMETER Remove
    Remove the scheduled task instead of creating it.

.PARAMETER PythonPath
    Path to the Python executable. Auto-detected if not specified.

.PARAMETER ResourceType
    Which resource types to sync: all, mcp-servers, agents, skills (default: all).
#>
param(
    [int]$IntervalMinutes = 60,
    [string]$TaskName = "CopilotSync",
    [switch]$Remove,
    [string]$PythonPath,
    [ValidateSet("all", "mcp-servers", "agents", "skills")]
    [string]$ResourceType = "all"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$syncScript = Join-Path $scriptDir "sync.py"

# ── Remove mode ──────────────────────────────────────────────────────────────

if ($Remove) {
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
        Write-Host "`u{2713} Removed scheduled task: $TaskName"
    }
    catch {
        Write-Host "Task '$TaskName' not found or could not be removed: $_"
    }
    return
}

# ── Locate Python ────────────────────────────────────────────────────────────

if (-not $PythonPath) {
    $PythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $PythonPath) {
        $PythonPath = (Get-Command python3 -ErrorAction SilentlyContinue).Source
    }
}

if (-not $PythonPath) {
    Write-Error "Python not found. Install Python or specify -PythonPath."
    return
}

if (-not (Test-Path $syncScript)) {
    Write-Error "Sync script not found at $syncScript"
    return
}

# ── Create the scheduled task ────────────────────────────────────────────────

Write-Host "Setting up scheduled task..."
Write-Host "  Task name : $TaskName"
Write-Host "  Interval  : every $IntervalMinutes minutes"
Write-Host "  Script    : $syncScript"
Write-Host "  Python    : $PythonPath"
Write-Host ""

$arguments = "`"$syncScript`" --resource-type $ResourceType"

$action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument $arguments

$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Syncs Copilot configs (MCP servers, agents, skills) between VS Code and CLI" `
        -Force | Out-Null

    Write-Host "`u{2713} Scheduled task created: $TaskName"
    Write-Host "  Runs every $IntervalMinutes minutes starting now."
    Write-Host ""
    Write-Host "To remove:"
    Write-Host "  powershell -File `"$($MyInvocation.MyCommand.Path)`" -Remove"
}
catch {
    Write-Error "Failed to create scheduled task: $_"
}
