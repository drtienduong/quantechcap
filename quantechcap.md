# QuanTechCap - Mô tả dự án

Chủ dự án: Dương. Nguồn: tóm tắt hội thoại `tomtathoithoai.txt` (26/10/2026), đối chiếu với thiết kế `C:\Claude\workspace\claude_traders\QUANTECHCAP_DESIGN.md` (v0.2) và code thử nghiệm trong `claude_traders\quantechcap\`.

Ghi chú: mục nào ghi "chưa kiểm chứng" nghĩa là chưa ai đo hoặc thử.

---

## 1. Mục đích

Hệ thống cho một nhóm quant trader cùng nghiên cứu alpha forex:

- Mục tiêu của mỗi trader: tạo alpha vượt bộ lọc IS (sau đó OS1 và xác nhận). OS2 là holdout mù do Dương giữ.
- Mọi trader dùng chung một bộ dữ liệu tick (`data-tickstory`, khoảng 627 GB) nằm trên máy bàn của Dương.
- Trader dùng AI để viết strategy (alpha) theo mẫu, chạy backtest/optimize bằng công cụ chung, rồi xem báo cáo trên web.
- Mỗi trader có một tên miền riêng dạng `ho-ten.quantechcap.com`. Hostname không cho dấu gạch dưới hay dấu tiếng Việt, nên dùng gạch ngang và ASCII (ví dụ `nguyen-an`).
- Dương thấy tất cả. Trader không thấy alpha của nhau.

Ngoài phạm vi giai đoạn đầu: giao dịch thật, thanh toán, chia sẻ dữ liệu thô ra ngoài.

---

## 2. Các quyết định đã chốt

1. Dương sở hữu `quantechcap.com`, DNS ở Cloudflare.
2. Máy bàn của Dương chạy 24/7.
3. Trader đăng nhập bằng email và mật khẩu do Dương cấp trực tiếp. Không đăng ký tự do.
4. Web server đặt ở VPS OVHcloud Singapore: 2 vCore, 4 GB RAM, đĩa 40 GB.
5. **Máy bàn tính toán, VPS chỉ hiển thị.** Máy bàn luôn là bên gọi ra VPS, VPS không có đường vào máy bàn. Lý do: bộ tick quá nặng để kéo qua mạng, VPS nhỏ không chạy nổi optimizer, và nếu VPS bị tấn công thì không có đường vào máy bàn.
6. **Mã alpha ở lại máy bàn** (cách 1):
 - Alpha do các agent con viết cùng trader qua Telegram.
 - Alpha lưu trong thư mục alpha của trader trên máy bàn.
 - Web chỉ chọn alpha có sẵn trong danh sách. Web không nhận và không gửi mã.
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
 - web: đăng nhập, form job,          - bot + agent của từng trader (viết alpha)
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

Thành phần máy bàn: bot Telegram và agent, desk, worker, thư mục alpha và report của từng trader.
Thành phần VPS (đề xuất): Caddy (TLS, định tuyến theo subdomain), lớp đăng nhập, viewer.
Cloudflare: DNS wildcard `*.quantechcap.com` trỏ về VPS. Chọn DNS-only hay bật proxy thì chưa quyết.

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

Chưa làm: cắt dữ liệu `data-is` (hiện `data_folder` trỏ thẳng vào `data-tickstory` đầy đủ), tài khoản Windows quyền thấp, cổng OS1, publisher lên VPS, dựng VPS, sao lưu, hạn mức và ledger.

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
- Giai đoạn 3: dựng VPS (Caddy, đăng nhập, viewer, cổng nhận báo cáo), DNS wildcard, thử với 1-2 trader.
- Giai đoạn 4: cổng OS1, agent tổng hợp, hạn mức, báo cáo chi phí, sao lưu; mở lên 5 trader.

Tiêu chí qua cửa mỗi giai đoạn: trader tự đi hết chu kỳ (ý tưởng → alpha → smoke → optimize IS → xem web) mà không cần Dương can thiệp, và kiểm thử chủ động xác nhận không có truy cập vượt quyền (alpha/agent không đọc được OS2, dữ liệu của trader khác, token).
