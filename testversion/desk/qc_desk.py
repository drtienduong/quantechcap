"""QuanTechCap desk - tác tử trên MÁY BÀN của Dương (bản thử nghiệm).

- CHỈ gọi ra máy chủ web (long-poll). Máy chủ web không có đường vào máy này.
- Không tin máy chủ web: tự kiểm tra lại cấu hình job (qc_common.validate_job), tự dựng file ini, tự ép đường dẫn/todate.
- Chạy backtest bằng rq_run.py (công cụ chuẩn) trong thư mục của đúng trader; báo cáo nằm ở <trader>/report/.
- Đẩy lên web chỉ bản TÓM TẮT (bảng biến thể). Trades/equity chỉ gửi khi trader bấm xem (theo yêu cầu, mỗi lần 100 lệnh).

Chạy:  python qc_desk.py [đường dẫn desk_config.json]
"""
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
import qc_common as C  # noqa: E402
sys.path.insert(0, str(HERE))
import qc_alpha_check as AC  # noqa: E402

CONFIG_PATH = (Path(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].endswith(".json")
               else Path(os.environ.get("QC_DESK_CONFIG") or HERE / "desk_config.json"))
CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
WEB = CFG["web_url"].rstrip("/")
TOKEN = CFG["token"]
PY = CFG["python"]
TOOLS = Path(CFG["tools"])
CLI = Path(CFG["cli"])
RQ_ROOT = CFG["rq_root"]
DATA_FOLDER = CFG["data_folder"]
SWAP_FOLDER = CFG["swap_folder"]
LIMITS = dict(C.DEFAULT_LIMITS, **CFG.get("limits", {}))
# auto_approve: CHỈ Dương bật trong desk_config.json (data-server là nơi quyết định, không tin cờ nào từ web)
TRADERS = {slug: {"dir": Path(t["dir"]).resolve(), "name": t["name"], "auto_approve": t.get("auto_approve") is True}
           for slug, t in CFG["traders"].items()}
RUNNER_TEMPLATE = Path(CFG.get("runner_template") or Path(RQ_ROOT) / "backtest-optimzie-rust-tool" / "src" / "runner_template.rs")
SMOKE_TIMEOUT_MIN = float(CFG.get("smoke_timeout_min", 20))
STATE = Path(CFG.get("state_dir") or HERE / "state")
STATE.mkdir(exist_ok=True)
JOBS_FILE = STATE / "jobs.json"

heavy_busy = threading.Event()   # đang chạy optimize (hàng nặng)
smoke_busy = threading.Event()   # đang chạy smoke (hàng nhanh, chạy song song với hàng nặng)
sub_busy = threading.Event()     # đang kiểm tra một bản nộp alpha
_jobs_lock = threading.Lock()
_catalog = {}   # slug -> {alpha_name: params}
_cat_hash = {}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def api(method, path, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(WEB + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def clean(x):
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return None
    return x


# ---------- danh mục alpha ----------
def alpha_params(cli_alpha_path: Path):
    """Lấy tham số + mặc định từ engine (init-input) - đúng nguồn mà GUI optimize hiện dùng."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "t.ini"
        r = subprocess.run([str(CLI), "init-input", "--output", str(out), "--alpha", str(cli_alpha_path)],
                           capture_output=True, text=True, timeout=120, cwd=str(CLI.parent))
        if r.returncode != 0 or not out.exists():
            return None
        return C.parse_tester_inputs(out.read_text(encoding="utf-8", errors="replace"))


def scan_alphas(slug):
    t = TRADERS[slug]
    suffix = "_" + t["name"]
    res = {}
    for p in sorted((t["dir"] / "alpha").glob("*.rs")):
        if p.stem.endswith(suffix) and C.NAME_RE.match(p.stem):
            params = alpha_params(p)
            if params:
                res[p.stem] = params
    return res


def sync_catalog(force=False):
    for slug in TRADERS:
        try:
            cat = scan_alphas(slug)
        except Exception as e:  # noqa: BLE001
            log("scan alpha lỗi", slug, repr(e))
            continue
        _catalog[slug] = cat
        h = hashlib.sha1(json.dumps(cat, sort_keys=True).encode()).hexdigest()
        if force or _cat_hash.get(slug) != h:
            api("POST", "/api/desk/alphas", {"slug": slug, "alphas": [{"name": n, "params": p} for n, p in cat.items()]})
            _cat_hash[slug] = h
            log(f"đồng bộ {len(cat)} alpha cho {slug}")


def catalog_loop():
    n = 0
    while True:
        try:
            sync_catalog(force=(n % 10 == 0))   # định kỳ gửi lại để web luôn có bản mới
        except Exception as e:  # noqa: BLE001
            log("catalog lỗi:", repr(e))
        n += 1
        time.sleep(60)


# ---------- chỉ mục job ----------
def jobs_index():
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def jobs_save(jid, slug, run_dir):
    with _jobs_lock:
        d = jobs_index()
        d[str(jid)] = {"slug": slug, "run_dir": str(run_dir)}
        JOBS_FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")


# ---------- chạy job ----------
def pid_alive(pid):
    """Kiểm tra tiến trình còn sống (Windows). KHÔNG dùng os.kill trên Windows: nó gọi TerminateProcess."""
    if os.name != "nt":
        return True
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == 259                    # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def upd(jid, **kw):
    try:
        return api("POST", f"/api/desk/jobs/{jid}/update", kw, timeout=30)
    except Exception as e:  # noqa: BLE001
        log("update lỗi", jid, repr(e))
        return {}


def read_progress(run_id):
    pf = None
    for t in TRADERS.values():
        p = t["dir"] / "lab" / "logs" / f"{run_id}.progress"
        if p.exists():
            pf = p
    if not pf:
        return None, ""
    txt = pf.read_text(encoding="utf-8", errors="replace").strip()
    m = re.match(r"([\d.]+)%", txt)
    return (float(m.group(1)) if m else None), txt


def collect_variants(run_dir: Path):
    db = run_dir / "data" / "results.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    evalmap = {}
    ev = run_dir / "_eval.csv"
    if ev.exists():
        import csv
        with ev.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    evalmap[int(r["run_id"])] = (str(r.get("is_pass")).lower() == "true", float(r["recent_sharpe"]) if r.get("recent_sharpe") else None)
                except (ValueError, KeyError):
                    pass
    out = []
    q = ("select i.run_id,i.symbol,i.parameters_json,m.trades,m.net_profit,m.max_drawdown_pct,m.profit_factor,m.winrate,"
         "m.custom_sharpe,m.time_in_trade_percent,m.fitness,m.recovery_factor from input i join metrics m on m.run_id=i.run_id order by i.run_id")
    for r in con.execute(q):
        ip, rs = evalmap.get(r[0], (False, None))
        out.append({"run_id": r[0], "symbol": r[1], "params": json.loads(r[2] or "{}"), "trades": r[3], "net_profit": clean(r[4]),
                    "dd_pct": clean(r[5]), "pf": clean(r[6]), "winrate": clean(r[7]), "sharpe": clean(r[8]), "tit": clean(r[9]),
                    "fitness": clean(r[10]), "recovery": clean(r[11]), "is_pass": ip, "recent_sharpe": clean(rs)})
    con.close()
    return out


def run_job(job):
    jid, slug = job["id"], job["slug"]

    def fail(msg):
        log(f"job {jid} lỗi: {msg}")
        upd(jid, status="failed", message=str(msg)[:480])

    try:
        t = TRADERS.get(slug)
        if not t:
            return fail("trader không có trong cấu hình data-server")
        cfg = job["config"]
        alpha = cfg.get("alpha", "")
        cat = _catalog.get(slug, {}).get(alpha)
        if cat is None:
            sync_catalog()
            cat = _catalog.get(slug, {}).get(alpha)
        if cat is None:
            return fail("alpha không có trong thư mục alpha của trader")
        clean_cfg, errs = C.validate_job(cfg, cat, LIMITS)     # KHÔNG tin web
        if errs:
            return fail("cấu hình bị data-server từ chối: " + "; ".join(errs))
        alpha_file = alpha + ".rs"
        alpha_path = (t["dir"] / "alpha" / alpha_file).resolve()
        if t["dir"] not in alpha_path.parents or not alpha_path.is_file():
            return fail("đường dẫn alpha không hợp lệ")
        jdir = t["dir"] / "work" / "jobs" / f"job_{jid}"
        jdir.mkdir(parents=True, exist_ok=True)
        ini = jdir / "input-optimize.ini"
        ini.write_text(C.build_ini(clean_cfg, alpha_file, DATA_FOLDER, SWAP_FOLDER), encoding="utf-8")
        env = dict(os.environ, RQ_ROOT=RQ_ROOT, TRADER_DIR=str(t["dir"]), TRADER_NAME=t["name"])
        cmd = [PY, str(TOOLS / "rq_run.py"), "--alpha", str(alpha_path), "--input", str(ini),
               "--report", str(t["dir"] / "report" / alpha), "--tag", f"web{jid}"]
        smoke = clean_cfg["type"] == "smoke"
        if smoke:
            cmd += ["--smoke", "--timeout-min", str(SMOKE_TIMEOUT_MIN)]
        upd(jid, status="running", message="Bắt đầu chạy trên data-server", progress=0)

        run_id = status_path = None
        for attempt in range(60):      # rq_run chỉ cho 1 run nặng một lúc -> chờ nếu bận (tối đa ~20 phút)
            r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
            out = (r.stdout or "") + (r.stderr or "")
            m = re.search(r"STARTED run_id=(\S+) status=(.+)", out)
            if m:
                run_id, status_path = m.group(1), Path(m.group(2).strip())
                break
            if "BUSY" in out:
                resp = upd(jid, message=f"Data-server đang bận run khác, chờ… ({attempt + 1})")
                if resp.get("cancel"):
                    return upd(jid, status="cancelled", message="Đã hủy")
                time.sleep(20)
                continue
            return fail("rq_run từ chối: " + out.strip()[-400:])
        if not run_id:
            return fail("hết thời gian chờ data-server rảnh")

        t0 = time.time()
        last = 0
        for _ in range(240):           # chờ file trạng thái xuất hiện (tối đa 2 phút)
            if status_path.exists():
                break
            time.sleep(0.5)
        if not status_path.exists():
            return fail("run không khởi động được trên data-server (tiến trình chạy thoát sớm). Thử lại sau.")
        st = {}
        dead_since = None
        while True:
            time.sleep(2)
            try:
                st = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if st.get("state") in ("done", "failed"):
                break
            # tiến trình run chết mà không kịp ghi trạng thái -> báo lỗi thay vì chờ mãi
            if st.get("pid") and not pid_alive(st["pid"]):
                dead_since = dead_since or time.time()
                if time.time() - dead_since > 20:
                    return fail("tiến trình run trên data-server dừng bất thường")
            else:
                dead_since = None
            if time.time() - last >= 8:
                last = time.time()
                pct, txt = read_progress(run_id)
                resp = upd(jid, progress=pct or 0, message=(txt or "Đang chạy")[:200])
                if resp.get("cancel"):
                    pid = st.get("pid")
                    if pid:
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
                    return upd(jid, status="cancelled", message="Đã hủy theo yêu cầu")
        if st.get("state") != "done":
            return fail(f"engine báo lỗi (exit {st.get('exit_code')}); xem log: {st.get('log', '')}")
        run_dir = Path(st["run_dir"]).resolve()
        if t["dir"] not in run_dir.parents:
            return fail("thư mục kết quả nằm ngoài workspace của trader")
        variants = collect_variants(run_dir)
        jobs_save(jid, slug, run_dir)
        n_pass = sum(1 for v in variants if v["is_pass"])
        summary = {"variants": len(variants), "is_pass": n_pass, "minutes": round((time.time() - t0) / 60, 1),
                   "report": str(run_dir.relative_to(t["dir"]))}
        for i in range(0, len(variants), 400):
            last_batch = i + 400 >= len(variants)
            api("POST", f"/api/desk/jobs/{jid}/result", {"variants": variants[i:i + 400], "final": last_batch, "summary": summary}, timeout=120)
        if not variants:
            api("POST", f"/api/desk/jobs/{jid}/result", {"variants": [], "final": True, "summary": summary})
        log(f"job {jid} xong: {len(variants)} biến thể, {n_pass} pass IS")
    except Exception as e:  # noqa: BLE001
        fail(repr(e))
    finally:
        (smoke_busy if job.get("config", {}).get("type") == "smoke" else heavy_busy).clear()


# ---------- nộp alpha: cách ly -> kiểm tra -> duyệt -> cài ----------
SUB_MSG = {
    "rejected_static": "Không đạt kiểm tra an toàn ({n} lỗi). Sửa rồi nộp lại.",
    "rejected_compile": "Lỗi biên dịch ({n} lỗi). Sửa rồi nộp lại.",
    "pending_review": "Đạt kiểm tra tự động và biên dịch. Chờ duyệt.",
    "approved_auto": "Đạt kiểm tra, tự duyệt. Alpha đã có trong danh sách.",
}


def sub_update(sid, status, message, result=None):
    body = {"status": status, "message": message}
    if result is not None:
        body["result"] = result
    try:
        api("POST", f"/api/desk/submissions/{sid}/update", body, timeout=30)
    except Exception as e:  # noqa: BLE001
        log("cập nhật bản nộp lỗi", sid, repr(e))


def sub_dir(t, sid, name):
    d = (t["dir"] / "submissions" / f"{int(sid):06d}_{name}").resolve()
    if t["dir"] not in d.parents:
        raise ValueError("đường dẫn bản nộp không hợp lệ")
    return d


def install_alpha(t, name, src_text):
    """Chép mã đã duyệt vào thư mục alpha của trader (ghi đè bản cũ cùng tên = phiên bản mới)."""
    dest = (t["dir"] / "alpha" / f"{name}.rs").resolve()
    if t["dir"] not in dest.parents:
        raise ValueError("đường dẫn alpha không hợp lệ")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".rs.tmp")
    tmp.write_text(src_text, encoding="utf-8", newline="\n")
    os.replace(tmp, dest)
    return dest


def handle_submission(sb):
    sid = sb.get("id")
    try:
        slug, name, src = sb.get("slug"), sb.get("name"), sb.get("source")
        t = TRADERS.get(slug)
        if not t:
            return sub_update(sid, "failed", "trader không có trên data-server")
        if not isinstance(name, str) or not C.NAME_RE.match(name) or not name.endswith("_" + t["name"]):
            return sub_update(sid, "rejected", f"tên alpha phải kết thúc bằng _{t['name']}")
        if not isinstance(src, str) or not src.strip():
            return sub_update(sid, "failed", "không nhận được mã nguồn")
        raw = src.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        if sb.get("sha256") and sb["sha256"] != sha:
            return sub_update(sid, "failed", "mã nhận được không khớp mã đã nộp (sha256)")
        qd = sub_dir(t, sid, name)
        qd.mkdir(parents=True, exist_ok=True)
        (qd / "source.rs").write_bytes(raw)          # khu cách ly: chưa vào thư mục alpha
        res = AC.check(src, RUNNER_TEMPLATE)
        meta = {"id": sid, "slug": slug, "name": name, "sha256": sha, "size": len(raw),
                "received": time.strftime("%Y-%m-%d %H:%M:%S"), "check": res}
        if not res["static_ok"]:
            meta["status"], msg = "rejected", SUB_MSG["rejected_static"].format(n=len(res["static_errors"]))
        elif res["compile_ok"] is False:
            meta["status"], msg = "rejected", SUB_MSG["rejected_compile"].format(n=len(res["compile_errors"]))
        elif t["auto_approve"]:
            install_alpha(t, name, src)
            meta["status"], meta["approved_by"], msg = "approved", "auto", SUB_MSG["approved_auto"]
        else:
            meta["status"], msg = "pending_review", SUB_MSG["pending_review"]
        (qd / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"bản nộp {sid} {name}: {meta['status']}")
        sub_update(sid, meta["status"], msg, {k: res[k] for k in ("static_errors", "compile_errors", "params", "fn_name")})
        if meta["status"] == "approved":
            try:
                sync_catalog(force=True)
            except Exception as e:  # noqa: BLE001
                log("đồng bộ alpha sau khi cài lỗi", repr(e))
    except Exception as e:  # noqa: BLE001
        log("kiểm tra bản nộp lỗi", sid, repr(e))
        sub_update(sid, "failed", "lỗi phía data-server khi kiểm tra: " + repr(e)[:200])
    finally:
        sub_busy.clear()


# ---------- yêu cầu dữ liệu theo trang ----------
def handle_request(req):
    rid, slug, kind, jid, pl = req["id"], req["slug"], req["kind"], req["job_id"], req["payload"]
    try:
        entry = jobs_index().get(str(jid))
        t = TRADERS.get(slug)
        if not entry or not t or entry["slug"] != slug:
            raise ValueError("không có kết quả của job này trên data-server")
        run_dir = Path(entry["run_dir"]).resolve()
        if t["dir"] not in run_dir.parents:
            raise ValueError("đường dẫn không hợp lệ")
        db = run_dir / "data" / "results.sqlite"
        run_id, offset = int(pl["run_id"]), max(0, int(pl.get("offset", 0)))
        limit = min(100, int(pl.get("limit", 100)))
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        if kind == "trades":
            total = con.execute("select count(*) from trade_info where run_id=?", (run_id,)).fetchone()[0]
            rows = con.execute("select trade_id,symbol,side,open_time_ms,open_price,close_time_ms,close_price,lots,profit,commission,"
                               "reason_open,reason_close from trade_info where run_id=? order by trade_id limit ? offset ?",
                               (run_id, limit, offset)).fetchall()
            keys = ["trade_id", "symbol", "side", "open_time_ms", "open_price", "close_time_ms", "close_price", "lots", "profit",
                    "commission", "reason_open", "reason_close"]
            res = {"ok": True, "total": total, "offset": offset, "rows": [{k: clean(v) for k, v in zip(keys, r)} for r in rows]}
        elif kind == "detail":
            row = con.execute("select symbol,period,deposit,leverage,fromdate,todate,swap_mode,commission_per_trade,"
                              "parameters_json from input where run_id=?", (run_id,)).fetchone()
            mrow = con.execute("select * from metrics where run_id=?", (run_id,)).fetchone()
            if not row or not mrow:
                raise ValueError("không có run này")
            mcols = [d[0] for d in con.execute("select * from metrics limit 0").description]
            keys = ["symbol", "period", "deposit", "leverage", "fromdate", "todate", "swap_mode", "commission_per_trade"]
            metrics = {k: clean(v) for k, v in zip(mcols, mrow) if k != "run_id"}
            # thống kê theo chiều lệnh (long/short): số lệnh và % lệnh có lãi
            side = {"long": (0, 0), "short": (0, 0)}
            for sd, n, won in con.execute("select lower(side), count(*), sum(case when profit > 0 then 1 else 0 end) "
                                          "from trade_info where run_id=? group by lower(side)", (run_id,)):
                if sd in side:
                    side[sd] = (n or 0, won or 0)
            for sd in ("long", "short"):
                n, won = side[sd]
                metrics[f"{sd}_trades"] = n
                metrics[f"{sd}_win_pct"] = round(100.0 * won / n, 2) if n else None
            res = {"ok": True,
                   "tester": {k: clean(v) for k, v in zip(keys, row[:8])},
                   "parameters": json.loads(row[8] or "{}"),
                   "metrics": metrics}
        elif kind == "equity":
            rows = con.execute("select date,balance,equity,drawdown from daily_equity where run_id=? order by date", (run_id,)).fetchall()
            n = len(rows)
            step = max(1, n // 600)
            pts = rows[::step]
            if rows and pts[-1] != rows[-1]:
                pts.append(rows[-1])
            res = {"ok": True, "total_points": n, "points": [[r[0], clean(r[1]), clean(r[2]), clean(r[3])] for r in pts]}
        else:
            raise ValueError("kind không hỗ trợ")
        con.close()
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "error": str(e)[:300]}
    try:
        api("POST", f"/api/desk/requests/{rid}/result", res, timeout=60)
    except Exception as e:  # noqa: BLE001
        log("gửi kết quả yêu cầu lỗi", repr(e))


# ---------- vòng lặp chính ----------
def main():
    log(f"desk chạy; web={WEB}; trader={list(TRADERS)}; data={DATA_FOLDER}")
    threading.Thread(target=catalog_loop, daemon=True).start()
    backoff = 3
    while True:
        try:
            # Mỗi lúc chỉ MỘT run (smoke hoặc optimize): giới hạn CPU 50% dùng chung (Job Object RustQuantCPU50)
            # không nhận tiến trình thứ hai khi đang có run (quy tắc job lồng nhau của Windows). Web ưu tiên giao smoke trước.
            run_busy = heavy_busy.is_set() or smoke_busy.is_set()
            lanes = [] if run_busy else ["heavy", "smoke"]
            busy = run_busy or sub_busy.is_set()
            # Rảnh: giữ kết nối tối đa 25s (nhận việc tức thì). Bận: chỉ chờ 4s để vừa phục vụ yêu cầu xem trade/equity,
            # vừa nhận việc kế tiếp ngay khi xong.
            r = api("GET", f"/api/desk/poll?wait={4 if busy else 25}&accept={','.join(lanes) or 'none'}"
                           f"&accept_sub={0 if sub_busy.is_set() else 1}", timeout=45)
            backoff = 3
            item = r.get("item")
            if not item:
                continue
            if item["type"] == "job":
                is_smoke = item["job"].get("config", {}).get("type") == "smoke"
                (smoke_busy if is_smoke else heavy_busy).set()
                log(f"nhận job {item['job']['id']} ({item['job']['slug']}, {'smoke' if is_smoke else 'heavy'})")
                threading.Thread(target=run_job, args=(item["job"],), daemon=True).start()
            elif item["type"] == "submission":
                sub_busy.set()
                log(f"nhận bản nộp {item['submission']['id']} ({item['submission']['slug']})")
                threading.Thread(target=handle_submission, args=(item["submission"],), daemon=True).start()
            else:
                threading.Thread(target=handle_request, args=(item["request"],), daemon=True).start()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            log("không gọi được web:", repr(e), f"- thử lại sau {backoff}s")
            time.sleep(backoff)
            backoff = min(30, backoff * 2)
        except Exception as e:  # noqa: BLE001
            log("lỗi vòng lặp:", repr(e))
            time.sleep(backoff)
            backoff = min(30, backoff * 2)


if __name__ == "__main__":
    main()
