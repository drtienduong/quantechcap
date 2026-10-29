// =====================================================================================
// QuanTechCap alpha API (tham chiếu). KHÔNG nộp file này.
// Đây là các kiểu dữ liệu mà engine trên data-server cung cấp sẵn cho alpha của bạn.
// Trong alpha KHÔNG cần (và không được) import chúng: engine tự đưa vào phạm vi.
// File này dùng cho AI của bạn tham khảo, và để tự kiểm cú pháp bằng local_check.rs (tùy chọn).
// =====================================================================================
use std::collections::{HashMap, HashSet};

#[derive(Clone, Copy, Debug)]
pub struct Tick {
    pub time_ms: i64,
    pub date: u32,
    pub time_of_day_ms: u32,
    pub bid: f64,
    pub ask: f64,
    pub last: f64,
    pub volume: u64,
    pub quote_to_usd_positive: f64,
    pub quote_to_usd_negative: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct Bar {
    pub start_time_ms: i64,
    pub end_time_ms: i64,
    pub period_minutes: u32,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub tick_volume: u64,
}

pub struct AccountView<'a> {
    pub symbol: &'a str,
    pub balance: f64,
    pub equity: f64,
    pub used_margin: f64,
    pub free_margin: f64,
    pub positions_on_symbol: usize,
    pub total_positions: usize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Clone, Copy, Debug)]
pub enum Signal {
    Hold,
    Open {
        side: OrderSide,
        volume: f64,
        stop_loss: Option<f64>,
        take_profit: Option<f64>,
        magic: u64,
        comment: &'static str,
    },
    Close {
        magic: Option<u64>,
        comment: &'static str,
    },
    Modify {
        magic: u64,
        stop_loss: Option<f64>,
        take_profit: Option<f64>,
        comment: &'static str,
    },
}

pub struct AlphaInput {
    pub name: &'static str,
    pub default: f64,
}

impl AlphaInput {
    pub const fn new(name: &'static str, default: f64) -> Self {
        Self { name, default }
    }
}

#[derive(Clone, Debug)]
pub struct ParameterSet {
    values: Vec<(String, f64)>,
}

impl ParameterSet {
    pub fn new(values: Vec<(String, f64)>) -> Self {
        Self { values }
    }

    pub fn get_f64(&self, name: &str, default: f64) -> f64 {
        self.values
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| *value)
            .unwrap_or(default)
    }

    pub fn get_i64(&self, name: &str, default: i64) -> i64 {
        self.get_f64(name, default as f64).round() as i64
    }

    pub fn get_bool(&self, name: &str, default: bool) -> bool {
        self.get_f64(name, if default { 1.0 } else { 0.0 }) != 0.0
    }

    pub fn to_json(&self) -> String {
        let mut out = String::from("{");
        for (index, (name, value)) in self.values.iter().enumerate() {
            if index > 0 {
                out.push(',');
            }
            out.push('"');
            for ch in name.chars() {
                if ch == '"' || ch == '\\' {
                    out.push('\\');
                }
                out.push(ch);
            }
            out.push_str("\":");
            out.push_str(&value.to_string());
        }
        out.push('}');
        out
    }
}

pub trait Alpha: Send {
    fn name(&self) -> &'static str;
    fn on_bar(&mut self, _bar: &Bar) -> Signal {
        Signal::Hold
    }
    fn on_tick(&mut self, _tick: &Tick) -> Signal {
        Signal::Hold
    }
    fn on_bar_with_account(&mut self, bar: &Bar, _account: &AccountView<'_>) -> Signal {
        self.on_bar(bar)
    }
    fn on_tick_with_account(&mut self, tick: &Tick, _account: &AccountView<'_>) -> Signal {
        self.on_tick(tick)
    }
}
