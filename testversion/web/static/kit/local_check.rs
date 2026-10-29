// Tự kiểm cú pháp alpha trên máy bạn (TÙY CHỌN, cần cài Rust: https://rustup.rs).
// Đặt 3 file cạnh nhau: local_check.rs, qc_alpha_api.rs, my_alpha.rs (bản sao alpha của bạn), rồi chạy:
//     rustc --edition=2024 --crate-type=lib --emit=metadata local_check.rs
// Không lỗi nghĩa là alpha đúng kiểu với API. Quy tắc an toàn (cấm file, mạng, static...) chỉ được kiểm khi nộp lên web.
#![allow(dead_code, unused_imports)]
include!("qc_alpha_api.rs");

mod alpha_impl {
    use super::*;
    include!("my_alpha.rs");
}

fn _uses_required_functions() {
    let params = ParameterSet::new(Vec::new());
    let _alpha: Box<dyn Alpha> = alpha_impl::create_alpha(&params);
    let _inputs: Vec<AlphaInput> = alpha_impl::alpha_inputs();
}
