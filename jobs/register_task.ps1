# 註冊（或更新）Windows 工作排程器的每日任務。可重複執行。
# 移除：Unregister-ScheduledTask -TaskName RevenueTrackerDaily -Confirm:$false
$taskName = "RevenueTrackerDaily"
$action   = New-ScheduledTaskAction -Execute "D:\Project\Revenue\jobs\run_daily.bat" -WorkingDirectory "D:\Project\Revenue"
$trigger  = New-ScheduledTaskTrigger -Daily -At "20:00"
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `            # 錯過排程時間，開機後盡快補跑
    -WakeToRun `                     # 睡眠中喚醒電腦（完全關機無法喚醒）
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "台股營收成長追蹤：每日增量更新營收、股價、指標與篩選" -Force | Out-Null
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
