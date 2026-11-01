# Register (or update) the daily task in Windows Task Scheduler. Safe to re-run.
# Remove: Unregister-ScheduledTask -TaskName RevenueTrackerDaily -Confirm:$false
# Settings: StartWhenAvailable = run ASAP after a missed schedule; WakeToRun = wake from sleep
# (a fully powered-off PC cannot be woken). Comments are ASCII on purpose: PowerShell 5.1
# misreads UTF-8 files without BOM.
$taskName = "RevenueTrackerDaily"

$action = New-ScheduledTaskAction -Execute "D:\Project\Revenue\jobs\run_daily.bat" -WorkingDirectory "D:\Project\Revenue"
$trigger = New-ScheduledTaskTrigger -Daily -At "20:00"

$settingArgs = @{
    StartWhenAvailable         = $true
    WakeToRun                  = $true
    AllowStartIfOnBatteries    = $true
    DontStopIfGoingOnBatteries = $true
    ExecutionTimeLimit         = (New-TimeSpan -Hours 3)
    RestartCount               = 2
    RestartInterval            = (New-TimeSpan -Minutes 10)
    MultipleInstances          = "IgnoreNew"
}
$settings = New-ScheduledTaskSettingsSet @settingArgs

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Revenue tracker: daily incremental update of revenue, prices, metrics and screens" -Force | Out-Null

$t = Get-ScheduledTask -TaskName $taskName
"Task: $($t.TaskName)  Next run: $((Get-ScheduledTaskInfo -TaskName $taskName).NextRunTime)"
"StartWhenAvailable=$($t.Settings.StartWhenAvailable) WakeToRun=$($t.Settings.WakeToRun) TimeLimit=$($t.Settings.ExecutionTimeLimit) RestartCount=$($t.Settings.RestartCount)"
