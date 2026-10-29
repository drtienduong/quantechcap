# Đăng ký lại tác vụ Task Scheduler "QuanTechCap_desk".
#
# Chạy bằng quyền Administrator (một lần) để có chế độ đầy đủ:
#   - khởi động cùng máy, không cần đăng nhập Windows (LogonType S4U, không lưu mật khẩu)
#   - chạy nền ở session 0, không có cửa sổ nào để lỡ tay đóng
# Chạy không có quyền admin: chế độ dự phòng
#   - khởi động lúc đăng nhập, console headless (không cửa sổ)
# Cả hai chế độ: watchdog mỗi 5 phút (nếu desk không chạy thì bật lại),
#   tự chạy lại khi lỗi, không giới hạn thời gian, chạy cả khi dùng pin.
#
# Hoàn tác: Unregister-ScheduledTask -TaskName QuanTechCap_desk -Confirm:$false
$ErrorActionPreference = "Stop"
$name = "QuanTechCap_desk"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $here "run_desk.ps1"
$user = "$env:USERDOMAIN\$env:USERNAME"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

$psArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`""
# Watchdog: lặp mỗi 5 phút, vô thời hạn. MultipleInstances=IgnoreNew nên đang chạy thì bỏ qua.
$watch = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)

if ($isAdmin) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $psArgs -WorkingDirectory $here
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    $start = New-ScheduledTaskTrigger -AtStartup
    $mode = "S4U, khoi dong cung may"
} else {
    # conhost --headless: không tạo cửa sổ (tránh Windows Terminal mở tab mà lỡ tay đóng)
    $action = New-ScheduledTaskAction -Execute "conhost.exe" -Argument "--headless powershell.exe $psArgs" -WorkingDirectory $here
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $start = New-ScheduledTaskTrigger -AtLogOn -User $user
    $mode = "Interactive, khoi dong luc dang nhap (chay lai voi quyen admin de co che do day du)"
}

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew

# Dừng phiên cũ (nếu có) trước khi đăng ký lại
$old = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($old) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'qc_desk\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Register-ScheduledTask -TaskName $name -Action $action -Principal $principal `
    -Trigger @($start, $watch) -Settings $settings `
    -Description "QuanTechCap desk: goi ra research.quantechcap.com, chay job backtest. Che do: $mode" | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "Da dang ky $name ($mode) va khoi dong."
