param(
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$SnapshotStore,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [Parameter(Mandatory = $true)][string]$ReceiptPath,
    [Parameter(Mandatory = $true)][string]$LockPath,
    [string]$PersonRegistry = "",
    [string]$TopicRegistry = "",
    [string]$TaskName = "DCB-Knowledge-Wiki-Projection",
    [string]$At = "04:00",
    [switch]$Apply,
    [switch]$Verify,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

function Quote-Arg([string]$Value) { '"' + $Value.Replace('"', '\"') + '"' }

$runner = Join-Path $RepoRoot "scripts\run_knowledge_wiki_projection.py"
$arguments = @(
    (Quote-Arg $PythonPath), (Quote-Arg $runner),
    "--snapshot-store", (Quote-Arg $SnapshotStore),
    "--output-root", (Quote-Arg $OutputRoot),
    "--receipt", (Quote-Arg $ReceiptPath),
    "--lock", (Quote-Arg $LockPath)
)
if ($PersonRegistry) { $arguments += @("--person-registry", (Quote-Arg $PersonRegistry)) }
if ($TopicRegistry) { $arguments += @("--topic-registry", (Quote-Arg $TopicRegistry)) }
$argumentString = $arguments -join " "

$action = New-ScheduledTaskAction -Execute "conhost.exe" -Argument ("--headless " + $argumentString) -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

$matches = $false
if ($existing) {
    $matches = ($existing.Actions.Execute -eq "conhost.exe") -and
        ($existing.Actions.Arguments -eq ("--headless " + $argumentString)) -and
        ($existing.Settings.MultipleInstances -eq "IgnoreNew")
}

if ($Verify) {
    $result = @{ schema = "dcb.knowledge_projection_task.v1"; ok = $matches; action = "verify"; task_present = [bool]$existing; task_matches = $matches; private_local_only = $true; paths_returned = $false }
    if ($Json) { $result | ConvertTo-Json -Compress } else { $result }
    if ($matches) { exit 0 } else { exit 2 }
}

if ($Apply) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "DCB private Knowledge Wiki daily projection" -Force | Out-Null
}
$result = @{ schema = "dcb.knowledge_projection_task.v1"; ok = $true; action = $(if ($Apply) { "apply" } else { "dry_run" }); changed = [bool]$Apply; task_present = [bool]$existing; private_local_only = $true; paths_returned = $false; schedule_local = $At }
if ($Json) { $result | ConvertTo-Json -Compress } else { $result }
