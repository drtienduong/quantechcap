# Hướng dẫn viết và nộp alpha QuanTechCap

## Quy trình
1. Tải bộ khung trên web (trang "Nộp alpha"): `AI_GUIDE.md`, `qc_alpha_api.rs`, `alpha_template.rs`, 2 alpha mẫu.
2. Đưa toàn bộ các file này cho AI của bạn (Claude, ChatGPT...) kèm ý tưởng giao dịch. Yêu cầu AI đọc `AI_GUIDE.md`
   trước và viết đúng theo đó. Chỉ cần MỘT file `.rs`.
3. Đặt tên file: `<tên_alpha>_<hậu tố của bạn>.rs` (hậu tố hiện trên trang nộp alpha). Chỉ chữ, số, dấu `_`.
4. Nộp file trên web. Data-server kiểm tra tự động trong vài giây:
   - Lỗi quy tắc an toàn hoặc lỗi biên dịch: web hiện lỗi kèm số dòng. Đưa lỗi cho AI sửa rồi nộp lại.
   - Đạt: alpha vào hàng chờ duyệt (trừ khi bạn được cấp quyền tự duyệt).
5. Duyệt xong, alpha xuất hiện ở trang "Alpha". Chạy Smoke test trước (khoảng ngắn, 1 symbol), rồi mới Optimize IS.

## Quy tắc chính (chi tiết trong AI_GUIDE.md)
- Alpha chỉ được tính toán trên dữ liệu engine đưa vào (tick, nến). Không đọc file, không mạng, không dữ liệu ngoài,
  không chạy chương trình khác, không `unsafe`, không biến `static`.
- Tham số khai báo đúng dạng `AlphaInput::new("tên", số)`. Web đọc danh sách này để dựng form tối ưu.
- SL/TP là giá tuyệt đối và phải nằm đúng phía giá hiện tại; sai là run bị dừng.
- Dữ liệu chạy được: các cặp FX, giai đoạn IS (đến 2024-01-01).

## Lưu ý
- Mã nguồn bạn nộp được lưu trên data-server của QuanTechCap cùng báo cáo. Web chỉ giữ mã tạm thời cho đến khi
  data-server nhận về.
- Mỗi lần nộp lại cùng tên là một phiên bản mới; các báo cáo cũ vẫn giữ bản mã đã dùng để chạy.
