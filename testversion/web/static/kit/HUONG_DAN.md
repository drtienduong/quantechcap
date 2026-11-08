# Hướng dẫn viết và nộp alpha QuanTechCap

## Quy trình
1. Bộ khung luôn ở trang https://research.quantechcap.com/guide_ai (công khai, bản mới nhất): `AI_GUIDE.md`,
   `qc_alpha_api.rs`, `alpha_template.rs`, 2 alpha mẫu.
2. Đưa link trang cho AI của bạn (Claude, ChatGPT...) kèm ý tưởng giao dịch. Yêu cầu AI đọc toàn bộ trang, nhất là
   `AI_GUIDE.md`, và viết đúng theo đó. Chỉ cần MỘT file `.rs`. AI không đọc được web thì tải bản offline (.zip)
   trên cùng trang và đính kèm vào chat.
3. Đặt tên file: `<tên_alpha>_<hậu tố của bạn>.rs` (hậu tố hiện trên trang nộp alpha). Chỉ chữ, số, dấu `_`.
4. Nộp file trên web. Data-server kiểm tra tự động trong vài giây:
   - Lỗi quy tắc an toàn hoặc lỗi biên dịch: web hiện lỗi kèm số dòng. Đưa lỗi cho AI sửa rồi nộp lại.
   - Đạt: alpha vào hàng chờ duyệt (trừ khi bạn được cấp quyền tự duyệt).
5. Duyệt xong, alpha xuất hiện ở trang "Alpha". Chạy Smoke test trước (khoảng ngắn, 1 symbol), rồi mới Optimize IS.
6. Biến thể pass tiêu chí IS (Sharpe IS > 1, Sharpe 3 năm gần nhất > 0.5, giữ lệnh 1-70%, fitness > 1, không stop-out)
   hiện ở trang "Alpha pass IS". Chọn biến thể rồi nộp chạy OS1 (2024-2026); mỗi biến thể chỉ chạy OS1 một lần, đúng bản
   alpha và cấu hình đã pass IS. Kết quả ở trang "Lịch sử OS1". Tiêu chí OS1: có đỉnh balance mới, DD OS1 không vượt DD lớn
   nhất của IS, Sharpe OS1 > 0.7 x Sharpe IS.

## Quy tắc chính (chi tiết trong AI_GUIDE.md)
- Alpha chỉ được tính toán trên dữ liệu engine đưa vào (tick, nến). Không đọc file, không mạng, không dữ liệu ngoài,
  không chạy chương trình khác, không `unsafe`, không biến `static`.
- Tham số khai báo đúng dạng `AlphaInput::new("tên", số)`. Web đọc danh sách này để dựng form tối ưu.
- Alpha chỉ đúng trên một khung nến thì khai báo `const REQUIRED_PERIOD: &str = "M5";`. Web chọn sẵn khung đó và
  cảnh báo nếu bạn chọn khung khác. Chạy sai khung thường ra 0 lệnh.
- SL/TP là giá tuyệt đối và phải nằm đúng phía giá hiện tại; sai là run bị dừng.
- Tài khoản mô phỏng: leverage mặc định 1:1000 (form chỉ cho chọn thấp hơn), margin call 100%, stop-out 50%.
  Lệnh mới bị từ chối khi không đủ free margin. Margin level dưới 50% thì engine đóng lệnh lỗ lớn nhất trước rồi
  dừng backtest của biến thể đó (biến thể bị loại). Crypto Binance (*_BN) không tính margin.
- Dữ liệu chạy được (giai đoạn IS, đến 2024-01-01): 29 cặp FX, vàng/bạc, dầu Brent/WTI, 8 chỉ số, crypto (BTC, ETH,
  dữ liệu Binance *_BN). Alpha chạy cả symbol ngoài FX thì nên đặt SL/TP theo % giá hoặc ATR, không theo point FX.

## Lưu ý
- Mã nguồn bạn nộp được lưu trên data-server của QuanTechCap cùng báo cáo. Web chỉ giữ mã tạm thời cho đến khi
  data-server nhận về.
- Mỗi lần nộp lại cùng tên là một phiên bản mới; các báo cáo cũ vẫn giữ bản mã đã dùng để chạy.
