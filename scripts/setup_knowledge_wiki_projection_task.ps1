param(
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$SnapshotStore,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [Parameter(Mandatory = $true)][string]$ReceiptPath,
    [Parameter(Mandatory = $true)][string]$LockPath,
    [Parameter(Mandatory = $true)][string]$ExpectedCommit,
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

function Test-OutsideRoot([string]$Candidate, [string]$Root) {
    $candidatePath = [IO.Path]::GetFullPath($Candidate).TrimEnd('\')
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    return -not ($candidatePath -eq $rootPath -or $candidatePath.StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase))
}

function Resolve-AgainstRoot([string]$Candidate, [string]$Root) {
    if ([IO.Path]::IsPathFullyQualified($Candidate)) {
        return [IO.Path]::GetFullPath($Candidate)
    }
    return [IO.Path]::GetFullPath((Join-Path $Root $Candidate))
}

function Test-PathHasReparse([string]$Candidate) {
    $current = [IO.Path]::GetFullPath($Candidate)
    while ($true) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                return $true
            }
            if ($item.PSObject.Properties.Name -contains 'LinkType' -and $item.LinkType) {
                return $true
            }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $current) {
            break
        }
        $current = $parent
    }
    return $false
}

function Test-DistinctCanonicalPaths([string[]]$Paths) {
    $normalized = @(
        $Paths | ForEach-Object {
            [IO.Path]::GetFullPath($_).TrimEnd('\').ToLowerInvariant()
        }
    )
    return ($normalized | Select-Object -Unique).Count -eq $normalized.Count
}

function Test-GitCommitPresent([string]$Root, [string]$Commit) {
    & git -C $Root cat-file -e "$Commit`^{commit}" 2>$null
    return $LASTEXITCODE -eq 0
}

function Test-GitCommitIncluded([string]$Root, [string]$Commit) {
    & git -C $Root merge-base --is-ancestor $Commit HEAD 2>$null
    return $LASTEXITCODE -eq 0
}

$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$SnapshotStore = Resolve-AgainstRoot $SnapshotStore $RepoRoot
$OutputRoot = Resolve-AgainstRoot $OutputRoot $RepoRoot
$ReceiptPath = Resolve-AgainstRoot $ReceiptPath $RepoRoot
$LockPath = Resolve-AgainstRoot $LockPath $RepoRoot
if ($PersonRegistry) { $PersonRegistry = Resolve-AgainstRoot $PersonRegistry $RepoRoot }
if ($TopicRegistry) { $TopicRegistry = Resolve-AgainstRoot $TopicRegistry $RepoRoot }

$runner = [IO.Path]::GetFullPath((Join-Path $RepoRoot "scripts\run_knowledge_wiki_projection.py"))
$arguments = @(
    (Quote-Arg $runner),
    "--snapshot-store", (Quote-Arg $SnapshotStore),
    "--output-root", (Quote-Arg $OutputRoot),
    "--receipt", (Quote-Arg $ReceiptPath),
    "--lock", (Quote-Arg $LockPath)
)
if ($PersonRegistry) { $arguments += @("--person-registry", (Quote-Arg $PersonRegistry)) }
if ($TopicRegistry) { $arguments += @("--topic-registry", (Quote-Arg $TopicRegistry)) }
$argumentString = $arguments -join " "

$action = New-ScheduledTaskAction -Execute $PythonPath -Argument $argumentString -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$dataPaths = @($SnapshotStore, $OutputRoot, $ReceiptPath, $LockPath)
if ($PersonRegistry) { $dataPaths += $PersonRegistry }
if ($TopicRegistry) { $dataPaths += $TopicRegistry }
$expectedCommitFormat = $ExpectedCommit -match '^[0-9a-fA-F]{40}$'
$gitPresent = [bool](Get-Command git -ErrorAction SilentlyContinue)
$expectedCommitPresent = $false
$expectedCommitIncluded = $false
if ($gitPresent -and $expectedCommitFormat -and (Test-Path -LiteralPath (Join-Path $RepoRoot '.git') -PathType Container)) {
    $expectedCommitPresent = Test-GitCommitPresent $RepoRoot $ExpectedCommit
    if ($expectedCommitPresent) {
        $expectedCommitIncluded = Test-GitCommitIncluded $RepoRoot $ExpectedCommit
    }
}
$detectors = @{
    python_present = Test-Path -LiteralPath $PythonPath -PathType Leaf
    repo_root_present = Test-Path -LiteralPath $RepoRoot -PathType Container
    stable_checkout = Test-Path -LiteralPath (Join-Path $RepoRoot '.git') -PathType Container
    git_present = $gitPresent
    expected_commit_format = $expectedCommitFormat
    expected_commit_present = $expectedCommitPresent
    expected_commit_in_head_history = $expectedCommitIncluded
    runner_present = Test-Path -LiteralPath $runner -PathType Leaf
    snapshot_store_present = Test-Path -LiteralPath $SnapshotStore -PathType Leaf
    person_registry_present = (-not $PersonRegistry) -or (Test-Path -LiteralPath $PersonRegistry -PathType Leaf)
    topic_registry_present = (-not $TopicRegistry) -or (Test-Path -LiteralPath $TopicRegistry -PathType Leaf)
    data_paths_outside_repo = @($dataPaths | Where-Object { -not (Test-OutsideRoot $_ $RepoRoot) }).Count -eq 0
    data_paths_without_reparse = @($dataPaths | Where-Object { Test-PathHasReparse $_ }).Count -eq 0
    operational_paths_distinct = Test-DistinctCanonicalPaths $dataPaths
    direct_exit_propagation = $action.Execute -eq $PythonPath
}
$readyToApply = @($detectors.Values | Where-Object { -not $_ }).Count -eq 0

$matches = $false
$matchDetails = @{
    action = $false
    working_directory = $false
    enabled = $false
    daily_trigger = $false
    schedule = $false
    multiple_instances = $false
    execution_time_limit = $false
    hidden = $false
}
if ($existing) {
    $expectedTime = [TimeSpan]::Parse($At)
    $enabledTriggers = @($existing.Triggers | Where-Object { $_.Enabled })
    $dailyTriggers = @($enabledTriggers | Where-Object { $_.DaysInterval -eq 1 })
    $matchingTriggers = @($dailyTriggers | Where-Object {
        ([DateTime]$_.StartBoundary).TimeOfDay -eq $expectedTime
    })
    $matchDetails.action = (@($existing.Actions).Count -eq 1) -and
        ($existing.Actions.Execute -eq $PythonPath) -and
        ($existing.Actions.Arguments -eq $argumentString)
    $matchDetails.working_directory = $existing.Actions.WorkingDirectory -eq $RepoRoot
    $matchDetails.enabled = ($existing.State -ne "Disabled") -and $existing.Settings.Enabled
    $matchDetails.daily_trigger = ($enabledTriggers.Count -eq 1) -and
        ($dailyTriggers.Count -eq 1)
    $matchDetails.schedule = $matchingTriggers.Count -eq 1
    $matchDetails.multiple_instances = $existing.Settings.MultipleInstances -eq "IgnoreNew"
    $matchDetails.execution_time_limit = $existing.Settings.ExecutionTimeLimit -eq "PT15M"
    $matchDetails.hidden = [bool]$existing.Settings.Hidden
    $matches = @($matchDetails.Values | Where-Object { -not $_ }).Count -eq 0
}

if ($Verify) {
    $result = @{ schema = "dcb.knowledge_projection_task.v1"; ok = ($matches -and $readyToApply); action = "verify"; task_present = [bool]$existing; task_matches = $matches; ready_to_apply = $readyToApply; detectors = $detectors; match_details = $matchDetails; private_local_only = $true; paths_returned = $false }
    if ($Json) { $result | ConvertTo-Json -Compress } else { $result }
    if ($matches -and $readyToApply) { exit 0 } else { exit 2 }
}

if ($Apply) {
    if (-not $readyToApply) {
        $result = @{ schema = "dcb.knowledge_projection_task.v1"; ok = $false; action = "apply_blocked"; changed = $false; ready_to_apply = $false; detectors = $detectors; private_local_only = $true; paths_returned = $false }
        if ($Json) { $result | ConvertTo-Json -Compress } else { $result }
        exit 2
    }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "DCB private Knowledge Wiki daily projection" -Force | Out-Null
}
$result = @{ schema = "dcb.knowledge_projection_task.v1"; ok = $true; action = $(if ($Apply) { "apply" } else { "dry_run" }); changed = [bool]$Apply; task_present = [bool]$existing; ready_to_apply = $readyToApply; detectors = $detectors; private_local_only = $true; paths_returned = $false; schedule_local = $At }
if ($Json) { $result | ConvertTo-Json -Compress } else { $result }
