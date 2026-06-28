$taskName = "InvestmentStrategy-OutcomeUpdate"
$batPath = "D:\secondbrain\Codex\investment-strategy-company\run_outcome_daily.bat"
$action = New-ScheduledTaskAction -Execute $batPath
$trigger = New-ScheduledTaskTrigger -Daily -At 15:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "Peihao" -LogonType Interactive -RunLevel Limited
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force
    Write-Host "Task registered for user Peihao"
} catch {
    Write-Host "Still need admin: run this script AS ADMINISTRATOR"
    Write-Host "Error: $_"
}
