# Chạy QuanTechCap bằng Docker

Không cần cài Python trên máy. Chỉ cần Docker (Docker Desktop trên Windows/macOS, Docker Engine + compose plugin trên Linux).

Hiện compose chỉ đóng gói **web** (backend + giao diện). **Desk** vẫn chạy trực tiếp trên Windows của máy bàn,
vì engine là `backtest-optimzie-rust-cli.exe` (Windows) và `cpucap.py` dùng Job Object của Windows. Xem mục cuối.

## Chạy

    git clone <repo> quantechcap
    cd quantechcap
    docker compose up -d            # lần đầu tự build image
    docker compose logs -f web      # xem log

Mở http://127.0.0.1:8800 (chỉ nghe trên 127.0.0.1 của máy chủ; trên VPS thì Caddy HTTPS proxy vào cổng này).
Đổi cổng phía máy chủ: đặt `QC_WEB_PORT=8900` trong file `.env` cạnh docker-compose.yml.

Lần đầu khởi động, container tự tạo `web_config.json` với `desk_token` ngẫu nhiên trong volume `qc_web_data`
(không in token ra log). Các lần sau giữ nguyên.

## Quản trị (chạy trong container)

    docker compose exec web python qc_admin.py add-user email@x.com slug-trader [--admin]
    docker compose exec web python qc_admin.py passwd email@x.com
    docker compose exec web python qc_admin.py list-users
    docker compose exec web python qc_admin.py show-token     # desk_token để dán vào desk_config.json

## Dừng, cập nhật, sao lưu

    docker compose down                     # dừng (dữ liệu trong volume vẫn còn)
    git pull && docker compose up -d --build
    docker compose cp web:/data ./backup-web-data      # sao lưu config + qc.sqlite

KHÔNG chạy `docker compose down -v` nếu không muốn xóa database và desk_token.

## Desk

Desk gọi ra web (`web_url` trong `desk_config.json`), web không gọi vào máy bàn. Khi web chạy trong Docker trên
cùng máy bàn, `web_url` vẫn là `http://127.0.0.1:8800`. `token` trong desk_config.json = kết quả `show-token`.
