# QuanTechCap desk - vòng chạy tự khởi động lại (dùng cho Task Scheduler "QuanTechCap_desk").
# Desk chỉ gọi RA web (desk_config.json -> web_url). Không mở cổng nào trên máy bàn.
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$env:PYTHONIOENCODING = "utf-8"
$py = "C:\Claude\.venv\Scripts\python.exe"
while ($true) {
    foreach ($f in @("desk.log", "desk.err.log")) {   # xoay log khi > 5 MB, giữ 1 bản cũ
        if ((Test-Path $f) -and ((Get-Item $f).Length -gt 5MB)) { Move-Item $f "$f.1" -Force }
    }
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    cmd /c "echo $ts [run_desk] khoi dong qc_desk.py>> desk.log"
    cmd /c "`"$py`" qc_desk.py >> desk.log 2>> desk.err.log"
    $code = $LASTEXITCODE
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    cmd /c "echo $ts [run_desk] qc_desk.py thoat ma $code, chay lai sau 15 giay>> desk.log"
    Start-Sleep -Seconds 15
}
