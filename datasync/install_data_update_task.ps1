# Đăng ký tác vụ Task Scheduler "QuanTechCap_data_update": cập nhật data-tickstory + swap hằng tuần,
# Chủ nhật 04:00 (Dương duyệt 2026-11-22). Chạy bỏ lỡ (máy tắt) thì chạy ngay khi có thể.
# Không giới hạn thời gian (Dukascopy có lúc rất chậm, lần đầu có thể mất nhiều giờ). Không chạy chồng.
#
#   powershell -ExecutionPolicy Bypass -File install_data_update_task.ps1          # đăng ký
#   powershell -ExecutionPolicy Bypass -File install_data_update_task.ps1 -RunNow  # đăng ký và chạy ngay
# Hoàn tác: Unregister-ScheduledTask -TaskName QuanTechCap_data_update -Confirm:$false
param([switch]$RunNow)
$ErrorActionPreference = "Stop"
$name = "QuanTechCap_data_update"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = "C:\Claude\.venv\Scripts\pythonw.exe"
$script = Join-Path $here "qc_data_update.py"
$user = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`" --run" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 04:00
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew

$old = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($old) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "QuanTechCap: noi tiep tick Dukascopy/Binance vao rust-quant\data-tickstory + swap synthetic (chi ghi noi tiep, co so ghi de hoan tac)" | Out-Null
$t = Get-ScheduledTask -TaskName $name
Write-Output "$name da dang ky: $($t.State), lan toi $((Get-ScheduledTaskInfo -TaskName $name).NextRunTime)"
if ($RunNow) { Start-ScheduledTask -TaskName $name; Write-Output "Da chay ngay" }
