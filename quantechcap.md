# QuanTechCap - Mô tả dự án

Chủ dự án: Dương. Nguồn: tóm tắt hội thoại `tomtathoithoai.txt` (26/10/2026), đối chiếu với thiết kế `C:\Claude\workspace\claude_traders\QUANTECHCAP_DESIGN.md` (v0.2) và code thử nghiệm trong `claude_traders\quantechcap\`.

Ghi chú: mục nào ghi "chưa kiểm chứng" nghĩa là chưa ai đo hoặc thử.

---

## 1. Mục đích

Hệ thống cho một nhóm quant trader cùng nghiên cứu alpha forex:

- Mục tiêu của mỗi trader: tạo alpha vượt bộ lọc IS (sau đó OS1 và xác nhận). OS2 là holdout mù do Dương giữ.
- Mọi trader dùng chung một bộ dữ liệu tick (`data-tickstory`, khoảng 627 GB) nằm trên máy bàn của Dương.
- Trader tự dùng AI của họ trên máy của họ để viết alpha theo bộ khung QuanTechCap, nộp mã nguồn lên web, chạy backtest/optimize trên data-server, rồi xem báo cáo trên web (quyết định 29/10/2026).
- Toàn bộ platform đặt tại một địa chỉ: `research.quantechcap.com` (Dương chốt 28/10/2026). Không tạo subdomain riêng cho từng trader; mỗi trader chỉ có một tài khoản đăng nhập. Slug trader (ASCII, gạch ngang, ví dụ `nguyen-an`) chỉ dùng nội bộ.
- Dương thấy tất cả. Trader không thấy alpha của nhau.

Ngoài phạm vi giai đoạn đầu: giao dịch thật, thanh toán, chia sẻ dữ liệu thô ra ngoài.

---

## 2. Các quyết định đã chốt

1. Dương sở hữu `quantechcap.com`, DNS ở Cloudflare.
2. Máy bàn của Dương chạy 24/7.
3. Trader đăng nhập bằng email và mật khẩu do Dương cấp trực tiếp. Không đăng ký tự do.
4. Web server đặt ở VPS OVHcloud Singapore: 2 vCore, 4 GB RAM, đĩa 40 GB.
5. **Máy bàn tính toán, VPS chỉ hiển thị.** Máy bàn luôn là bên gọi ra VPS, VPS không có đường vào máy bàn. Lý do: bộ tick quá nặng để kéo qua mạng, VPS nhỏ không chạy nổi optimizer, và nếu VPS bị tấn công thì không có đường vào máy bàn.
6. **Alpha nộp qua web, có cổng kiểm** (đổi ngày 29/10/2026, thay cho "cách 1" agent viết alpha):
 - Trader tự viết alpha bằng AI của họ, dựa trên bộ khung + alpha mẫu tải trên web. Dương KHÔNG cung cấp agent cho trader, KHÔNG phát SDK/engine, KHÔNG phát dữ liệu mẫu. Mọi lần chạy đều trên data-server.
 - Trader nộp MÃ NGUỒN `.rs` (không nộp file chạy). Web chỉ giữ mã tạm đến khi data-server kéo về rồi xóa; mã và report lưu ở data-server.
 - Data-server: khu cách ly -> công cụ kiểm tra tự động (danh sách cho phép) -> biên dịch thử (chỉ trả lỗi thuộc alpha) -> duyệt (agent platform duyệt; trader được Dương cấp quyền trong desk_config thì tự duyệt) -> mới vào thư mục alpha của trader.
 - Alpha chỉ được dùng dữ liệu engine đưa vào (tick/bar trong data-server). Không đọc file, mạng, tiến trình, biến môi trường, không `unsafe`, không dữ liệu ngoài.
 - Bắt buộc trước khi nhận mã của trader ngoài Dương: `data-is` (giấu OS2) và tài khoản Windows quyền thấp `qc_runner` để biên dịch/chạy alpha.
7. **Web không nhận file `.ini` thô.** Form có cấu trúc, máy bàn tự dựng lại file cấu hình và tự ép `data_folder`, `swap_folder`, `todate`.
8. **Tách job IS và OS1.** Job optimize chạy đến 2024-01-01 nên `results.sqlite` không bao giờ chứa OS1. OS1 là loại job riêng, chỉ cho biến thể đã pass IS, qua cổng có ghi sổ.
9. **Tải báo cáo theo nhu cầu.** Web chỉ nhận bản tóm tắt. Danh sách lệnh (phần nặng nhất) chỉ tải khi trader bấm xem, mỗi trang 100 lệnh. Phần run cũng làm tương tự.
10. **OS2:** Dương không chấp nhận để mã alpha đọc được OS2 (đã nói rõ trong hội thoại). Nhưng bản thử nghiệm hiện tại chưa cần giấu OS2 (Dương xác nhận). Việc giấu OS2 vẫn là điều kiện bắt buộc trước khi cho trader thật dùng.

---

## 3. Kiến trúc

```
Trader (trình duyệt)                 Trader (Telegram)
       |                                   |
       v                                   v
VPS OVH Singapore                    MÁY BÀN CỦA DƯƠNG (24/7)
 - web: đăng nhập, form job,          - (bỏ agent cho trader từ 29/10/2026)
   hàng đợi, trạng thái, báo cáo      - desk: gọi ra web lấy job
 - lưu bản tóm tắt báo cáo            - backtest tool (rq_run) + data-tickstory
                                      - thư mục report của từng trader
        ^                                     |
        |  máy bàn gọi ra (long-poll,         |
        +-- đẩy tóm tắt, tiến độ, trang lệnh -+
```

Luồng một job (như đã thống nhất):
1. Trader đăng nhập web, chọn alpha trong danh sách, nhập lưới tham số (start, step, stop), symbol, khung thời gian bằng form.
2. Cấu hình có cấu trúc (không phải ini, không phải mã) nằm trong hàng đợi trên VPS.
3. Máy bàn hỏi VPS có job mới không. Nhận job, kiểm tra lại độc lập (không tin web), tự dựng ini, ép đường dẫn và `todate`.
4. Máy bàn chạy `rq_run.py` (giới hạn CPU 50%, mỗi lúc một run nặng). Kết quả lưu ở `<trader>\report\<alpha>\ver_..._opt_...\`.
5. Máy bàn đẩy lên VPS bản tóm tắt (bảng biến thể) và tiến độ. Trades và equity chỉ gửi khi trader bấm xem.
6. Máy bàn offline thì web phải báo rõ job đang chờ.

Thành phần máy bàn (data-server): desk, công cụ kiểm tra alpha, worker, thư mục alpha/submissions/report của từng trader.
Thành phần VPS (đề xuất): Caddy (TLS cho research.quantechcap.com), lớp đăng nhập, viewer.
Cloudflare: một bản ghi A `research.quantechcap.com` trỏ về VPS (không cần wildcard). Lần đầu DNS-only để Caddy xin chứng chỉ; bật proxy hay không thì chưa quyết.

---

## 4. Bộ lọc và quy tắc dữ liệu

- IS: từ đầu dữ liệu đến 2024-01-01. OS1: 2024 đến 2026. OS2: từ 2026-01-01, holdout mù chỉ Dương dùng.
- Trader không được thấy OS1 của mọi biến thể (tránh chọn tham số theo OS1, tức overfit). Web chỉ đăng bản IS-only.
- OS2 không bao giờ lên web.
- Tên alpha có đuôi `_<tên trader>` (ví dụ `alpha_breakout_day_duong_nguyen`). Desk chỉ liệt kê alpha khớp đuôi này.

---

## 5. Hiện trạng (bản thử nghiệm)

Đang thử với một trader: **duong-nguyen**, agent `duong_nguyen_claude`.

Cây code: `C:\Claude\workspace\claude_traders\quantechcap\`
- `common\qc_common.py`: phần dùng chung (kiểm tra job, dựng ini, giới hạn mặc định).
- `desk\qc_desk.py`: tác tử trên máy bàn. Chỉ gọi ra web, kiểm tra lại cấu hình, chạy `rq_run`, báo tiến độ, trả trang lệnh/equity khi được hỏi, hỗ trợ hủy job.
- `web\qc_web.py`, `qc_admin.py`, `static\*`: web (hiện nghe `127.0.0.1:8800`), SQLite `qc.sqlite`, đăng nhập.
- Workspace trader: `duong_nguyen_claude\` (alpha, report, notes, lab).

Đã quan sát được:
- Ít nhất 9 job chạy qua luồng web → desk, đều exit 0. Job optimize (job 3) tạo report đúng cấu trúc `ver_..._opt_...` kèm `_eval`.
- Các job chạy chỉ vài giây trong khi một run thật mất khoảng 77 phút. Cấu hình thử có vẻ rất ngắn nên kết quả chỉ dùng để kiểm tra luồng. Nguyên nhân chưa xác nhận.

Docker (2026-10-27): web đóng gói được bằng `docker-compose.yml` ở gốc repo (xem `DOCKER.md`): image python slim, không cần pip, user thường, root chỉ đọc, chỉ mở 127.0.0.1:8800, config + SQLite trong volume `qc_web_data`, tự tạo desk_token lần đầu. Đã kiểm luồng khởi động bằng Python với cùng biến môi trường; CHƯA build/chạy image thật vì máy bàn chưa có Docker. Desk không nằm trong compose (engine .exe Windows, cpucap dùng API Windows); desk tự gọi ra web. Caddy nằm trong compose ở profile `prod`: VPS có `.env` (COMPOSE_PROFILES=prod) thì `docker compose up` ra https://research.quantechcap.com; localhost không có `.env` thì chỉ web ở http://127.0.0.1:8800. Đã kiểm: Caddyfile hợp lệ (caddy validate 2.8.4), chạy thử Caddy proxy trước web: đăng nhập được, cookie có Secure, header HSTS/nosniff/DENY có. Chưa kiểm: xin chứng chỉ thật trên VPS, build image Docker thật.

VPS (2026-10-28): VPS 15.235.208.240 (Ubuntu 26.04) dùng chung với các dịch vụ có sẵn của Dương (Nginx Proxy Manager giữ 80/443 cho note.quantechcap.com, Portainer, RustDesk, webnote, openclaw). Vì vậy KHÔNG dùng Caddy; web chạy ở /opt/stacks/quantechcap (docker-compose.yml + docker-compose.npm.yml, .env từ .env.npm.example), container quantechcap-web nối mạng `proxy` của NPM, chỉ publish 127.0.0.1:8800. NPM proxy host research.quantechcap.com -> quantechcap-web:8800 (Let's Encrypt). Đã kiểm: build image thật trên VPS OK, healthy, NPM gọi được web (200). Token desk đã đổi (tạo mới trên VPS), tài khoản web mới (mật khẩu ở file trên máy bàn, ngoài repo). Desk chạy qua Task Scheduler QuanTechCap_desk (lúc đăng nhập, run_desk.ps1 tự khởi động lại), web_url https://research.quantechcap.com.
Đã lên mạng (2026-10-28): https://research.quantechcap.com qua NPM (Let's Encrypt, Force SSL, HSTS). Kiểm thử đầu-cuối 18/18 PASS (đăng nhập, cô lập 2 tài khoản, CSRF, chặn todate > 2024, job smoke web -> desk -> kết quả, trang lệnh theo yêu cầu). Chưa kiểm trên trình duyệt thật. Admin chưa có màn xem dữ liệu của trader (API lọc theo user_id). VPS đã cập nhật hết gói và reboot sau snapshot.
Desk 24/7 (2026-10-29): desk đã dừng từ 17:55 ngày 28 (mã 0xC000013A, nhiều khả năng cửa sổ console bị Windows Terminal mở thành tab rồi bị đóng); tác vụ chỉ khởi động lúc đăng nhập nên không tự lên lại. Sửa: `install_desk_task.ps1` đăng ký lại QuanTechCap_desk với watchdog mỗi 5 phút (MultipleInstances=IgnoreNew), RestartCount 999, không giới hạn thời gian; run_desk.ps1 có mutex một vòng chạy và không bật qc_desk.py thứ hai nếu bản cũ còn sống. Chế độ hiện tại (không admin): khởi động lúc đăng nhập, chạy qua `conhost --headless` (không cửa sổ). Đã kiểm: giết desk lúc 10:48:40, watchdog bật lại 10:51:01; giết riêng run_desk thì không sinh desk trùng. Chưa làm (cần Dương chạy script bằng quyền admin một lần): chế độ S4U khởi động cùng máy không cần đăng nhập, chưa kiểm chứng.
Nộp alpha qua web (2026-10-29): bộ khung `testversion/web/static/kit/` (AI_GUIDE.md, HUONG_DAN.md, qc_alpha_api.rs, alpha_template.rs, 2 alpha mẫu, reserved_names.txt, local_check.rs), tải ở trang "Nộp alpha" (cần đăng nhập). Web nhận mã nguồn `.rs` (<= 200 KB, tên phải có hậu tố `_<slug với _>`, giới hạn 30 lần/giờ, 3 bản chờ), giao cho desk rồi XÓA mã khỏi web. Desk: khu cách ly `<trader>/submissions/<id>_<tên>/` -> `qc_alpha_check.py` (kiểm tĩnh theo danh sách cho phép + biên dịch thử --emit=metadata trong khung engine thật, chỉ trả lỗi thuộc alpha) -> chờ duyệt (`qc_review.py list/show/approve/reject`) hoặc tự duyệt nếu `auto_approve: true` trong desk_config -> chép vào `<trader>/alpha/`. Smoke: tối đa 92 ngày, 3 symbol, được ưu tiên trước optimize đang chờ (KHÔNG chạy song song: Job Object RustQuantCPU50 của cpucap không nhận tiến trình thứ hai khi đã có run; sửa cần đổi cpucap.py của claude_traders). Desk không còn chờ mãi khi run không khởi động được (lỗi sau 2 phút, hoặc 20 giây sau khi tiến trình run chết). Đã kiểm: checker chặn 32/32 mẫu lách, 35/47 alpha cũ qua (12 bị chặn đúng: đọc CSV ngoài, static, > 200 KB); 2 alpha mẫu chạy smoke thật trên engine; kiểm thử đầu-cuối cục bộ 40/40 (D:\quantechcap-data-research\e2e\e2e_submit.py). Chưa kiểm trên trình duyệt thật.
Trader thứ hai (2026-10-29): duong-hoang (hậu tố alpha _duong_hoang), thư mục D:\quantechcap-data-research	raders\duong_hoang, auto_approve tắt. Bước 5 (data-is, qc_runner) Dương CHƯA đồng ý làm: alpha của trader vẫn chạy dưới tài khoản zicza và đọc được OS2; chặn duy nhất là checker + duyệt tay.
Báo lỗi vận hành cho trader (2026-10-29): web có luồng giám sát 30 giây/lần: bản nộp "đang kiểm tra" quá 3 phút -> lỗi "hãy nộp lại"; job đang chạy không có cập nhật 5 phút trong khi data-server online liên tục >= 2 phút -> lỗi "Chạy lại job này" (desk gửi tiếp cập nhật thì tự trả về đang chạy). Banner + ghi chú khi data-server offline (việc sẽ tự xử lý khi online lại); job chờ hiện số job đứng trước. Desk: chờ khi có run khác giữ giới hạn CPU (kể cả run ngoài web) và báo trader, thử khởi động lại tối đa 4 lần; khi khởi động lại thì nhận lại job dở (run còn sống -> theo dõi tiếp; đã xong -> gửi kết quả; đã chết -> báo lỗi); thông báo tiến độ tiếng Việt, cảnh báo khi 30 phút không có tiến độ; lỗi engine trích lý do từ log (không lộ đường dẫn), quá thời gian thì báo thu hẹp job. Nút "Chạy lại job này" cho job lỗi/hủy. Kiểm thử: D:\quantechcap-data-research\e2e\e2e_ops.py.
Hardening VPS đã làm: chặn cổng 81 (admin NPM) và 9443 (Portainer) từ internet bằng DOCKER-USER trong /etc/ufw/after.rules, after6.rules (vào qua SSH tunnel); tắt SSH mật khẩu (kiểm phiên khóa mới OK); fail2ban sshd; unattended-upgrades vốn đã bật. Sao lưu cấu hình trước khi sửa: /opt/backup/pre-qc-20261028-1510.

Chưa làm: cắt dữ liệu `data-is` (hiện `data_folder` trỏ thẳng vào `data-tickstory` đầy đủ), tài khoản Windows quyền thấp, cổng OS1, sao lưu, hạn mức và ledger.

Lưu ý bảo mật: token desk nằm dạng chữ thường trong `desk_config.json` và `web_config.json`. Cần đổi và quản lý riêng trước khi đưa lên VPS. Bật `secure_cookie` khi dùng HTTPS.

---

## 6. Tần suất máy bàn gọi lên web

Câu hỏi này Dương nêu trong hội thoại; theo code `qc_desk.py` hiện tại:
- Vòng chính (long-poll): khi rảnh, giữ kết nối tối đa 25 giây để nhận job tức thì. Khi đang chạy job, chỉ chờ 4 giây để vẫn phục vụ yêu cầu xem trang lệnh/equity và nhận job kế tiếp ngay khi xong.
- Tiến độ job: cập nhật khoảng 8 giây một lần.
- Danh mục alpha: quét mỗi 60 giây, chỉ gửi lên web khi có thay đổi, và gửi lại đầy đủ mỗi 10 lần quét.
- Mất kết nối: thử lại với thời gian chờ tăng dần, tối đa 30 giây.

---

## 7. Rủi ro và việc còn mở

Rủi ro chính:
1. **OS2 đọc được:** chốt chặn `todate` chỉ chống nhầm. Cách chắc duy nhất là worker chỉ thấy bộ dữ liệu đã cắt tại 2026-01-01 (`data-is`). `data-is` vẫn chứa OS1 thô, nên cổng OS1 chỉ là kiểm soát quy trình.
2. **Mã alpha lạ chạy trên máy bàn:** cần tài khoản quyền thấp cho khâu biên dịch và chạy (cần Administrator, chưa làm).
3. **Hàng đợi:** mỗi run nặng khoảng 2 giờ, nhiều trader phải xếp hàng. Chưa đo thông lượng.
4. **Sao lưu:** chưa có cho data (627 GB), báo cáo (14 GB), alpha.
5. **Dung lượng VPS 40 GB:** mỗi `results.sqlite` có thể trên 100 MB, nên chỉ giữ phần cần xem và xóa báo cáo cũ.
6. **Chi phí AI:** có `cost.csv` và lệnh `/cost`, cần gom và đặt hạn mức.
7. **Điều khoản:** chưa kiểm tra điều khoản dữ liệu (TickStory, broker), Anthropic, OVH, Cloudflare.

Việc cần Dương cung cấp hoặc quyết:
1. Dung lượng trống của máy bàn và ổ dùng cho cây `quantechcap`/`data-is`.
2. Phạm vi symbol cho trader (25 cặp FX hay cả chỉ số, hàng hóa, crypto).
3. VPS: hệ điều hành và đã có SSH chưa.
4. TLS Cloudflare: DNS-only hay proxy.
5. Danh sách trader và slug.
6. Vị trí chính xác của `quytrinh.md` (chưa tìm thấy; Dương nói nằm ở thư mục Documents).

Chưa kiểm chứng: toàn bộ phần VPS, hành vi Cloudflare, công cụ cắt TICKBIN1 trên toàn bộ file (mới kiểm tra header và hai bản ghi của file EURUSD), viewer với SQLite đã cắt và nhiều thể hiện song song, phân quyền Windows, thông lượng nhiều trader.

---

## 8. Lộ trình

- Giai đoạn 0 (đang làm): bản thử web → desk với một trader (duong-nguyen), chạy cục bộ, chưa giấu OS2.
- Giai đoạn 1: cắt dữ liệu `data-is`. Điều kiện bắt buộc trước khi cho trader thật chạy backtest.
- Giai đoạn 2: tài khoản quyền thấp, hàng đợi, ledger; dời cây sang ổ riêng.
- Giai đoạn 3: dựng VPS (Caddy, đăng nhập, viewer, cổng nhận báo cáo), DNS research.quantechcap.com, thử với 1-2 trader.
- Giai đoạn 4: cổng OS1, agent tổng hợp, hạn mức, báo cáo chi phí, sao lưu; mở lên 5 trader.

Tiêu chí qua cửa mỗi giai đoạn: trader tự đi hết chu kỳ (ý tưởng → alpha → smoke → optimize IS → xem web) mà không cần Dương can thiệp, và kiểm thử chủ động xác nhận không có truy cập vượt quyền (alpha/agent không đọc được OS2, dữ liệu của trader khác, token).
