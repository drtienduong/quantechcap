"""Cập nhật hằng tuần dữ liệu tick của rust-quant (data-tickstory) + swap synthetic, chỉ GHI NỐI TIẾP.

Dương duyệt 2026-11-22: tải Dukascopy (thay Tickstory), Binance cho *_BN, USDHKD sinh tiếp theo peg 7.80,
swap synthetic kéo dài bằng lãi suất của dòng cuối (xấp xỉ). Chạy Chủ nhật 04:00 (Task Scheduler QuanTechCap_data_update).

Quy ước (đã kiểm khớp 100% với file Tickstory xuất trên 9 symbol/ngày):
  Dukascopy bi5 '>IIIff' (ms trong giờ, ask, bid, vol ask, vol bid; THÁNG 0-based, giờ UTC)
  -> TICKBIN1 <IIdddQ>: date YYYYMMDD, tod = ms trong ngày làm tròn XUỐNG giây, bid = bid_int/div, ask = ask_int/div,
     last = bid, vol = floor(vol_bid + 0.5). div suy ra từ chỗ nối (10^k).
  Bỏ tick ask < bid hoặc giá <= 0 (giống bước làm sạch hàng hóa 11/2026).
  Binance: giữ đúng binance_to_tickbin.py (kline 1s, BTCUSDT_BN) và binance1m_to_tickbin.py (kline 1m, *_BN khác),
     spread giả lập 1 bps, thời điểm = close_time.

Các bước (mỗi lần chạy):
  0. recover(): sửa lần ghi dở dang trước (theo sổ append_journal.jsonl).
  1. stage: tải + chuyển đổi vào staging\\<SYM>.rec (KHÔNG đụng data-tickstory). Kiểm chỗ nối: các tick của giờ cuối
     trong .bin phải trùng chính xác đầu giờ đó của dữ liệu thô; lệch -> bỏ symbol, báo lỗi.
     Chỉ lấy các giờ liền mạch: gặp giờ Dukascopy chưa có (404/lỗi) thì dừng ở đó (không để lỗ hổng).
  2. append: chờ không có backtest (rq_run --child / engine CLI), đặt khóa data_update.lock (desk chờ khóa),
     ghi nối + fsync rồi mới sửa số dòng ở header; kiểm lại đầu/cuối.
  3. swap: nối thêm dòng cho các ngày mới (ghi file tạm rồi thay thế).

  python qc_data_update.py --run              # đầy đủ (Task Scheduler)
  python qc_data_update.py --stage-only [SYM ...]   # chỉ tải + chuẩn bị, xem báo cáo
  python qc_data_update.py --rollback SYM     # cắt file về kích thước trước lần ghi gần nhất
"""
import argparse
import hashlib
import io
import json
import lzma
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

RQ = Path(r"C:\Users\zicza\Documents\rust-quant")
BIN_DIR = RQ / "data-tickstory"
SWAP_DIR = RQ / "data-swap"
WORK = Path(r"D:\quantechcap-data-research\data-update")
RAW = WORK / "raw-dukascopy"
TS_CACHE = Path(r"D:\TickStory Data")          # bộ nhớ đệm cũ của Tickstory (chỉ đọc)
BN_RAW = Path(r"D:\binance_data\spot")
STAGE = WORK / "staging"
LOGS = WORK / "logs"
JOURNAL = WORK / "append_journal.jsonl"
LOCK = WORK / "data_update.lock"
REPORT = WORK / "last_report.json"

DT = np.dtype([("date", "<u4"), ("tod", "<u4"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"), ("vol", "<u8")])
BI5 = np.dtype([("ms", ">u4"), ("ask", ">u4"), ("bid", ">u4"), ("av", ">f4"), ("bv", ">f4")])
DUKA_URL = "https://datafeed.dukascopy.com/datafeed/{sym}/{y}/{m:02d}/{d:02d}/{h:02d}h_ticks.bi5"
BN_BASE = "https://data.binance.vision/data/spot/{kind}/klines/{sym}/{iv}/{sym}-{iv}-{tag}.zip"
BN_1S = {"BTCUSDT_BN"}                 # kline 1s; *_BN khác: kline 1m
PEG = {"USDHKD": "HKGIDXHKD"}          # sinh tiếp theo peg, kéo tới ngày cuối của symbol kia
UA = {"User-Agent": "Mozilla/5.0 (QuanTechCap data update)"}
LOG_FILE = None


def log(*a):
    s = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(s, flush=True)
    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(s + "\n")


# ---------------- TICKBIN1 ----------------
def bin_path(sym):
    return BIN_DIR / f"{sym}_mt5_ticks.bin"


def bin_info(sym):
    p = bin_path(sym)
    with open(p, "rb") as f:
        h = f.read(32)
        if h[:8] != b"TICKBIN1":
            raise RuntimeError(f"{p}: không phải TICKBIN1")
        ver, rs, n = struct.unpack("<IIQ", h[8:24])
        size = os.fstat(f.fileno()).st_size
    if ver != 1 or rs != 40:
        raise RuntimeError(f"{p}: phiên bản/record lạ")
    return n, size


def read_recs(sym, start, count):
    with open(bin_path(sym), "rb") as f:
        f.seek(32 + start * 40)
        return np.frombuffer(f.read(count * 40), dtype=DT).copy()


def last_hour_recs(sym, n):
    """Các bản ghi cuối file thuộc cùng giờ với bản ghi cuối (đọc lùi theo khối)."""
    last = read_recs(sym, n - 1, 1)[0]
    d, hr = int(last["date"]), int(last["tod"]) // 3_600_000
    chunk, got = 20000, None
    while True:
        st = max(0, n - chunk)
        r = read_recs(sym, st, n - st)
        m = (r["date"] == d) & (r["tod"] // 3_600_000 == hr)
        if st == 0 or not m[0]:
            idx = np.nonzero(m)[0]
            got = r[idx[0]:] if idx.size else r[-1:]
            return d, hr, got
        chunk *= 4


def ymd_of(dt_):
    return dt_.year * 10000 + dt_.month * 100 + dt_.day


def dt_of(ymd, tod=0):
    return datetime(ymd // 10000, ymd // 100 % 100, ymd % 100, tzinfo=timezone.utc) + timedelta(milliseconds=int(tod))


# ---------------- Dukascopy ----------------
def http_get(url, tries=20, timeout=60):
    """Trả bytes (200), None (404), hoặc ném lỗi sau nhiều lần thử.
    Dukascopy (11/2026): ~30% yêu cầu hỏng ở bước kết nối (timeout 21 s) -> thử lại ngay; 503 -> chờ tăng dần."""
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
            time.sleep(min(60, 5 * (i + 1)))       # 503/5xx: máy chủ quá tải
            continue
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(2 if i < 10 else 30)            # lỗi kết nối: thử lại nhanh
    raise RuntimeError(f"tải lỗi {url}: {err!r}")


def duka_hour(sym, h):
    """bytes bi5 của giờ h (UTC): b'' = không có tick, None = Dukascopy chưa có giờ này."""
    rel = Path(sym, str(h.year), f"{h.month - 1:02d}", f"{h.day:02d}", f"{h.hour:02d}h_ticks.bi5")
    hour_end = (h + timedelta(hours=1)).timestamp()
    p = RAW / rel                      # bản tự tải: chỉ lưu khi Dukascopy trả 200 -> luôn tin
    if p.exists():
        return p.read_bytes()
    p = TS_CACHE / rel                 # bộ nhớ đệm Tickstory: mtime không phải lúc tải (đã so 106 file: giống hệt bản tải mới)
    if p.exists():
        st = p.stat()
        weekend = sym not in ("BTCUSD", "ETHUSD") and (          # crypto Dukascopy giao dịch cả cuối tuần
            (h.weekday() == 4 and h.hour >= 22) or h.weekday() == 5 or (h.weekday() == 6 and h.hour < 21))
        if st.st_size > 0 or weekend or st.st_mtime >= hour_end + 2 * 3600:
            return p.read_bytes()      # file rỗng ngày thường (có thể là giờ chưa có lúc Tickstory tải) -> tải lại
    b = http_get(DUKA_URL.format(sym=sym, y=h.year, m=h.month - 1, d=h.day, h=h.hour))
    if b is None:
        return None
    p = RAW / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    tmp.write_bytes(b)
    os.replace(tmp, p)
    return b


def decode_bi5(b):
    if not b:
        return np.zeros(0, dtype=BI5)
    return np.frombuffer(lzma.decompress(b), dtype=BI5)


def duka_to_recs(raw, h, div):
    out = np.empty(raw.size, dtype=DT)
    out["date"] = ymd_of(h)
    out["tod"] = h.hour * 3_600_000 + (raw["ms"].astype(np.uint32) // 1000) * 1000
    out["bid"] = raw["bid"].astype(np.float64) / div
    out["ask"] = raw["ask"].astype(np.float64) / div
    out["last"] = out["bid"]
    out["vol"] = np.floor(raw["bv"].astype(np.float64) + 0.5).astype(np.uint64)
    return out


def clean(rec):
    ok = (rec["bid"] > 0) & (rec["ask"] > 0) & (rec["ask"] >= rec["bid"])
    return rec[ok], int((~ok).sum())


def same(a, b):
    if a.size != b.size:
        return False
    return bool(np.array_equal(a["date"], b["date"]) and np.array_equal(a["tod"], b["tod"])
                and np.array_equal(a["vol"], b["vol"]) and np.allclose(a["bid"], b["bid"], rtol=1e-12, atol=0)
                and np.allclose(a["ask"], b["ask"], rtol=1e-12, atol=0) and np.allclose(a["last"], b["last"], rtol=1e-12, atol=0))


def stage_duka(sym, end_hour, workers):
    n, size = bin_info(sym)
    d, hr, tail = last_hour_recs(sym, n)
    h0 = dt_of(d) + timedelta(hours=hr)
    raw0 = decode_bi5(duka_hour(sym, h0))
    if raw0.size == 0:
        raise RuntimeError(f"giờ nối {h0:%Y-%m-%d %H}h không có dữ liệu thô để đối chiếu")
    div = 10.0 ** round(np.log10(raw0["bid"][0] / tail["bid"][0])) if tail["bid"][0] > 0 else None
    conv0 = duka_to_recs(raw0, h0, div)
    # giờ nối: tail phải trùng phần đầu của giờ thô (có hoặc không bước làm sạch)
    k = tail.size
    cut = None
    if same(conv0[:k], tail):
        cut = k
    else:
        c0, _ = clean(conv0)
        if same(c0[:k], tail):     # tìm vị trí tương ứng trong dãy thô
            ok = (conv0["bid"] > 0) & (conv0["ask"] > 0) & (conv0["ask"] >= conv0["bid"])
            cut = int(np.nonzero(ok)[0][k - 1]) + 1
    if cut is None:
        raise RuntimeError(f"chỗ nối {h0:%Y-%m-%d %H}h KHÔNG khớp dữ liệu Dukascopy (bin {k} tick, thô {raw0.size}); "
                           "không ghi, cần xem tay")
    out = STAGE / f"{sym}.rec"
    tmp = out.with_suffix(".part")
    first_new = last_new = None
    total = dropped = 0
    empty_weekday = 0
    stop_reason = "tới mốc cuối"
    hours = []
    h = h0 + timedelta(hours=1)
    while h < end_hour:
        hours.append(h)
        h += timedelta(hours=1)
    last_key = (int(tail["date"][-1]), int(tail["tod"][-1]))
    with open(tmp, "wb") as fo, ThreadPoolExecutor(workers) as ex:
        first_chunk, _d = clean(conv0[cut:])
        dropped += _d
        chunks = [(h0, first_chunk)]
        def safe(x):
            try:
                return x, duka_hour(sym, x)
            except Exception as e:  # noqa: BLE001
                return x, e
        it = ex.map(safe, hours)
        reached = h0
        for hh, b in it if hours else []:
            if b is None or isinstance(b, Exception):
                stop_reason = (f"Dukascopy chưa có {hh:%Y-%m-%d %H}h" if b is None
                               else f"tải lỗi ở {hh:%Y-%m-%d %H}h ({b}); tuần sau tải tiếp")
                break
            raw = decode_bi5(b)
            rec, dr = clean(duka_to_recs(raw, hh, div))
            dropped += dr
            if raw.size == 0 and hh.weekday() < 4:
                empty_weekday += 1
            chunks.append((hh, rec))
            reached = hh
            if len(chunks) >= 200:
                for _, r in chunks:
                    if r.size:
                        fo.write(r.tobytes())
                        first_new = first_new or (int(r["date"][0]), int(r["tod"][0]))
                        last_new = (int(r["date"][-1]), int(r["tod"][-1]))
                        total += r.size
                chunks = []
        for _, r in chunks:
            if r.size:
                fo.write(r.tobytes())
                first_new = first_new or (int(r["date"][0]), int(r["tod"][0]))
                last_new = (int(r["date"][-1]), int(r["tod"][-1]))
                total += r.size
    if first_new and first_new < last_key:
        raise RuntimeError("tick mới không nối tiếp tick cũ (thời gian lùi)")
    os.replace(tmp, out)
    return {"sym": sym, "kind": "dukascopy", "div": div, "old_n": n, "old_size": size,
            "old_last": [int(tail["date"][-1]), int(tail["tod"][-1])], "join_hour": f"{h0:%Y-%m-%d %H}h",
            "new_count": total, "first_new": first_new, "last_new": last_new, "reached_hour": f"{reached:%Y-%m-%d %H}h",
            "stop": stop_reason, "dropped_bad": dropped, "empty_mon_thu_hours": empty_weekday}


# ---------------- Binance ----------------
def bn_parse(b):
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        raw = z.read(z.namelist()[0])
    lines = raw.strip().split(b"\n")
    if not lines or not lines[0]:
        return np.zeros(0, np.int64), np.zeros(0), np.zeros(0)
    if not lines[0][:1].isdigit():
        lines = lines[1:]
    ncol = lines[0].count(b",") + 1
    a = np.array(b",".join(lines).replace(b"\r", b"").split(b","), dtype=np.float64).reshape(-1, ncol)
    ct = a[:, 6].astype(np.int64)
    if ct[0] > 10**14:
        ct //= 1000
    return ct, a[:, 4], a[:, 5]


def bn_zip(pair, iv, kind, tag):
    """Zip kline (đã kiểm CHECKSUM). Lưu ở D:\\binance_data\\spot\\klines_<iv>[ _daily]\\<pair>\\. None = chưa có."""
    sub = f"klines_{iv}" + ("" if kind == "monthly" else "_daily")
    p = BN_RAW / sub / pair / f"{pair}-{iv}-{tag}.zip"
    url = BN_BASE.format(kind=kind, sym=pair, iv=iv, tag=tag)
    cs = http_get(url + ".CHECKSUM")
    if cs is None:
        return None
    want = cs.decode().split()[0]
    if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == want:
        return p.read_bytes()
    b = http_get(url, timeout=300)
    if b is None:
        return None
    if hashlib.sha256(b).hexdigest() != want:
        raise RuntimeError(f"CHECKSUM sai {url}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b)
    return b


def bn_recs(ct, close, vol, half):
    day = ct // 86_400_000
    rec = np.empty(ct.size, dtype=DT)
    rec["date"] = np.char.replace(np.datetime_as_string(day.astype("datetime64[D]")), "-", "").astype(np.uint32)
    rec["tod"] = (ct - day * 86_400_000).astype(np.uint32)
    rec["bid"], rec["ask"], rec["last"] = close * (1 - half), close * (1 + half), close
    rec["vol"] = np.round(vol * 1e8).astype(np.uint64)
    return rec


def stage_binance(sym, today):
    pair = sym[:-3]
    iv = "1s" if sym in BN_1S else "1m"
    half = 1.0 / 2e4
    n, size = bin_info(sym)
    last = read_recs(sym, n - 1, 1)[0]
    last_ms = int(dt_of(int(last["date"]), int(last["tod"])).timestamp() * 1000)
    last_day = dt_of(int(last["date"])).date()
    # kiểm quy ước: dựng lại ngày cuối trong .bin từ dữ liệu thô, phải trùng
    files = []        # (kind, tag, month_start_flag)
    dday = last_day        # tải lại cả ngày chứa tick cuối; điều kiện ct > last_ms bỏ phần đã có
    m = date(dday.year, dday.month, 1)
    while m <= today:
        nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        if nxt <= today - timedelta(days=3):          # tháng đã khép: dùng zip tháng nếu đã có
            files.append(("monthly", f"{m:%Y-%m}", m))
        else:
            d = max(m, dday)
            while d < min(nxt, today):
                files.append(("daily", f"{d:%Y-%m-%d}", d))
                d += timedelta(days=1)
        m = nxt
    out = STAGE / f"{sym}.rec"
    tmp = out.with_suffix(".part")
    total, first_new, last_new, stop = 0, None, None, "tới hôm qua"
    prev_close = float(last["last"])   # 1s: so giá với giây trước (giây bị bỏ luôn có giá = tick giữ lại gần nhất)
    with open(tmp, "wb") as fo:
        for kind, tag, start in files:
            if kind == "monthly":
                b = bn_zip(pair, iv, "monthly", tag)
                if b is None:     # tháng chưa có bản tháng -> dùng file ngày
                    files_d = []
                    d = start
                    nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
                    while d < nxt:
                        files_d.append(d)
                        d += timedelta(days=1)
                    parts = [(bn_zip(pair, iv, "daily", f"{x:%Y-%m-%d}"), x) for x in files_d]
                else:
                    parts = [(b, start)]
            else:
                parts = [(bn_zip(pair, iv, "daily", tag), start)]
            for b, st in parts:
                if b is None:
                    stop = f"Binance chưa có {pair} {st}"
                    break
                ct, close, vol = bn_parse(b)
                if ct.size == 0:
                    continue
                month_start = st.day == 1
                ok = (close > 0) & (ct > last_ms) & np.concatenate(([True], np.diff(ct) > 0))
                if iv == "1s":
                    prevc = np.concatenate(([np.nan if month_start else prev_close], close[:-1]))
                    first_keep = np.zeros(ct.size, bool)
                    first_keep[0] = month_start          # bản gốc xử lý theo tháng: dòng đầu tháng luôn giữ
                    ok &= (vol > 0) | (close != prevc) | first_keep
                    prev_close = float(close[-1])
                ct, close, vol = ct[ok], close[ok], vol[ok]
                if ct.size == 0:
                    continue
                last_ms = int(ct[-1])
                r = bn_recs(ct, close, vol, half)
                fo.write(r.tobytes())
                total += r.size
                first_new = first_new or (int(r["date"][0]), int(r["tod"][0]))
                last_new = (int(r["date"][-1]), int(r["tod"][-1]))
            else:
                continue
            break
    os.replace(tmp, out)
    return {"sym": sym, "kind": f"binance-{iv}", "old_n": n, "old_size": size,
            "old_last": [int(last["date"]), int(last["tod"])], "new_count": total,
            "first_new": first_new, "last_new": last_new, "stop": stop}


def verify_binance_join(sym):
    """Dựng lại tháng chứa tick cuối của .bin từ zip tháng (đã có sẵn) và so 2000 tick cuối."""
    pair, iv = sym[:-3], ("1s" if sym in BN_1S else "1m")
    n, _ = bin_info(sym)
    last = read_recs(sym, n - 1, 1)[0]
    d = dt_of(int(last["date"]))
    b = bn_zip(pair, iv, "monthly", f"{d:%Y-%m}")
    if b is None:
        return "không có zip tháng để đối chiếu"
    ct, close, vol = bn_parse(b)
    ok = (close > 0) & np.concatenate(([True], np.diff(ct) > 0))
    if iv == "1s":
        ok &= (vol > 0) | np.concatenate(([True], np.diff(close) != 0))
    r = bn_recs(ct[ok], close[ok], vol[ok], 1.0 / 2e4)
    tail = read_recs(sym, n - 2000, 2000)
    lk = (int(last["date"]), int(last["tod"]))
    idx = np.nonzero((r["date"] == lk[0]) & (r["tod"] == lk[1]))[0]
    if not idx.size:
        return "tick cuối của .bin không có trong zip tháng"
    seg = r[idx[0] - 1999: idx[0] + 1]
    return "khớp" if same(seg, tail) else "KHÔNG khớp"


# ---------------- USDHKD peg ----------------
def stage_peg(sym, until_ymd):
    n, size = bin_info(sym)
    last = read_recs(sym, n - 1, 1)[0]
    d = dt_of(int(last["date"])).date() + timedelta(days=1)
    end = dt_of(until_ymd).date()
    days = []
    while d <= end:
        if d.weekday() < 5:
            days.append(d.year * 10000 + d.month * 100 + d.day)
        d += timedelta(days=1)
    rec = np.zeros(len(days) * 24, dtype=DT)
    if days:
        rec["date"] = np.repeat(np.array(days, np.uint32), 24)
        rec["tod"] = np.tile(np.arange(24, dtype=np.uint32) * 3_600_000, len(days))
        rec["bid"], rec["ask"], rec["last"] = last["bid"], last["ask"], last["last"]
    (STAGE / f"{sym}.rec").write_bytes(rec.tobytes())
    return {"sym": sym, "kind": "peg", "old_n": n, "old_size": size, "old_last": [int(last["date"]), int(last["tod"])],
            "new_count": int(rec.size), "first_new": (days[0], 0) if days else None,
            "last_new": (days[-1], 23 * 3_600_000) if days else None, "stop": f"tới {until_ymd}"}


# ---------------- ghi nối an toàn ----------------
def busy_runs():
    ps = ("Get-CimInstance Win32_Process | Where-Object { ($_.CommandLine -match 'rq_run\\.py' -and $_.CommandLine -match '--child') "
          "-or $_.Name -match 'backtest-optimzie-rust-cli' } | Select-Object -ExpandProperty ProcessId")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    return [x for x in r.stdout.split() if x.strip().isdigit()]


def journal(entry):
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def journal_open():
    """Các lần ghi đã bắt đầu mà chưa ghi 'done'."""
    st = {}
    if JOURNAL.exists():
        for line in JOURNAL.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                st[(e["sym"], e["id"])] = e
    return [e for e in st.values() if e.get("state") == "start"]


def set_rows(p, n):
    with open(p, "r+b") as f:
        f.seek(16)
        f.write(struct.pack("<Q", n))
        f.flush()
        os.fsync(f.fileno())


def recover():
    for e in journal_open():
        p = bin_path(e["sym"])
        n, size = bin_info(e["sym"])
        if n == e["new_n"] and size == 32 + 40 * e["new_n"]:
            journal(dict(e, state="done", note="recover: ghi đã xong"))
            log(f"recover {e['sym']}: lần ghi trước đã xong")
        else:
            with open(p, "r+b") as f:
                f.truncate(e["old_size"])
            set_rows(p, e["old_n"])
            journal(dict(e, state="rolled_back", note="recover: cắt về trạng thái cũ"))
            log(f"recover {e['sym']}: cắt về {e['old_n']} tick")


def append_one(meta):
    sym = meta["sym"]
    p, sp = bin_path(sym), STAGE / f"{sym}.rec"
    n, size = bin_info(sym)
    if n != meta["old_n"] or size != meta["old_size"] or size != 32 + 40 * n:
        raise RuntimeError(f"{sym}: file đã đổi từ lúc chuẩn bị (n {n} vs {meta['old_n']}), bỏ qua")
    add = sp.stat().st_size // 40
    if add != meta["new_count"] or add == 0:
        return 0
    import shutil
    free = shutil.disk_usage(BIN_DIR).free
    if free < sp.stat().st_size + (10 << 30):          # giữ tối thiểu 10 GB trống trên ổ dữ liệu
        raise RuntimeError(f"{sym}: ổ dữ liệu chỉ còn {free / 2**30:.1f} GB, không ghi")
    e = {"sym": sym, "id": time.strftime("%Y%m%d%H%M%S"), "state": "start", "old_n": n, "old_size": size,
         "new_n": n + add, "first_new": meta["first_new"], "last_new": meta["last_new"]}
    journal(e)
    with open(p, "r+b") as f, open(sp, "rb") as src:
        f.seek(size)
        while True:
            b = src.read(64 << 20)
            if not b:
                break
            f.write(b)
        f.flush()
        os.fsync(f.fileno())
    set_rows(p, n + add)
    n2, size2 = bin_info(sym)
    a, b_ = read_recs(sym, n - 1, 2), read_recs(sym, n2 - 1, 1)[0]
    ok = (n2 == n + add and size2 == 32 + 40 * n2 and [int(a[0]["date"]), int(a[0]["tod"])] == list(meta["old_last"])
          and (int(a[1]["date"]), int(a[1]["tod"])) == tuple(meta["first_new"])
          and (int(b_["date"]), int(b_["tod"])) == tuple(meta["last_new"]))
    if not ok:
        with open(p, "r+b") as f:
            f.truncate(size)
        set_rows(p, n)
        journal(dict(e, state="rolled_back", note="kiểm sau khi ghi không khớp"))
        raise RuntimeError(f"{sym}: kiểm sau khi ghi không khớp -> đã cắt về như cũ")
    journal(dict(e, state="done"))
    sp.unlink()
    return add


def rollback(sym):
    es = [json.loads(x) for x in JOURNAL.read_text(encoding="utf-8").splitlines() if x.strip()]
    es = [e for e in es if e["sym"] == sym and e["state"] == "done"]
    if not es:
        raise SystemExit("không có lần ghi nào để hoàn tác")
    e = es[-1]
    n, size = bin_info(sym)
    if n != e["new_n"]:
        raise SystemExit(f"file đã đổi sau lần ghi đó (n={n}, mong {e['new_n']}); không tự cắt")
    with open(bin_path(sym), "r+b") as f:
        f.truncate(e["old_size"])
    set_rows(bin_path(sym), e["old_n"])
    journal(dict(e, state="rolled_back_manual", id=e["id"]))
    print(f"{sym}: về {e['old_n']} tick")


# ---------------- swap synthetic ----------------
def first_mids(sym, from_ymd):
    """{ngày: mid của tick đầu tiên trong ngày} cho các ngày >= from_ymd (đọc từ cuối file)."""
    n, _ = bin_info(sym)
    lo, hi = 0, n
    while lo < hi:
        m = (lo + hi) // 2
        if int(read_recs(sym, m, 1)[0]["date"]) < from_ymd:
            lo = m + 1
        else:
            hi = m
    r = read_recs(sym, lo, n - lo)
    out = {}
    if r.size:
        days, idx = np.unique(r["date"], return_index=True)
        for d_, i in zip(days, idx):
            out[int(d_)] = (float(r["bid"][i]) + float(r["ask"][i])) / 2
    return out


def extend_swap(csv_path):
    sym = csv_path.name.replace("_synthetic_swap.csv", "")
    if not bin_path(sym).exists():
        return None
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    head, rows = lines[0], [x.split(",") for x in lines[1:] if x.strip()]
    last_day = int(rows[-1][0])
    n, _ = bin_info(sym)
    tick_end = int(read_recs(sym, n - 1, 1)[0]["date"])
    if tick_end <= last_day:
        return 0
    fx = len(sym) == 6 and sym.isalpha() and sym != "XAUUSD" and sym != "XAGUSD"
    ref = next(r for r in reversed(rows) if int(r[3]) > 0)      # dòng gần nhất có rollover
    base_rate, quote_rate = float(ref[4]), float(ref[5])
    k_long = float(ref[1]) / (float(ref[6]) * int(ref[3]) * float(ref[7]))
    k_short = float(ref[2]) / (float(ref[6]) * int(ref[3]) * float(ref[7]))
    start = dt_of(last_day).date() + timedelta(days=1)
    from_ymd = start.year * 10000 + start.month * 100 + start.day
    mids = first_mids(sym, from_ymd)
    quote = sym[3:6] if fx else "USD"
    conv = None
    if quote != "USD":
        if bin_path(quote + "USD").exists():
            conv = ("direct", first_mids(quote + "USD", from_ymd))
        elif bin_path("USD" + quote).exists():
            conv = ("inverse", first_mids("USD" + quote, from_ymd))
        else:
            raise RuntimeError(f"{sym}: không có cặp quy đổi {quote}")
    add = []
    d = start
    end = dt_of(tick_end).date()
    last_mid = None
    days_with_ticks = sorted(mids)
    while d <= end:
        ymd = d.year * 10000 + d.month * 100 + d.day
        wd = d.weekday()
        if fx:
            nxt = next((x for x in days_with_ticks if x >= ymd), None)   # giống generate_synthetic_swap: tick đầu từ ngày này trở đi
            spot = mids[nxt] if nxt is not None else last_mid
            last_mid = spot
            q2u = 1.0
            if conv:
                cd = sorted(conv[1])
                cn = next((x for x in cd if x >= ymd), cd[-1] if cd else None)
                v = conv[1].get(cn) if cn is not None else None
                q2u = None if v is None else (v if conv[0] == "direct" else 1.0 / v)
            if spot is None or q2u is None:
                d += timedelta(days=1)
                continue
            mult = 0 if wd >= 5 else (3 if wd == 2 else 1)
            long_ = 100000.0 * spot * ((base_rate - quote_rate) / 100.0) / 365.0 * mult * q2u
            short_ = -long_
            add.append(f"{ymd},{long_:.8f},{short_:.8f},{mult},{base_rate:.6f},{quote_rate:.6f},{spot:.8f},{q2u:.8f}")
        else:                       # XAU / chỉ số: chỉ ngày có tick (như file gốc), hệ số theo dòng tham chiếu
            if ymd in mids:
                spot = mids[ymd]
                mult = 0 if wd >= 5 else (3 if wd == 2 else 1)
                add.append(f"{ymd},{k_long * spot * mult:.8f},{k_short * spot * mult:.8f},{mult},{ref[4]},{ref[5]},{spot:.8f},{ref[7]}")
        d += timedelta(days=1)
    if not add:
        return 0
    tmp = csv_path.with_suffix(".csv.part")
    tmp.write_text("\n".join([head] + [",".join(r) for r in rows] + add) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, csv_path)
    return len(add)


# ---------------- điều phối ----------------
def symbols():
    out = []
    for p in sorted(BIN_DIR.glob("*_mt5_ticks.bin")):
        out.append(p.name[:-14])
    return out


def stage_all(syms, workers):
    now = datetime.now(timezone.utc)
    end_hour = (now - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    today = now.date()
    STAGE.mkdir(parents=True, exist_ok=True)
    metas, errors = [], []
    for s in syms:
        if s in PEG:
            continue
        t0 = time.time()
        try:
            m = stage_binance(s, today) if s.endswith("_BN") else stage_duka(s, end_hour, workers)
            metas.append(m)
            log(f"stage {s}: +{m['new_count']} tick, {m.get('first_new')} -> {m.get('last_new')}, {m.get('stop')}, "
                f"bỏ {m.get('dropped_bad', 0)} tick lỗi, {time.time() - t0:.0f}s")
        except Exception as e:  # noqa: BLE001
            errors.append({"sym": s, "error": str(e)})
            log(f"stage {s}: LỖI {e}")
    for s, ref in PEG.items():
        if s in syms:
            refm = next((m for m in metas if m["sym"] == ref and m.get("last_new")), None)
            rn, _ = bin_info(ref)
            until = max(refm["last_new"][0] if refm else 0, int(read_recs(ref, rn - 1, 1)[0]["date"]))
            if until:
                metas.append(stage_peg(s, until))
                log(f"stage {s}: peg tới {until}")
    return metas, errors


def wait_idle(max_wait_min=360):
    t0 = time.time()
    while True:
        b = busy_runs()
        if not b:
            return True
        if (time.time() - t0) / 60 > max_wait_min:
            return False
        log(f"đang có backtest chạy (pid {','.join(b)}), chờ 10 phút")
        time.sleep(600)


def main():
    global LOG_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--stage-only", action="store_true")
    ap.add_argument("--rollback")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--no-swap", action="store_true")
    ap.add_argument("--no-mt5", action="store_true")
    ap.add_argument("syms", nargs="*")
    a = ap.parse_args()
    for d in (WORK, LOGS, STAGE):
        d.mkdir(parents=True, exist_ok=True)
    LOG_FILE = LOGS / time.strftime("update_%Y%m%d_%H%M%S.log")
    try:                     # ưu tiên thấp (BELOW_NORMAL): không tranh CPU với backtest
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except Exception:  # noqa: BLE001
        pass
    if a.rollback:
        return rollback(a.rollback.upper())
    if not (a.run or a.stage_only):
        ap.error("cần --run hoặc --stage-only")
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 12 * 3600:
        raise SystemExit("đang có một lần cập nhật khác (data_update.lock)")
    syms = [s.upper() for s in a.syms] or symbols()
    t0 = time.time()
    report = {"start": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "run" if a.run else "stage-only"}
    recover()
    for s in syms:
        if s.endswith("_BN"):
            report.setdefault("binance_join", {})[s] = verify_binance_join(s)
            log(f"đối chiếu chỗ nối Binance {s}: {report['binance_join'][s]}")
    metas, errors = stage_all(syms, a.workers)
    report.update(staged=metas, errors=errors)
    bad_join = {s for s, v in report.get("binance_join", {}).items() if v != "khớp"}
    if a.run:
        if not wait_idle():
            report["append"] = "bỏ: backtest chạy quá 6 giờ"
        else:
            LOCK.write_text(json.dumps({"pid": os.getpid(), "t": time.time()}))
            try:
                added = {}
                for m in metas:
                    if m["sym"] in bad_join:
                        errors.append({"sym": m["sym"], "error": "chỗ nối Binance không khớp, không ghi"})
                        continue
                    if busy_runs():
                        if not wait_idle():
                            break
                    try:
                        added[m["sym"]] = append_one(m)
                        if added[m["sym"]]:
                            log(f"ghi {m['sym']}: +{added[m['sym']]} tick")
                    except Exception as e:  # noqa: BLE001
                        errors.append({"sym": m["sym"], "error": str(e)})
                        log(f"ghi {m['sym']}: LỖI {e}")
                report["appended"] = added
                if not a.no_swap:
                    sw = {}
                    for c in sorted(SWAP_DIR.glob("*_synthetic_swap.csv")):
                        try:
                            k = extend_swap(c)
                            if k:
                                sw[c.name] = k
                        except Exception as e:  # noqa: BLE001
                            errors.append({"swap": c.name, "error": str(e)})
                    report["swap_rows_added"] = sw
                    log(f"swap: nối {sum(sw.values())} dòng ở {len(sw)} file")
            finally:
                LOCK.unlink(missing_ok=True)
        if not a.no_mt5:               # đồng bộ symbol *_Tickstory trong MT5 (service QTC_TickSync), không cần khóa
            try:
                import qc_mt5_sync
                end_hour = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
                r = qc_mt5_sync.sync_all(end_hour)
                report["mt5"] = r
                if r.get("error"):
                    log("MT5:", r["error"])
                else:
                    log(f"MT5: {len(r['done'])} symbol, {sum(x['added'] for x in r['done'])} tick, {len(r['errors'])} lỗi")
                    errors += [dict(x, mt5=True) for x in r["errors"]]
            except Exception as e:  # noqa: BLE001
                errors.append({"mt5": True, "error": str(e)})
    report["errors"] = errors
    report["minutes"] = round((time.time() - t0) / 60, 1)
    REPORT.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"xong sau {report['minutes']} phút, {len(errors)} lỗi")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
