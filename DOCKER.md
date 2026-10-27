# Chạy QuanTechCap bằng Docker

Chỉ cần Docker (Docker Desktop trên Windows/macOS, Docker Engine + compose plugin trên Ubuntu). Không cần cài Python.
Lệnh giống nhau trên mọi hệ điều hành.

## Dev (localhost)

    git clone <repo> quantechcap
    cd quantechcap
    docker compose up            # thêm -d để chạy nền

Mở http://127.0.0.1:8800. Không cần file `.env`. Chỉ có container `web`, không có Caddy, cookie không Secure (http).

## Production (VPS) -> https://quantechcap.com

Chuẩn bị một lần:
1. DNS: bản ghi A `quantechcap.com` -> IP VPS. Lần đầu để Cloudflare ở chế độ DNS-only (đám mây xám) để Caddy xin
   chứng chỉ Let's Encrypt thuận lợi.
2. Tường lửa VPS mở 22, 80, 443 (TCP).
3. Trong thư mục repo: `cp .env.example .env` (giữ `COMPOSE_PROFILES=prod`, `QC_DOMAIN`, `QC_SECURE_COOKIE=1`).

Chạy:

    docker compose up -d
    docker compose logs -f caddy     # thấy "certificate obtained successfully" là xong

`COMPOSE_PROFILES=prod` trong `.env` làm Caddy chạy kèm. Caddy tự xin và gia hạn chứng chỉ, chuyển http -> https,
thêm HSTS và header bảo mật, proxy vào `web:8800` trong mạng nội bộ của Docker.
Web vẫn chỉ nghe ở 127.0.0.1:8800 của VPS (debug qua SSH tunnel), bên ngoài chỉ vào được qua Caddy.

Lưu ý: Docker tự mở cổng đã publish, bỏ qua ufw. Ở đây chỉ publish 80/443 (Caddy) và 127.0.0.1:8800 nên vẫn an toàn.
Không xóa volume `caddy_data` (chứa chứng chỉ; Let's Encrypt giới hạn số lần cấp).

## Quản trị (chạy trong container)

    docker compose exec web python qc_admin.py add-user email@x.com slug-trader [--admin]
    docker compose exec web python qc_admin.py passwd email@x.com
    docker compose exec web python qc_admin.py list-users
    docker compose exec web python qc_admin.py show-token     # desk_token để dán vào desk_config.json

## Dừng, cập nhật, sao lưu

    docker compose down                     # dừng (dữ liệu trong volume vẫn còn)
    git pull && docker compose up -d --build
    docker compose cp web:/data ./backup-web-data      # sao lưu config + qc.sqlite

KHÔNG chạy `docker compose down -v` (xóa database, desk_token và chứng chỉ).

## Desk

Desk (máy bàn) không nằm trong compose; nó tự gọi ra web. `desk_config.json`:
- dev: `"web_url": "http://127.0.0.1:8800"`
- production: `"web_url": "https://quantechcap.com"`
`token` = kết quả `show-token` của web tương ứng.
