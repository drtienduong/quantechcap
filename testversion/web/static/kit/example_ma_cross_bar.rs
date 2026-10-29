// QuanTechCap - ALPHA MẪU 1: tín hiệu theo NẾN, vào lệnh ở TICK kế tiếp.
// Ý tưởng: đường trung bình nhanh cắt đường trung bình chậm (SMA cross).
//  - on_bar(): cập nhật chỉ báo khi một nến vừa đóng, ghi lại tín hiệu chờ.
//  - on_tick(): đặt lệnh với SL/TP tính từ giá bid/ask thật của tick hiện tại.
// Đổi tên file và chuỗi trong name() thành <tên_alpha>_<tên_trader>, ví dụ alpha_ma_cross_nguyen_an.

const MAGIC: u64 = 1001;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Pending {
    Buy,
    Sell,
}

struct MaCross {
    fast: usize,
    slow: usize,
    sl_points: f64,
    tp_points: f64,
    closes: Vec<f64>,
    prev_fast: Option<f64>,
    prev_slow: Option<f64>,
    pending: Option<Pending>,
}

fn sma(values: &[f64], period: usize) -> Option<f64> {
    if period == 0 || values.len() < period {
        return None;
    }
    let tail = &values[values.len() - period..];
    Some(tail.iter().sum::<f64>() / period as f64)
}

// Engine không cho alpha biết kích thước point của symbol: suy ra từ mức giá (JPY ~ 100+, còn lại ~ 1).
fn point_size(price: f64) -> f64 {
    if price >= 20.0 { 0.001 } else { 0.00001 }
}

impl Alpha for MaCross {
    fn name(&self) -> &'static str {
        "example_ma_cross_bar"
    }

    fn on_bar(&mut self, bar: &Bar) -> Signal {
        self.closes.push(bar.close);
        // giữ bộ đệm vừa đủ, tránh tăng bộ nhớ mãi
        let keep = self.slow + 2;
        if self.closes.len() > keep * 2 {
            let cut = self.closes.len() - keep;
            self.closes.drain(0..cut);
        }
        let (Some(f), Some(s)) = (sma(&self.closes, self.fast), sma(&self.closes, self.slow)) else {
            return Signal::Hold;
        };
        if let (Some(pf), Some(ps)) = (self.prev_fast, self.prev_slow) {
            if pf <= ps && f > s {
                self.pending = Some(Pending::Buy);
            } else if pf >= ps && f < s {
                self.pending = Some(Pending::Sell);
            }
        }
        self.prev_fast = Some(f);
        self.prev_slow = Some(s);
        Signal::Hold
    }

    fn on_tick(&mut self, tick: &Tick) -> Signal {
        let Some(side) = self.pending.take() else {
            return Signal::Hold;
        };
        let pt = point_size(tick.bid);
        // SL/TP là GIÁ tuyệt đối. Lệnh Buy: SL < bid < TP. Lệnh Sell: TP < ask < SL. Sai là run bị dừng.
        match side {
            Pending::Buy => Signal::Open {
                side: OrderSide::Buy,
                volume: 0.0, // 0 = dùng "Lot" trong form chạy job
                stop_loss: if self.sl_points > 0.0 { Some(tick.bid - self.sl_points * pt) } else { None },
                take_profit: if self.tp_points > 0.0 { Some(tick.bid + self.tp_points * pt) } else { None },
                magic: MAGIC, // cùng magic: lệnh ngược chiều sẽ tự đóng lệnh đang mở
                comment: "ma_cross_buy",
            },
            Pending::Sell => Signal::Open {
                side: OrderSide::Sell,
                volume: 0.0,
                stop_loss: if self.sl_points > 0.0 { Some(tick.ask + self.sl_points * pt) } else { None },
                take_profit: if self.tp_points > 0.0 { Some(tick.ask - self.tp_points * pt) } else { None },
                magic: MAGIC,
                comment: "ma_cross_sell",
            },
        }
    }
}

// Tham số: mỗi dòng đúng dạng AlphaInput::new("tên", số_mặc_định). Web đọc danh sách này để dựng form.
pub fn alpha_inputs() -> Vec<AlphaInput> {
    vec![
        AlphaInput::new("fast", 20.0),
        AlphaInput::new("slow", 50.0),
        AlphaInput::new("sl_points", 300.0),
        AlphaInput::new("tp_points", 600.0),
    ]
}

// Engine gọi hàm này một lần cho MỖI biến thể tham số (mỗi biến thể có trạng thái riêng).
pub fn create_alpha(params: &ParameterSet) -> Box<dyn Alpha> {
    let fast = params.get_i64("fast", 20).max(1) as usize;
    let slow = params.get_i64("slow", 50).max(2) as usize;
    Box::new(MaCross {
        fast: fast.min(slow - 1).max(1),
        slow,
        sl_points: params.get_f64("sl_points", 300.0).max(0.0),
        tp_points: params.get_f64("tp_points", 600.0).max(0.0),
        closes: Vec::with_capacity(slow * 4),
        prev_fast: None,
        prev_slow: None,
        pending: None,
    })
}
