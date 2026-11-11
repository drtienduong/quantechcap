"""QuanTechCap - phần dùng chung cho web (VPS) và desk (data-server).

- validate_job(): kiểm tra cấu hình job có cấu trúc từ form. CẢ web VÀ desk đều gọi; desk không tin web.
- build_ini(): dựng input-optimize.ini từ cấu hình đã kiểm tra. Web KHÔNG BAO GIỜ nhận file .ini thô.
"""
import math
import re
from datetime import datetime

# 25 cặp FX chuẩn (PORTFOLIO_25 của rust-quant)
FX25 = ("AUDCAD AUDJPY AUDNZD AUDUSD CADCHF EURAUD EURCAD EURCHF EURGBP EURJPY EURNZD EURUSD "
        "GBPAUD GBPCAD GBPCHF GBPNZD GBPUSD NZDUSD USDCHF USDJPY AUDCHF CADJPY CHFJPY GBPJPY USDCAD").split()
PERIODS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

# Toàn bộ symbol trader được chạy (theo data-tickstory, 2026-10-31). USDHKD chỉ dùng quy đổi HKD -> USD, không cho chạy.
# symbol: (nhóm, point value / 1 lot (lãi khi giá đổi 1.0), ngày bắt đầu dữ liệu, có swap synthetic)
# Point value lấy theo cấu hình Dương đang dùng trong rust-quant/alpha-source/*.ini.
_FX_START = {"AUDCAD": "2006.01.03", "AUDCHF": "2006.03.01", "AUDJPY": "2003.12.01", "AUDNZD": "2006.12.12", "AUDUSD": "2003.08.04",
             "CADCHF": "2006.01.03", "CADJPY": "2004.10.25", "CHFJPY": "2003.08.04", "EURAUD": "2005.10.09", "EURCAD": "2004.10.25",
             "EURCHF": "2003.08.04", "EURGBP": "2003.08.04", "EURJPY": "2003.08.04", "EURNZD": "2006.01.03", "EURUSD": "2003.05.05",
             "GBPAUD": "2006.03.22", "GBPCAD": "2006.01.03", "GBPCHF": "2003.08.04", "GBPJPY": "2003.08.04", "GBPNZD": "2006.01.03",
             "GBPUSD": "2003.05.05", "NZDUSD": "2003.08.04", "USDCAD": "2003.08.04", "USDCHF": "2003.05.05", "USDJPY": "2003.05.05",
             "NZDCAD": "2006.01.03", "NZDCHF": "2006.01.03", "NZDJPY": "2006.01.03", "USDCNH": "2012.06.27"}
SYMBOLS = {s: ("FX", 100000.0, d, True) for s, d in _FX_START.items()}
SYMBOLS.update({
    "XAUUSD": ("Kim loại", 100.0, "2003.05.05", True),
    "XAGUSD": ("Kim loại", 5000.0, "2003.05.05", False),
    "BRENTCMDUSD": ("Năng lượng", 1000.0, "2010.12.03", False),
    "LIGHTCMDUSD": ("Năng lượng", 1000.0, "2013.01.01", False),
    "USA500IDXUSD": ("Chỉ số", 1.0, "2011.09.19", False),
    "USATECHIDXUSD": ("Chỉ số", 1.0, "2011.09.19", False),     # thêm 2026-11-03 từ TickStory CSV
    "DEUIDXEUR": ("Chỉ số", 1.0, "2011.09.19", False),
    "GBRIDXGBP": ("Chỉ số", 1.0, "2011.09.19", False),
    "EUSIDXEUR": ("Chỉ số", 1.0, "2011.09.19", False),
    "ESPIDXEUR": ("Chỉ số", 1.0, "2017.12.01", False),
    "AUSIDXAUD": ("Chỉ số", 1.0, "2013.01.03", False),
    "HKGIDXHKD": ("Chỉ số", 100.0, "2011.09.19", False),
    "BTCUSD": ("Crypto", 1.0, "2017.05.07", False),
    "ETHUSD": ("Crypto", 1.0, "2017.12.11", False),
    "BTCUSDT_BN": ("Crypto", 1.0, "2017.08.17", False),
    "ETHUSDT_BN": ("Crypto", 1.0, "2017.08.17", False),
    "BNBUSDT_BN": ("Crypto", 1.0, "2017.11.06", False),
    "LTCUSDT_BN": ("Crypto", 1.0, "2017.12.13", False),
    "NEOUSDT_BN": ("Crypto", 1.0, "2017.11.20", False),
    # thêm 2026-11-09 từ TickStory CSV (đã bỏ tick ask<bid; copper bỏ ngày 2012-03-01 sai thang giá).
    # Point value = kích thước hợp đồng sàn gốc / lot (Dương duyệt). Đuôi USX = giá bằng US cent: engine >= 2026.11.09
    # tự nhân 0.01 nên lãi khi giá đổi 1.0 (1 cent) = point value x 0.01 USD.
    "COPPERCMDUSD": ("Kim loại", 25000.0, "2012.03.02", False),   # 25.000 lb, giá USD/lb
    "COCOACMDUSD": ("Nông sản", 10.0, "2017.10.20", False),       # 10 tấn, giá USD/tấn
    "COFFEECMDUSX": ("Nông sản", 37500.0, "2017.12.05", False),   # 37.500 lb, giá cent/lb
    "COTTONCMDUSX": ("Nông sản", 50000.0, "2017.10.23", False),   # 50.000 lb, giá cent/lb
    "OJUICECMDUSX": ("Nông sản", 15000.0, "2017.10.23", False),   # 15.000 lb, giá cent/lb
    "SUGARCMDUSD": ("Nông sản", 50.0, "2017.10.03", False),       # 50 tấn, giá USD/tấn
})
_SUBGROUPS = ["FX", "Kim loại", "Năng lượng", "Nông sản", "Chỉ số", "Crypto"]   # thứ tự chuẩn của symbol trong job
SYMBOL_ORDER = list(FX25) + [s for g in _SUBGROUPS for s in SYMBOLS if SYMBOLS[s][0] == g and s not in FX25]
# Nhóm hiển thị trên web (2026-11-11, Dương): gộp kim loại, năng lượng, nông sản thành Commodity
GROUP_OF = {"Kim loại": "Commodity", "Năng lượng": "Commodity", "Nông sản": "Commodity"}
GROUPS = ["FX", "Commodity", "Chỉ số", "Crypto"]


def symbols_info():
    """Cho giao diện: danh sách symbol theo thứ tự hiển thị."""
    return [{"symbol": s, "group": GROUP_OF.get(SYMBOLS[s][0], SYMBOLS[s][0]), "point_value": SYMBOLS[s][1], "start": SYMBOLS[s][2], "swap": SYMBOLS[s][3]}
            for s in SYMBOL_ORDER]
JOB_TYPES = ["optimize_is", "smoke"]          # trader tạo trực tiếp qua form
OS1_TYPE = "os1"                               # chỉ tạo qua trang "Alpha pass IS" (1 biến thể đã pass IS)
DISPATCH_TYPES = JOB_TYPES + [OS1_TYPE]        # các loại job desk nhận

DEFAULT_LIMITS = {
    "max_variants": 2000,        # số biến thể tối đa của một job optimize
    "max_variants_smoke": 20,
    "is_end": "2024.01.01",      # trader loại 1 (không có quyền OS1): job không được vượt mốc này
    "os1_end": "2026.01.01",     # trader loại 2 (có quyền OS1) và job OS1: không vượt mốc này. OS2 không bao giờ chạy qua web
    "min_from": "2003.01.01",
    "smoke_max_days": 92,        # hàng smoke chạy song song với optimize nên phải ngắn
    "smoke_max_symbols": 3,
}

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,80}$")
DATE_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
LEV_RE = re.compile(r"^1:\d{1,4}$")
DEFAULT_LEVERAGE = "1:1000"


def full_alpha_name(name, suffix):
    """Tên trader gõ (bất kỳ) -> tên alpha cuối cùng = <tên>_<hậu tố trader>. Platform tự thêm hậu tố.
    Bỏ '.rs', khoảng trắng hai đầu, đổi ký tự không hợp lệ thành '_'; đã có đúng hậu tố thì không thêm lần nữa.
    Trả về (tên_cuối, lỗi). suffix dạng '_duong_tien'."""
    if not isinstance(name, str):
        return None, "thiếu tên alpha"
    base = re.sub(r"\.rs$", "", name.strip(), flags=re.I)
    base = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_")
    if base.endswith(suffix):
        base = base[: -len(suffix)].rstrip("_")
    if not base or base == suffix[1:]:
        return None, "thiếu tên alpha"
    full = base + suffix
    if not NAME_RE.match(full):
        return None, f"tên alpha quá dài: tối đa {80 - len(suffix)} ký tự (chưa kể hậu tố {suffix} platform tự thêm)"
    return full, None
MAX_LEVERAGE = 1000
MARGIN_CALL_LEVEL = 100   # %: mở lệnh mới bị từ chối nếu free margin < 0 hoặc margin level < mức này
STOPOUT_LEVEL = 50        # %: margin level dưới mức này thì đóng lệnh lỗ lớn nhất trước, rồi dừng backtest


def _date(s):
    if not isinstance(s, str) or not DATE_RE.match(s):
        raise ValueError(f"ngày không hợp lệ: {s!r} (dạng YYYY.MM.DD)")
    return datetime.strptime(s, "%Y.%m.%d")


def fnum(x):
    """Số -> chuỗi gọn, ổn định cho file ini."""
    x = float(x)
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return format(x, ".10g")


def grid_count(start, step, stop):
    if step == 0:
        return 1
    return int(math.floor((stop - start) / step + 1e-9)) + 1


def _num(v, name, lo=None, hi=None):
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError(f"{name}: phải là số")
    try:
        x = float(v)
    except ValueError:
        raise ValueError(f"{name}: không phải số: {v!r}")
    if not math.isfinite(x):
        raise ValueError(f"{name}: không hữu hạn")
    if lo is not None and x < lo:
        raise ValueError(f"{name}: phải >= {lo}")
    if hi is not None and x > hi:
        raise ValueError(f"{name}: phải <= {hi}")
    return x


def validate_job(cfg, catalog, limits=None, os1_access=False):
    """Trả về (cfg_sạch, lỗi). catalog: {tên_tham_số: [start, step, stop]} của alpha được chọn (từ data-server).
    os1_access: trader loại 2 được chạy đến os1_end; loại 1 chỉ đến is_end."""
    lim = dict(DEFAULT_LIMITS, **(limits or {}))
    if os1_access:
        lim["is_end"] = lim["os1_end"]
    errs = []
    if not isinstance(cfg, dict):
        return None, ["cấu hình phải là object"]
    out = {}

    def need(fn, key, *a, **kw):
        try:
            out[key] = fn(cfg.get(key), key, *a, **kw)
        except ValueError as e:
            errs.append(str(e))

    typ = cfg.get("type")
    if typ not in JOB_TYPES:
        errs.append(f"type phải thuộc {JOB_TYPES}")
    out["type"] = typ
    alpha = cfg.get("alpha")
    if not isinstance(alpha, str) or not NAME_RE.match(alpha):
        errs.append("alpha: tên không hợp lệ")
    out["alpha"] = alpha

    mode = cfg.get("symbols_mode")
    if mode not in ("portfolio", "independent"):
        errs.append("symbols_mode phải là portfolio hoặc independent")
    out["symbols_mode"] = mode
    syms = cfg.get("symbols")
    if not isinstance(syms, list) or not syms or len(syms) > len(SYMBOLS) or len(set(syms)) != len(syms) \
            or any(s not in SYMBOLS for s in syms):
        errs.append("symbols: danh sách không rỗng, không trùng, thuộc danh sách symbol cho phép")
    out["symbols"] = [s for s in SYMBOL_ORDER if isinstance(syms, list) and s in syms]  # thứ tự chuẩn
    if typ == "smoke" and isinstance(syms, list) and len(syms) > lim["smoke_max_symbols"]:
        errs.append(f"smoke: tối đa {lim['smoke_max_symbols']} symbol")

    if cfg.get("period") not in PERIODS:
        errs.append(f"period phải thuộc {PERIODS}")
    out["period"] = cfg.get("period")

    try:
        fd, td = _date(cfg.get("fromdate")), _date(cfg.get("todate"))
        out["fromdate"], out["todate"] = cfg["fromdate"], cfg["todate"]
        if fd < _date(lim["min_from"]):
            errs.append(f"fromdate phải >= {lim['min_from']}")
        if td > _date(lim["is_end"]):
            errs.append(f"todate phải <= {lim['is_end']}" + (" (không được chạm OS2)" if os1_access
                        else " (tài khoản của bạn chỉ chạy IS; OS1 chạy qua trang Alpha pass IS)"))
        if fd >= td:
            errs.append("fromdate phải trước todate")
        late = [s for s in out["symbols"] if _date(SYMBOLS[s][2]) >= td]
        if late:
            errs.append("không có dữ liệu trước todate cho: " + ", ".join(f"{s} (từ {SYMBOLS[s][2]})" for s in late))
        if typ == "smoke" and (td - fd).days > lim["smoke_max_days"]:
            errs.append(f"smoke: khoảng thời gian tối đa {lim['smoke_max_days']} ngày")
    except ValueError as e:
        errs.append(str(e))

    need(_num, "deposit", lo=100, hi=1e12)
    need(_num, "lot", lo=0.001, hi=1000)
    need(_num, "point_value_per_lot", lo=1, hi=1e7)
    need(_num, "commission_per_trade", lo=0, hi=1000)
    try:
        mt = _num(cfg.get("max_ticks", 0), "max_ticks", lo=0, hi=2e9)
        out["max_ticks"] = int(mt)
    except ValueError as e:
        errs.append(str(e))
    if typ == "optimize_is" and out.get("max_ticks"):
        errs.append("optimize_is: max_ticks phải = 0")
    if typ == "smoke" and not out.get("max_ticks"):
        out["max_ticks"] = 0
    # quy tắc tài khoản (2026-11-07): leverage tối đa 1:1000 (mặc định), margin call 100%, stop-out 50%.
    # Trader chỉ được chọn leverage THẤP hơn (chặt hơn); alpha tự khai báo cũng chỉ được chặt hơn.
    lev = cfg.get("leverage", DEFAULT_LEVERAGE)
    if not isinstance(lev, str) or not LEV_RE.match(lev) or not (1 <= int(lev.split(":")[1]) <= MAX_LEVERAGE):
        errs.append(f"leverage dạng 1:N với N từ 1 đến {MAX_LEVERAGE}")
    out["leverage"] = lev
    if cfg.get("swap_mode") not in ("zero", "synthetic"):
        errs.append("swap_mode phải là zero hoặc synthetic")
    elif cfg.get("swap_mode") == "synthetic":
        noswap = [s for s in out["symbols"] if not SYMBOLS[s][3]]
        if noswap:
            errs.append("swap synthetic chỉ có cho FX và XAUUSD; bỏ chọn hoặc dùng swap zero cho: " + ", ".join(noswap))
    out["swap_mode"] = cfg.get("swap_mode")

    # tham số: chỉ tên có trong catalog; thiếu thì dùng mặc định (đơn trị)
    params = {}
    given = cfg.get("params") if isinstance(cfg.get("params"), dict) else {}
    for k in given:
        if k not in catalog:
            errs.append(f"tham số không có trong alpha: {k}")
    total = 1
    for name, default in (catalog or {}).items():
        v = given.get(name, default)
        try:
            if not isinstance(v, (list, tuple)) or len(v) != 3:
                raise ValueError(f"{name}: cần [start, step, stop]")
            a, s, b = (_num(v[0], f"{name}.start"), _num(v[1], f"{name}.step", lo=0), _num(v[2], f"{name}.stop"))
            if s > 0 and b < a:
                raise ValueError(f"{name}: stop phải >= start")
            if s == 0:
                b = a
            params[name] = [a, s, b]
            total *= grid_count(a, s, b)
        except ValueError as e:
            errs.append(str(e))
    out["params"] = params
    cap = lim["max_variants_smoke"] if typ == "smoke" else lim["max_variants"]
    if total > cap:
        errs.append(f"số biến thể {total} vượt giới hạn {cap}")
    out["variants"] = total
    return (None, errs) if errs else (out, [])


def build_ini(cfg, alpha_file, data_folder, swap_folder):
    """Dựng input-optimize.ini từ cấu hình đã validate. Tất cả giá trị nhạy cảm (đường dẫn, todate) do desk ép."""
    if cfg["symbols_mode"] == "portfolio":
        symbols = "portfolio:" + ",".join(cfg["symbols"])
    else:
        symbols = ",".join(cfg["symbols"])
    alpha_name = alpha_file[:-3] if alpha_file.endswith(".rs") else alpha_file
    lines = [
        "[Tester]",
        f"expert = {alpha_file}",
        f"symbols = {symbols}",
        f"period = {cfg['period']}",
        f"deposit = {fnum(cfg['deposit'])}",
        "account_currency = USD",
        f"leverage = {cfg['leverage']}",
        f"margin_call_level = {MARGIN_CALL_LEVEL}",
        f"stopout_level = {STOPOUT_LEVEL}",
        "alpha_account_rules = stricter",
        f"fromdate = {cfg['fromdate']}",
        f"todate = {cfg['todate']}",
        f"data_folder = {data_folder}",
        f"swap_mode = {cfg['swap_mode']}",
        f"swap_folder = {swap_folder}",
        f"lot = {fnum(cfg['lot'])}",
        f"point_value_per_lot = {fnum(cfg['point_value_per_lot'])}",
        *([f"point_value_map = " + ",".join(f"{s}:{fnum(SYMBOLS[s][1])}" for s in cfg["symbols"] if SYMBOLS[s][0] != "FX")]
          if any(SYMBOLS[s][0] != "FX" for s in cfg["symbols"]) else []),
        f"commission_per_trade = {fnum(cfg['commission_per_trade'])}",
        f"max_ticks = {int(cfg.get('max_ticks', 0))}",
        f"alpha_name = {alpha_name}",
        "",
        "[TesterInputs]",
    ]
    for name, (a, s, b) in cfg["params"].items():
        lines.append(f"{name} = {fnum(a)},{fnum(s)},{fnum(b)}")
    return "\n".join(lines) + "\n"


def parse_tester_inputs(ini_text):
    """Đọc [TesterInputs] của file init-input -> {tên: [start, step, stop]}."""
    out, on = {}, False
    for line in ini_text.splitlines():
        t = line.strip()
        if t.startswith("["):
            on = t.lower() == "[testerinputs]"
        elif on and "=" in t:
            k, v = t.split("=", 1)
            parts = [p.strip() for p in v.split(",")]
            if len(parts) == 3 and NAME_RE.match(k.strip()):
                try:
                    out[k.strip()] = [float(p) for p in parts]
                except ValueError:
                    pass
    return out


# ---------- OS1: cùng công thức với rq_eval.py (tools dùng chung của trader) ----------
OS1_IS_END, OS1_OS_END = "20240101", "20260101"
OS_RATIO = 0.7
CRYPTO_SYMBOLS = {"BTCUSD", "ETHUSD", "BTCUSDT_BN", "ETHUSDT_BN"}
ANN_FX, ANN_CRYPTO = 260, 365


def eq_sharpe(eq, ann=ANN_FX):
    r = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]
    if len(r) < 2:
        return 0.0
    m = sum(r) / len(r)
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / len(r))
    return m / sd * math.sqrt(ann) if sd > 0 else 0.0


def os1_eval(daily, crypto=False, is_end=OS1_IS_END, os_end=OS1_OS_END):
    """daily: [(yyyymmdd, balance, equity, drawdown)] của một run liền IS -> OS1.
    Tiêu chí OS1 (Dương duyệt): có đỉnh balance mới, DD OS1 không vượt DD lớn nhất của IS, Sharpe OS1 > 0.7 x Sharpe IS."""
    ann = ANN_CRYPTO if crypto else ANN_FX
    isr = [x for x in daily if x[0] < is_end]
    osr = [x for x in daily if is_end <= x[0] < os_end]
    if not isr or not osr:
        return {"os_ok": False, "os_note": "không có dữ liệu IS hoặc OS1"}
    sh = eq_sharpe([x[2] for x in isr], ann)
    os_sh = eq_sharpe([isr[-1][2]] + [x[2] for x in osr], ann)
    new_high = max(x[1] for x in osr) > max(x[1] for x in isr)
    no_dd = max(x[3] for x in osr) <= max(x[3] for x in isr)
    enough = len(osr) > 100
    res = {"os_ok": enough, "is_sharpe": round(sh, 3), "os_sharpe": round(os_sh, 3), "os_new_high": new_high,
           "os_no_new_dd": no_dd, "os_profit": round(osr[-1][1] - isr[-1][1], 2), "os_days": len(osr),
           "os_max_dd": round(max(x[3] for x in osr), 2), "is_max_dd": round(max(x[3] for x in isr), 2),
           "os_pass": bool(enough and new_high and no_dd and os_sh > OS_RATIO * sh)}
    if not enough:
        res["os_note"] = "OS1 quá ngắn (cần > 100 ngày có dữ liệu)"
    return res
