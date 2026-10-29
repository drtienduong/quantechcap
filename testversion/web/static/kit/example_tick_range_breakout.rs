// QuanTechCap - ALPHA MẪU 2: chỉ dùng TICK.
// Ý tưởng: lấy biên độ cao/thấp từ đầu ngày đến giờ range_end_hour; sau đó giá phá biên (cộng thêm buffer)
// thì vào lệnh theo hướng phá; dời SL theo giá (trailing) bằng Signal::Modify; đóng hết lệnh lúc exit_hour.
// Minh họa: đọc ngày/giờ của tick, Signal::Open / Modify / Close, tự theo dõi trạng thái lệnh.
// Giờ là giờ của dữ liệu tick trên data-server (không đổi múi giờ).

const MAGIC: u64 = 2001;
const HOUR_MS: u32 = 3_600_000;

struct RangeBreakout {
    // tham số
    range_end_hour: u32,
    exit_hour: u32,
    buffer_points: f64,
    sl_points: f64,
    trail_points: f64,
    // trạng thái trong ngày
    day: u32,
    hi: f64,
    lo: f64,
    traded_today: bool,
    // trạng thái lệnh do alpha tự ghi nhớ (engine có thể đã đóng lệnh do SL; khi đó Modify/Close không làm gì)
    long: Option<bool>,
    best: f64,
    sl: f64,
}

fn point_size(price: f64) -> f64 {
    if price >= 20.0 { 0.001 } else { 0.00001 }
}

impl RangeBreakout {
    fn new_day(&mut self, date: u32) {
        self.day = date;
        self.hi = f64::MIN;
        self.lo = f64::MAX;
        self.traded_today = false;
    }
}

impl Alpha for RangeBreakout {
    fn name(&self) -> &'static str {
        "example_tick_range_breakout"
    }

    fn on_tick(&mut self, tick: &Tick) -> Signal {
        if tick.bid <= 0.0 || tick.ask <= 0.0 {
            return Signal::Hold;
        }
        // sang ngày mới: đóng lệnh còn treo từ hôm trước, đặt lại biên độ
        if tick.date != self.day {
            self.new_day(tick.date);
            if self.long.take().is_some() {
                return Signal::Close { magic: Some(MAGIC), comment: "day_change" };
            }
        }
        let hour = tick.time_of_day_ms / HOUR_MS;
        let pt = point_size(tick.bid);

        if hour < self.range_end_hour {
            self.hi = self.hi.max(tick.bid);
            self.lo = self.lo.min(tick.bid);
            return Signal::Hold;
        }
        if hour >= self.exit_hour {
            if self.long.take().is_some() {
                return Signal::Close { magic: Some(MAGIC), comment: "exit_hour" };
            }
            return Signal::Hold;
        }

        // đang có lệnh: dời SL khi giá đi đúng hướng (mỗi lần ít nhất 1 point)
        if let Some(is_long) = self.long {
            if self.trail_points <= 0.0 {
                return Signal::Hold;
            }
            if is_long {
                self.best = self.best.max(tick.bid);
                let new_sl = self.best - self.trail_points * pt;
                if new_sl > self.sl + pt && new_sl < tick.bid {
                    self.sl = new_sl;
                    return Signal::Modify { magic: MAGIC, stop_loss: Some(new_sl), take_profit: None, comment: "trail" };
                }
            } else {
                self.best = self.best.min(tick.ask);
                let new_sl = self.best + self.trail_points * pt;
                if new_sl < self.sl - pt && new_sl > tick.ask {
                    self.sl = new_sl;
                    return Signal::Modify { magic: MAGIC, stop_loss: Some(new_sl), take_profit: None, comment: "trail" };
                }
            }
            return Signal::Hold;
        }

        if self.traded_today || self.hi <= self.lo {
            return Signal::Hold;
        }
        let buf = self.buffer_points * pt;
        if tick.ask > self.hi + buf {
            self.traded_today = true;
            self.long = Some(true);
            self.best = tick.bid;
            self.sl = tick.bid - self.sl_points * pt; // Buy: SL phải < bid
            return Signal::Open {
                side: OrderSide::Buy,
                volume: 0.0,
                stop_loss: Some(self.sl),
                take_profit: None,
                magic: MAGIC,
                comment: "range_up",
            };
        }
        if tick.bid < self.lo - buf {
            self.traded_today = true;
            self.long = Some(false);
            self.best = tick.ask;
            self.sl = tick.ask + self.sl_points * pt; // Sell: SL phải > ask
            return Signal::Open {
                side: OrderSide::Sell,
                volume: 0.0,
                stop_loss: Some(self.sl),
                take_profit: None,
                magic: MAGIC,
                comment: "range_down",
            };
        }
        Signal::Hold
    }
}

pub fn alpha_inputs() -> Vec<AlphaInput> {
    vec![
        AlphaInput::new("range_end_hour", 8.0),
        AlphaInput::new("exit_hour", 21.0),
        AlphaInput::new("buffer_points", 20.0),
        AlphaInput::new("sl_points", 250.0),
        AlphaInput::new("trail_points", 300.0),
    ]
}

pub fn create_alpha(params: &ParameterSet) -> Box<dyn Alpha> {
    let range_end_hour = params.get_i64("range_end_hour", 8).clamp(1, 22) as u32;
    let exit_hour = params.get_i64("exit_hour", 21).clamp(range_end_hour as i64 + 1, 24) as u32;
    Box::new(RangeBreakout {
        range_end_hour,
        exit_hour,
        buffer_points: params.get_f64("buffer_points", 20.0).max(0.0),
        sl_points: params.get_f64("sl_points", 250.0).max(10.0),
        trail_points: params.get_f64("trail_points", 300.0).max(0.0),
        day: 0,
        hi: f64::MIN,
        lo: f64::MAX,
        traded_today: false,
        long: None,
        best: 0.0,
        sl: 0.0,
    })
}
