# QuanTechCap desk - vòng chạy tự khởi động lại (dùng cho Task Scheduler "QuanTechCap_desk").
# Desk chỉ gọi RA web (desk_config.json -> web_url). Không mở cổng nào trên máy bàn.
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$env:PYTHONIOENCODING = "utf-8"
$py = "C:\Claude\.venv\Scripts\python.exe"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    cmd /c "echo $ts [run_desk] $msg>> desk.log"
}

# Chỉ một vòng run_desk được chạy (watchdog của Task Scheduler có thể gọi lại).
$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Global\QuanTechCap_desk_runner", [ref]$created)
if (-not $created) { exit 0 }

$waitLogged = $false
while ($true) {
    # Nếu còn qc_desk.py cũ (vòng trước bị giết nhưng python còn sống) thì chờ, không chạy song song.
    $old = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'qc_desk\.py' }
    if ($old) {
        if (-not $waitLogged) { Log "qc_desk.py khac dang chay (pid $($old.ProcessId -join ',')), cho"; $waitLogged = $true }
        Start-Sleep -Seconds 30
        continue
    }
    $waitLogged = $false
    foreach ($f in @("desk.log", "desk.err.log")) {   # xoay log khi > 5 MB, giữ 1 bản cũ
        if ((Test-Path $f) -and ((Get-Item $f).Length -gt 5MB)) { Move-Item $f "$f.1" -Force }
    }
    Log "khoi dong qc_desk.py (user $env:USERNAME)"
    cmd /c "`"$py`" qc_desk.py >> desk.log 2>> desk.err.log"
    Log "qc_desk.py thoat ma $LASTEXITCODE, chay lai sau 15 giay"
    Start-Sleep -Seconds 15
}
