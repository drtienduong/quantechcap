# QuanTechCap alpha guide (for the trader's AI assistant)

You are helping a trader write ONE Rust source file (an "alpha") for the QuanTechCap backtest/optimize engine.
The file is uploaded on the QuanTechCap website, checked automatically, reviewed, then compiled and run on
QuanTechCap's data-server. The trader cannot run the engine locally, so the file must be correct on the first try.

Files in this kit (all shown on https://research.quantechcap.com/guide_ai, plain text at
https://research.quantechcap.com/guide_ai.md, offline copy in the zip linked there; that page is always the latest version):
- `qc_alpha_api.rs`: the exact API (types, trait) the engine provides. Read it first. Do NOT submit it.
- `alpha_template.rs`: skeleton to start from.
- `example_ma_cross_bar.rs`: bar-based signal, tick-based execution, SL/TP.
- `example_tick_range_breakout.rs`: tick-only logic, time of day, Open/Modify/Close, trailing stop.
- `reserved_names.txt`: names you must not use for your own items unless you define them yourself (see Rule 6).
- `local_check.rs`: optional local type-check with `rustc` (instructions inside).

## 1. File shape (required)

- One `.rs` file, Rust edition 2024, plain `std` only, no external crates, no `Cargo.toml`.
- File name: `<alpha_name>_<trader_suffix>.rs`, only `[A-Za-z0-9_]`, max 80 chars. The trader knows their suffix
  (shown on the upload page). `fn name()` must return the same name as a string literal.
- The API types (`Tick`, `Bar`, `AccountView`, `OrderSide`, `Signal`, `AlphaInput`, `ParameterSet`, `Alpha`,
  `HashMap`, `HashSet`) are already in scope. Do NOT import or redefine them.
- Must contain exactly these public functions:
  ```rust
  pub fn alpha_inputs() -> Vec<AlphaInput> { vec![ AlphaInput::new("param_a", 20.0), /* ... */ ] }
  pub fn create_alpha(params: &ParameterSet) -> Box<dyn Alpha> { /* read params, build struct */ }
  ```
  and one `impl Alpha for YourStruct { fn name(&self) -> &'static str { "..." } ... }`.

### Required timeframe (recommended)

If the logic only makes sense on one bar timeframe (e.g. it checks `bar.period_minutes`, or "first M5 bar of the
week"), declare it once at the top of the file:
```rust
const REQUIRED_PERIOD: &str = "M5";   // one of: M1, M5, M15, M30, H1, H4, D1
```
Exactly this form (string literal, declared once). The website then pre-selects this timeframe on the job form and
warns the trader if another one is chosen. A job run on a different timeframe usually produces ZERO trades, which is
the most common mistake. Tick-only or timeframe-agnostic alphas should not declare it.
You may still guard with `if bar.period_minutes != 5 { return Signal::Hold; }`, but always declare REQUIRED_PERIOD too.

## 2. Parameters

- Declare each parameter as `AlphaInput::new("name", <plain numeric literal>)`, e.g. `AlphaInput::new("period", 20.0)`.
  The website discovers parameters by scanning this exact text pattern. No constants, no expressions, no `_` in numbers,
  no exponent. At least 1 parameter, at most 64. Names: `[A-Za-z0-9_]`, unique.
- Never write `AlphaInput::new(` inside comments or strings.
- All values are `f64`. Read them in `create_alpha` with `params.get_f64(name, default)`,
  `params.get_i64(name, default)` (rounded) or `params.get_bool(name, default)` (non-zero = true).
  Clamp/validate values yourself (`.max(1)`, `.clamp(..)`), the optimizer may send any value in the grid.
- The trader sets the optimization grid (start, step, stop) per parameter on the website.

## 3. How the engine calls the alpha

- `create_alpha` is called once per parameter combination (variant). Variants run in parallel threads, each with its
  own instance. Keep ALL state in your struct fields.
- Data: real ticks (bid/ask) of the chosen symbols, in-sample period only (up to 2024-01-01).
  Time is the tick data's own clock (`tick.time_ms` epoch ms, `tick.date` = yyyymmdd, `tick.time_of_day_ms`).
- Bars are built by the engine from BID prices for the timeframe chosen on the website (M1, M5, M15, M30, H1, H4, D1),
  aligned to the epoch. `on_bar(&Bar)` is called with the just-completed bar when the FIRST tick of the next bar
  arrives. The last unfinished bar is never delivered.
- Order of work for every tick: (1) engine checks SL/TP of open positions and closes hits at the current tick price,
  (2) if a bar just completed: `on_bar` is called and its `Signal` is executed at this tick, (3) `on_tick` is called and
  its `Signal` is executed at this tick.
- `on_tick` runs on every tick (tens of millions per year per symbol, times the number of variants). Keep it O(1):
  no allocation, no loops over history. Do heavy work in `on_bar`.
- Modes chosen on the website:
  - "Independent" (each symbol separately): engine calls `on_bar` / `on_tick`.
  - "Portfolio" (one shared account, one alpha instance per symbol): engine calls `on_bar_with_account` /
    `on_tick_with_account` (their default implementations forward to `on_bar` / `on_tick`).
    `AccountView` gives `symbol`, `balance`, `equity`, `used_margin`, `free_margin`, `positions_on_symbol`, `total_positions`.
  If you only override the `*_with_account` methods, the alpha will not trade in Independent mode, unless you also
  override `fn wants_account(&self) -> bool { true }` (then Independent mode calls them with that symbol's own account).
- The alpha is not told the symbol or its point size in Independent mode. The pattern
  `fn point_size(price: f64) -> f64 { if price >= 20.0 { 0.001 } else { 0.00001 } }` is only valid for FX
  (JPY pairs vs others). Symbols available besides FX: metals (XAUUSD, XAGUSD, COPPERCMDUSD), energy (BRENTCMDUSD, LIGHTCMDUSD),
  agricultural (COCOACMDUSD, SUGARCMDUSD in USD/tonne; COFFEECMDUSX, COTTONCMDUSX, OJUICECMDUSX quoted in US CENTS per lb,
  data from late 2017 only),
  indices (USA500IDXUSD, USATECHIDXUSD, DEUIDXEUR, GBRIDXGBP, EUSIDXEUR, ESPIDXEUR, AUSIDXAUD, HKGIDXHKD), crypto (BTCUSD, ETHUSD and
  Binance *_BN pairs). If the alpha may run on non-FX symbols, express SL/TP and thresholds RELATIVE to price
  (ATR multiples or a percent of price), never as FX points.

## 4. Orders (return a `Signal`)

- `Signal::Hold`: do nothing.
- `Signal::Open { side, volume, stop_loss, take_profit, magic, comment }`:
  - Buy fills at ask, Sell fills at bid. `volume <= 0.0` means "use the Lot from the job form" (recommended).
  - One net position per `magic`. Open on a magic that already has a position on the same side: ignored.
    Opposite side: the existing position is closed first (reason `opposite_open`), then the new one opens.
  - `stop_loss` / `take_profit` are absolute PRICES (`Option<f64>`), not distances.
    Buy: SL < current bid < TP. Sell: TP < current ask < SL. Must be finite.
    **Invalid SL/TP stops the whole run with an error.** Always compute them from the current tick's bid/ask with
    a safe distance.
- `Signal::Modify { magic, stop_loss, take_profit, comment }`: replaces SL/TP of the position with this magic
  (same validity rules). No position: nothing happens.
- `Signal::Close { magic: Some(m), comment }` closes that position; `magic: None` closes all positions of this alpha
  instance. No position: nothing happens.
- `comment` must be a string literal (`&'static str`), it appears as the open/close reason in reports.
- The engine does not tell the alpha when SL/TP closed a position (except `positions_on_symbol` in Portfolio mode).
  Track your own belief; Modify/Close on a closed position are harmless no-ops.

## 4b. Account rules (margin call, stop-out)

The engine simulates a broker account in USD. Defaults: leverage **1:1000**, margin call level **100%**,
stop-out level **50%**. Binance crypto symbols (`*_BN`) are exempt: they use no margin and are never blocked.

- Margin of a position (USD) = lots x contract size x current mid price x conversion to USD / leverage,
  recomputed on every tick at the current price. Examples at 1:1000: EURUSD 1 lot at 1.10 = 110 USD;
  USDJPY 1 lot = 100 USD at any price; XAUUSD 1 lot (100 oz) at 2000 = 200 USD.
- Equity = balance + floating P/L. Free margin = equity - used margin. Margin level = equity / used margin x 100.
- **New orders are rejected** (silently, the Open is ignored) if after opening free margin would be < 0 or
  margin level < margin call level. Modify and Close are always allowed. Rejections are counted in the report.
- **Stop-out**: checked on every tick. If margin level < stop-out level, the engine closes the position with the
  largest loss first (reason `stop_out`), repeats until margin level is back above the level, then closes all
  remaining positions (reason `stop_out_end`) and **the backtest of that variant ends** (no more trading,
  equity stays flat to the end date). A stopped-out variant is a failed variant.
- In Portfolio mode all symbols share one account; in Independent mode each symbol has its own account.
- `AccountView` (Portfolio mode, or Independent with `wants_account() = true`) gives `equity`, `used_margin`,
  `free_margin`, `leverage`, `contract_size`, `margin_call_level`, `stopout_level`: size positions from them
  (e.g. risk a percent of equity) instead of a fixed lot when possible.
- Optional: the alpha may declare stricter rules by overriding `account_leverage()` (e.g. `Some(100.0)` = 1:100),
  `margin_call_level()` or `stopout_level()` (percent). Only stricter values are accepted on the platform
  (leverage <= 1000, margin call >= 100, stop-out >= 50); looser values are ignored.

## 5. Safety rules (enforced by the automatic checker; a violation rejects the upload)

The alpha may only compute on the data the engine passes in. It must not do anything else.

Allowed:
- Your own `struct`, `enum`, `impl`, `fn`, `const`, `trait`, `type`, closures, generics.
- `std::` modules ONLY from: `collections, cmp, f32, f64, i8..i128, u8..u128, isize, usize, fmt, ops, iter, convert,
  option, result, string, vec, boxed, num, mem, hash, marker, default, clone, borrow, char, str, slice, array, rc, cell, sync`
  (same for `core::`).
- Macros ONLY: `vec!, format!, matches!, assert!, assert_eq!, assert_ne!, debug_assert!, debug_assert_eq!,
  debug_assert_ne!, panic!, unreachable!`.
- Attributes ONLY: `#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]`, `#[allow(..)]`,
  `#[inline]`, `#[must_use]`, `#[cold]`, `#[rustfmt::skip]`.

Forbidden (rejected):
- Files, network, processes, environment, time/clock, threads: `std::fs, io, net, process, env, thread, time, path,
  os, ffi, ptr, panic, ...` and `File`, `Path`, `env`, `BufReader`... (any `std::` module not in the allowed list).
- External data of any kind (CSV, web, embedded files). Only engine-provided ticks/bars.
- `unsafe`, `extern`, `static` (use struct fields instead), `mod`, `crate::`, `super::`, `self::<engine name>`,
  `async/await`, `macro_rules!`, inner attributes `#![...]`, any other attribute.
- `println!`, `eprintln!`, `dbg!`, `print!`, `include!`, `include_str!`, `env!`, `concat!`, `asm!` and every macro not
  listed above.
- Non-ASCII identifiers, bidi/control characters. (Non-ASCII text in comments and strings is fine.)
- File larger than 200 KB or 5000 lines.

## 6. Reserved names

The engine has internal items (listed in `reserved_names.txt`, e.g. `Position`, `Side`, `Metrics`, `File`, `path`,
`env`). Do not reference them. You may define an item with such a name yourself (it then shadows the engine's),
but simpler: choose different names (e.g. `MyPos`, `TradeSide`).

## 7. Robustness checklist

- No panics: no `unwrap()` on values that can be `None`, no integer division by zero, no out-of-range slice indexes.
  A panic fails the run.
- Guard against `tick.bid <= 0.0 || tick.ask <= 0.0` (bad ticks).
- Bounded memory: keep rolling windows (`VecDeque` or trim `Vec`), never grow a buffer forever.
- Deterministic: same input gives same output (no randomness unless seeded from parameters, no clocks).
- Use `const MAGIC: u64` values per logical position; reuse the same magic to manage one position.
- Do not look ahead: decide only from data already received.

## 8. Before submitting

1. The file follows sections 1 to 6. If the logic depends on one timeframe, `REQUIRED_PERIOD` is declared.
2. Optional: run `local_check.rs` (needs Rust installed) to catch type errors.
3. Upload on the website. The page shows checker errors with line numbers and compile errors from your file only.
   Fix and upload again. After approval, run a Smoke test first (short period, 1 symbol), then Optimize IS.
