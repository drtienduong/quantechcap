// QuanTechCap - KHUNG ALPHA. Sao chép file này, đổi tên thành <tên_alpha>_<tên_trader>.rs
// (chỉ chữ thường/hoa, số, dấu _), sửa phần TODO. Một alpha = một file .rs duy nhất.
// Các kiểu Tick, Bar, AccountView, OrderSide, Signal, AlphaInput, ParameterSet, Alpha, HashMap, HashSet
// có sẵn, KHÔNG import. Xem qc_alpha_api.rs và AI_GUIDE.md.

const MAGIC: u64 = 1; // số nhận diện lệnh của alpha; cùng magic = cùng một vị thế

// Tùy chọn: nếu logic chỉ đúng trên MỘT khung nến, bỏ // ở dòng dưới và đặt khung (M1, M5, M15, M30, H1, H4, D1).
// Web sẽ chọn sẵn khung này và cảnh báo khi chọn khung khác (chạy sai khung thường ra 0 lệnh).
// const REQUIRED_PERIOD: &str = "H1";

struct MyAlpha {
    // TODO: tham số
    period: usize,
    // TODO: trạng thái
    closes: Vec<f64>,
}

impl Alpha for MyAlpha {
    fn name(&self) -> &'static str {
        "alpha_myidea_trader" // TODO: trùng tên file (không có .rs)
    }

    // Gọi khi một nến (khung chọn trên web: M1..D1) vừa đóng. Nến dựng từ giá bid.
    fn on_bar(&mut self, bar: &Bar) -> Signal {
        self.closes.push(bar.close);
        if self.closes.len() > self.period * 2 {
            self.closes.remove(0);
        }
        // TODO: tính chỉ báo, quyết định
        Signal::Hold
    }

    // Gọi ở MỌI tick (rất nhiều lần): giữ nhẹ, không cấp phát bộ nhớ trong hàm này.
    fn on_tick(&mut self, _tick: &Tick) -> Signal {
        // TODO: vào/ra lệnh nếu cần độ chính xác theo tick
        let _ = MAGIC;
        Signal::Hold
    }
}

pub fn alpha_inputs() -> Vec<AlphaInput> {
    vec![
        AlphaInput::new("period", 20.0), // TODO: mỗi tham số một dòng, giá trị mặc định là số viết thẳng
    ]
}

pub fn create_alpha(params: &ParameterSet) -> Box<dyn Alpha> {
    let period = params.get_i64("period", 20).max(1) as usize;
    Box::new(MyAlpha { period, closes: Vec::new() })
}
