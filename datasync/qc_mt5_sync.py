"""Đồng bộ tick cho các symbol tùy chỉnh *_Tickstory trong MT5 (Dương duyệt 2026-11-22) qua service QTC_TickSync.

Đã kiểm (22/11): tick trong MT5 = cùng quy ước với data-tickstory (giờ UTC, giá, thời gian làm tròn giây; 67.337/67.337 tick
EURUSD khớp). Volume: tick có volume -> flags 30 (BID|ASK|LAST|VOLUME); volume = 0 -> flags 14 và MT5 giữ volume tick trước.
Mỗi symbol nối tiếp từ TICK CUỐI CỦA CHÍNH NÓ trong MT5 (MT5 và .bin không dừng cùng chỗ), nguồn Dukascopy (dùng lại bộ nhớ đệm
của qc_data_update). Giờ nối: tick MT5 trong giờ đó phải trùng phần đầu dữ liệu thô; ghi bằng CustomTicksReplace từ đầu giờ nối
(phần đầu ghi lại y hệt), chia khối <= 1 triệu tick, cắt ở ranh giới msc. Hoàn tác: --undo SYMBOL (xóa tick sau tick cuối cũ).

  python qc_mt5_sync.py --info                 # tick cuối của mọi symbol
  python qc_mt5_sync.py --sync [SYM ...]       # đồng bộ (mặc định mọi symbol *_Tickstory có tick)
  python qc_mt5_sync.py --undo EURUSD_Tickstory
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import qc_data_update as U  # noqa: E402

Q = Path(os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\Common\Files\QuanTechCap\mt5sync"))
JOURNAL = U.WORK / "mt5_journal.jsonl"
REC44 = np.dtype([("msc", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"), ("vol", "<u8"), ("flags", "<u4")])
CHUNK = 1_000_000
# tên trong MT5 (bỏ _Tickstory) -> symbol Dukascopy; FX/kim loại/crypto cùng tên
MAP = {"US500": "USA500IDXUSD", "US30": "USA30IDXUSD", "USTECH": "USATECHIDXUSD", "AUS200": "AUSIDXAUD", "DE40": "DEUIDXEUR",
       "ESP35": "ESPIDXEUR", "EUSTX50": "EUSIDXEUR", "FRA40": "FRAIDXEUR", "JPN225": "JPNIDXJPY", "SWI20": "CHEIDXCHF",
       "UK100": "GBRIDXGBP", "HKG": "HKGIDXHKD", "Brent_USD": "BRENTCMDUSD", "WTI_USD": "LIGHTCMDUSD"}


def log(*a):
    U.log("[mt5]", *a)


def service_alive(max_age=60):
    p = Q / "service_status.txt"
    return p.exists() and time.time() - p.stat().st_mtime < max_age


_seq = [int(time.time() * 1000)]


def request(lines, timeout=600, ext=None):
    """Gửi yêu cầu cho service, chờ kết quả. Trả dict key=value (+ 'csv' nếu có)."""
    for d in ("req", "in", "out"):
        (Q / d).mkdir(parents=True, exist_ok=True)
    _seq[0] += 1
    rid = str(_seq[0])
    tmp = Q / "req" / f"{rid}_{lines['cmd']}.tmp"
    tmp.write_text("".join(f"{k}={v}\n" for k, v in lines.items()), encoding="ascii")
    os.replace(tmp, Q / "req" / f"{rid}_{lines['cmd']}.txt")
    out = Q / "out" / f"{rid}_{lines['cmd']}.txt"
    t0 = time.time()
    while not out.exists():
        if time.time() - t0 > timeout:
            raise TimeoutError(f"service không trả lời yêu cầu {rid} ({lines['cmd']}) sau {timeout}s")
        time.sleep(1)
    time.sleep(0.2)
    res = dict(x.split("=", 1) for x in out.read_text(encoding="ascii", errors="replace").splitlines() if "=" in x)
    out.unlink()
    csv = Q / "out" / f"{rid}_{lines['cmd']}.csv"
    if csv.exists():
        res["csv"] = [x.split(",") for x in csv.read_text(encoding="ascii").splitlines() if x]
        csv.unlink()
    return res


def parse_info():
    """{tên MT5: dict(last_msc, bid, ask, vol, digits) hoặc None nếu không có tick}"""
    for d in ("req", "in", "out"):
        (Q / d).mkdir(parents=True, exist_ok=True)
    _seq[0] += 1
    rid = f"{_seq[0]}_info"
    (Q / "req" / f"{rid}.tmp").write_text("cmd=info\n", encoding="ascii")
    os.replace(Q / "req" / f"{rid}.tmp", Q / "req" / f"{rid}.txt")
    out = Q / "out" / f"{rid}.txt"
    t0 = time.time()
    while not out.exists():
        if time.time() - t0 > 300:
            raise TimeoutError("service không trả lời info")
        time.sleep(1)
    time.sleep(0.2)
    res = {}
    for line in out.read_text(encoding="ascii", errors="replace").splitlines():
        if not line.startswith("sym="):
            continue
        p = line[4:].split(",")
        if p[2] == "NO_TICKS":
            res[p[0]] = None
        else:
            res[p[0]] = {"digits": int(p[1]), "msc": int(p[2]), "bid": float(p[3]), "ask": float(p[4]), "vol": int(p[6])}
    out.unlink()
    return res


def ms_of(rec):
    """msc UTC của các bản ghi TICKBIN1 (đổi theo từng ngày duy nhất cho nhanh)."""
    if not rec.size:
        return np.zeros(0, np.int64)
    days, inv = np.unique(rec["date"], return_inverse=True)
    base = np.array([int(datetime(int(d) // 10000, int(d) // 100 % 100, int(d) % 100, tzinfo=timezone.utc).timestamp()) * 1000
                     for d in days], np.int64)
    return base[inv] + rec["tod"].astype(np.int64)


def to_mt5(rec, prev_vol):
    out = np.empty(rec.size, dtype=REC44)
    if not rec.size:
        return out
    out["msc"] = ms_of(rec)
    out["bid"], out["ask"], out["last"] = rec["bid"], rec["ask"], rec["last"]
    v = rec["vol"].astype(np.uint64)
    has = v > 0
    idx = np.where(has, np.arange(v.size), -1)
    idx = np.maximum.accumulate(idx)
    out["vol"] = np.where(idx >= 0, v[np.maximum(idx, 0)], np.uint64(prev_vol))
    out["flags"] = np.where(has, 30, 14).astype(np.uint32)
    return out


def journal(e):
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def sync_symbol(name, st, end_hour, workers=12):
    base = name[: -len("_Tickstory")]
    duka = MAP.get(base, base)
    last_msc = st["msc"]
    h0 = datetime.fromtimestamp(last_msc // 1000, timezone.utc).replace(minute=0, second=0)
    h0_ms = int(h0.timestamp() * 1000)
    ex = request({"cmd": "export", "symbol": name, "from_msc": h0_ms, "to_msc": h0_ms + 3_599_999})
    if ex.get("ok") != "1":
        raise RuntimeError(f"export lỗi: {ex}")
    mrows = ex.get("csv", [])
    k = len(mrows)
    raw0 = U.decode_bi5(U.duka_hour(duka, h0))
    if raw0.size == 0 or k == 0:
        raise RuntimeError(f"giờ nối {h0:%Y-%m-%d %H}h thiếu dữ liệu (MT5 {k}, thô {raw0.size})")
    div = 10.0 ** round(np.log10(raw0["bid"][0] / float(mrows[0][1])))
    m_msc = np.array([int(r[0]) for r in mrows], np.int64)
    m_bid = np.array([float(r[1]) for r in mrows])
    m_ask = np.array([float(r[2]) for r in mrows])
    cand = []
    conv = U.duka_to_recs(raw0, h0, div)
    cand.append(conv)
    cand.append(U.clean(conv)[0])
    g0 = None
    for c in cand:
        if c.size >= k:
            cm = to_mt5(c[:k], 0)
            if (np.array_equal(cm["msc"], m_msc) and np.allclose(cm["bid"], m_bid, rtol=1e-12, atol=0)
                    and np.allclose(cm["ask"], m_ask, rtol=1e-12, atol=0)):
                g0 = c
                break
    if g0 is None:
        raise RuntimeError(f"giờ nối {h0:%Y-%m-%d %H}h: tick MT5 ({k}) KHÔNG khớp dữ liệu Dukascopy {duka}; không ghi")
    # phần đầu giờ: dùng volume MT5 đang có; phần sau: theo quy tắc mang volume
    prev_vol = int(mrows[-1][4])
    head = np.empty(k, dtype=REC44)
    head["msc"], head["bid"], head["ask"] = m_msc, m_bid, m_ask
    head["last"] = [float(r[3]) for r in mrows]
    head["vol"] = [int(r[4]) for r in mrows]
    head["flags"] = [int(r[5]) for r in mrows]
    rest = [U.clean(g0[k:])[0] if g0 is cand[0] else g0[k:]]
    stop = "tới mốc cuối"
    hours = []
    h = h0 + timedelta(hours=1)
    while h < end_hour:
        hours.append(h)
        h += timedelta(hours=1)

    def safe(x):
        try:
            return x, U.duka_hour(duka, x)
        except Exception as e:  # noqa: BLE001
            return x, e
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(workers) as pool:
        for hh, b in pool.map(safe, hours):
            if b is None or isinstance(b, Exception):
                stop = f"dừng ở {hh:%Y-%m-%d %H}h ({'chưa có' if b is None else b})"
                break
            rest.append(U.clean(U.duka_to_recs(U.decode_bi5(b), hh, div))[0])
    new = np.concatenate(rest) if rest else np.zeros(0, U.DT)
    if new.size == 0:
        return {"sym": name, "added": 0, "stop": stop}
    newm = to_mt5(new, prev_vol)
    if newm["msc"][0] < last_msc:
        raise RuntimeError("tick mới lùi thời gian so với tick cuối MT5")
    allm = np.concatenate([head, newm])
    journal({"sym": name, "t": time.strftime("%Y-%m-%d %H:%M:%S"), "state": "start", "old_last_msc": last_msc,
             "from_msc": h0_ms, "new_last_msc": int(allm["msc"][-1]), "add": int(newm.size)})
    # chia khối, cắt ở ranh giới msc
    i, part = 0, 0
    while i < allm.size:
        j = min(allm.size, i + CHUNK)
        while j < allm.size and allm["msc"][j] == allm["msc"][j - 1]:
            j += 1
        seg = allm[i:j]
        fname = f"{name}_{int(time.time())}_{part}.bin"
        (Q / "in" / (fname + ".tmp")).write_bytes(seg.tobytes())
        os.replace(Q / "in" / (fname + ".tmp"), Q / "in" / fname)
        frm = h0_ms if i == 0 else int(seg["msc"][0])
        r = request({"cmd": "replace", "symbol": name, "file": fname, "from_msc": frm, "to_msc": int(seg["msc"][-1]),
                     "count": seg.size}, timeout=1800)
        if r.get("ok") != "1":
            raise RuntimeError(f"replace khối {part} lỗi: {r}")
        i, part = j, part + 1
    after = parse_info().get(name)
    ok = after is not None and after["msc"] == int(allm["msc"][-1])
    journal({"sym": name, "t": time.strftime("%Y-%m-%d %H:%M:%S"), "state": "done" if ok else "check_failed",
             "old_last_msc": last_msc, "new_last_msc": int(allm["msc"][-1]), "mt5_last_after": after and after["msc"]})
    if not ok:
        raise RuntimeError(f"sau khi ghi, tick cuối MT5 {after} khác mong đợi {int(allm['msc'][-1])}")
    return {"sym": name, "added": int(newm.size), "first_new": int(newm["msc"][0]), "last_new": int(allm["msc"][-1]),
            "stop": stop, "chunks": part}


def undo(name):
    es = [json.loads(x) for x in JOURNAL.read_text(encoding="utf-8").splitlines() if x.strip()]
    es = [e for e in es if e["sym"] == name and e["state"] in ("start", "done", "check_failed")]
    if not es:
        raise SystemExit("không có lần ghi nào")
    old = es[-1]["old_last_msc"]          # hoàn tác lần ghi gần nhất: xóa mọi tick sau tick cuối trước lần đó
    r = request({"cmd": "delete", "symbol": name, "from_msc": old + 1, "to_msc": 4_102_444_800_000})
    print(name, r)
    journal({"sym": name, "t": time.strftime("%Y-%m-%d %H:%M:%S"), "state": "undone", "old_last_msc": old, "result": r})


def sync_all(end_hour, only=None):
    if not service_alive():
        return {"error": "service QTC_TickSync không chạy (MT5 tắt hoặc service dừng)"}
    st = parse_info()
    out = {"done": [], "skipped": [], "errors": []}
    for name, s in sorted(st.items()):
        if only and name not in only:
            continue
        if s is None:
            out["skipped"].append({"sym": name, "reason": "MT5 không có tick"})
            continue
        try:
            r = sync_symbol(name, s, end_hour)
            out["done"].append(r)
            log(f"{name}: +{r['added']} tick, {r['stop']}")
        except Exception as e:  # noqa: BLE001
            out["errors"].append({"sym": name, "error": str(e)})
            log(f"{name}: LỖI {e}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--undo")
    ap.add_argument("--until", help="giờ UTC cuối (YYYY-mm-ddTHH), mặc định giờ hiện tại - 3h")
    ap.add_argument("syms", nargs="*")
    a = ap.parse_args()
    U.LOGS.mkdir(parents=True, exist_ok=True)
    U.LOG_FILE = U.LOGS / time.strftime("mt5_%Y%m%d_%H%M%S.log")
    if a.info:
        for k, v in sorted(parse_info().items()):
            print(k, v and datetime.fromtimestamp(v["msc"] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        return 0
    if a.undo:
        return undo(a.undo)
    if a.sync:
        end = (datetime.strptime(a.until, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if a.until else
               (datetime.now(timezone.utc) - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0))
        r = sync_all(end, set(a.syms) or None)
        (U.WORK / "last_mt5_report.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in r.items()}, ensure_ascii=False))
        return 1 if r.get("errors") or r.get("error") else 0
    ap.error("cần --info, --sync hoặc --undo")


if __name__ == "__main__":
    sys.exit(main())
