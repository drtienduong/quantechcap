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
from datetime import datetime
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


RUN_WAIT_MAX_MIN = 240          # chờ run khác (của web hoặc ngoài web) tối đa 4 giờ
START_RETRIES = 4               # số lần thử khởi động lại khi tiến trình run thoát sớm
STALL_WARN_MIN = 30             # không thấy tiến độ mới quá 30 phút -> ghi chú cho trader
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s'\"]+")


def rq_runs_active():
    """PID các run rq_run đang chạy trên máy (của web, agent hay alpha-lab). Chúng giữ giới hạn CPU dùng chung,
    run mới không vào được cho tới khi chúng xong."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
                            "Where-Object { $_.CommandLine -match 'rq_run\\.py' -and $_.CommandLine -match '--child' } | "
                            "ForEach-Object { $_.ProcessId }"],
                           capture_output=True, text=True, timeout=60, creationflags=0x08000000 if os.name == "nt" else 0)
        return [int(x) for x in r.stdout.split() if x.strip().isdigit()]
    except Exception:  # noqa: BLE001
        return []


def nice_progress(txt):
    """'12.3% elapsed=5m eta=30m EURUSD' -> câu tiếng Việt cho trader."""
    m = re.match(r"([\d.]+)% elapsed=(-?\d+)m eta=(-?\d+)m\s*(.*)", txt or "")
    if not m:
        return "Đang chạy"
    pct, el, eta, sym = m.groups()
    out = f"Đang chạy {float(pct):.1f}% · đã chạy {el} phút"
    if int(eta) >= 0:
        out += f" · còn khoảng {eta} phút"
    if sym.strip():
        out += f" · {sym.strip()[:20]}"
    return out


def engine_error_message(st, timeout_min):
    """Đọc cuối log của run, đưa ra lý do dễ hiểu + việc trader cần làm. Không lộ đường dẫn data-server."""
    tail = []
    try:
        lines = Path(st.get("log", "")).read_text(encoding="utf-8", errors="replace").splitlines()
        tail = [ln.strip() for ln in lines[-80:] if ln.strip() and not ln.startswith(("PROGRESS", "SYMBOL_"))]
    except Exception:  # noqa: BLE001
        pass
    if any("TIMEOUT_KILL" in ln for ln in tail):
        return (f"Run vượt giới hạn thời gian ({timeout_min:g} phút) nên đã bị dừng. "
                "Hãy thu hẹp khoảng thời gian, số symbol hoặc số biến thể rồi chạy lại.")
    keys = ("must", "invalid", "panicked", "error", "Error", "failed", "missing", "not found")
    generic = ("runner failed with exit code", "helper failed with status", "optimizer helper failed")
    hits = [ln for ln in tail if any(k in ln for k in keys) and not ln.lstrip().startswith(("File \"", "Traceback"))
            and not any(g in ln for g in generic)]
    if hits:
        reason = _PATH_RE.sub("<đường dẫn>", hits[-1])[:260]
        return (f"Engine dừng vì lỗi: {reason}. Thường do alpha (SL/TP sai phía giá, panic, chia cho 0…) hoặc cấu hình. "
                "Sửa rồi chạy lại; nếu không rõ nguyên nhân, báo quản trị kèm số job.")
    return (f"Engine dừng bất thường (mã {st.get('exit_code')}). Hãy chạy lại; nếu lặp lại, báo quản trị kèm số job.")


def run_job(job):
    jid, slug = job["id"], job["slug"]
    lane = smoke_busy if job.get("config", {}).get("type") == "smoke" else heavy_busy

    def fail(msg):
        log(f"job {jid} lỗi: {msg}")
        upd(jid, status="failed", message=str(msg)[:480])

    try:
        t = TRADERS.get(slug)
        if not t:
            return fail("Tài khoản của bạn chưa được cấu hình trên data-server. Báo quản trị.")
        cfg = job["config"]
        alpha = cfg.get("alpha", "")
        cat = _catalog.get(slug, {}).get(alpha)
        if cat is None:
            sync_catalog()
            cat = _catalog.get(slug, {}).get(alpha)
        if cat is None:
            return fail("Alpha không còn trên data-server. Kiểm tra trang Alpha hoặc nộp lại alpha.")
        clean_cfg, errs = C.validate_job(cfg, cat, LIMITS)     # KHÔNG tin web
        if errs:
            return fail("Cấu hình bị data-server từ chối: " + "; ".join(errs) + ". Sửa cấu hình rồi gửi lại.")
        alpha_file = alpha + ".rs"
        alpha_path = (t["dir"] / "alpha" / alpha_file).resolve()
        if t["dir"] not in alpha_path.parents or not alpha_path.is_file():
            return fail("Alpha không còn trên data-server. Kiểm tra trang Alpha hoặc nộp lại alpha.")
        jdir = t["dir"] / "work" / "jobs" / f"job_{jid}"
        jdir.mkdir(parents=True, exist_ok=True)
        ini = jdir / "input-optimize.ini"
        ini.write_text(C.build_ini(clean_cfg, alpha_file, DATA_FOLDER, SWAP_FOLDER), encoding="utf-8")
        env = dict(os.environ, RQ_ROOT=RQ_ROOT, TRADER_DIR=str(t["dir"]), TRADER_NAME=t["name"])
        cmd = [PY, str(TOOLS / "rq_run.py"), "--alpha", str(alpha_path), "--input", str(ini),
               "--report", str(t["dir"] / "report" / alpha), "--tag", f"web{jid}"]
        smoke = clean_cfg["type"] == "smoke"
        timeout_min = SMOKE_TIMEOUT_MIN if smoke else 200.0
        if smoke:
            cmd += ["--smoke", "--timeout-min", str(SMOKE_TIMEOUT_MIN)]
        upd(jid, status="running", message="Data-server đã nhận job, đang chuẩn bị chạy", progress=0)

        status_path, t_wait, starts = None, time.time(), 0
        while status_path is None:
            waited = (time.time() - t_wait) / 60
            if waited > RUN_WAIT_MAX_MIN:
                return fail(f"Data-server bận quá {RUN_WAIT_MAX_MIN} phút nên job chưa chạy được. Hãy gửi lại sau.")
            others = rq_runs_active()
            out = ""
            if not others:
                r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
                out = (r.stdout or "") + (r.stderr or "")
                m = re.search(r"STARTED run_id=(\S+) status=(.+)", out)
                if m:
                    sp = Path(m.group(2).strip())
                    for _ in range(120):           # chờ file trạng thái (tối đa 60 giây)
                        if sp.exists():
                            break
                        time.sleep(0.5)
                    if sp.exists():
                        status_path = sp
                        break
                    starts += 1                    # tiến trình run thoát sớm (thường do run khác vừa chen vào)
                    log(f"job {jid}: run thoát sớm (lần {starts})")
                    if starts >= START_RETRIES:
                        return fail("Data-server không khởi động được run sau nhiều lần thử. "
                                    "Hãy chạy lại sau ít phút; nếu vẫn lỗi, báo quản trị kèm số job.")
                    continue
                if "BUSY" not in out:
                    return fail("Data-server từ chối chạy: " + _PATH_RE.sub("<đường dẫn>", out.strip()[-300:]))
            resp = upd(jid, message=f"Data-server đang chạy một run khác; job của bạn sẽ tự bắt đầu khi run đó xong "
                                    f"(đã chờ {waited:.0f} phút)")
            if resp.get("cancel"):
                return upd(jid, status="cancelled", message="Đã hủy trước khi chạy")
            time.sleep(20)
        monitor_job(jid, slug, t, status_path, time.time(), timeout_min)
    except Exception as e:  # noqa: BLE001
        fail("Lỗi phía data-server: " + _PATH_RE.sub("<đường dẫn>", repr(e))[:300] + ". Hãy chạy lại; nếu lặp lại, báo quản trị.")
    finally:
        lane.clear()


def monitor_job(jid, slug, t, status_path, t0, timeout_min):
    """Theo dõi một run đến khi xong; dùng cả khi desk khởi động lại và nhận lại job đang chạy."""
    def fail(msg):
        log(f"job {jid} lỗi: {msg}")
        upd(jid, status="failed", message=str(msg)[:480])

    run_id = status_path.stem
    last, dead_since, last_pct, pct_at = 0.0, None, None, time.time()
    st = {}
    while True:
        time.sleep(2)
        try:
            st = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if st.get("state") in ("done", "failed"):
            break
        if st.get("pid") and not pid_alive(st["pid"]):
            dead_since = dead_since or time.time()
            if time.time() - dead_since > 20:
                return fail("Run dừng giữa chừng trên data-server (tiến trình bị tắt). Hãy bấm \"Chạy lại job này\".")
        else:
            dead_since = None
        if time.time() - last >= 8:
            last = time.time()
            pct, txt = read_progress(run_id)
            if pct != last_pct:
                last_pct, pct_at = pct, time.time()
            msg = nice_progress(txt)
            stall = (time.time() - pct_at) / 60
            if stall >= STALL_WARN_MIN:
                msg += f" · chưa có tiến độ mới trong {stall:.0f} phút (run sẽ tự dừng nếu quá {timeout_min:g} phút)"
            resp = upd(jid, progress=pct or 0, message=msg[:300])
            if resp.get("cancel"):
                pid = st.get("pid")
                if pid:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
                return upd(jid, status="cancelled", message="Đã hủy theo yêu cầu")
    if st.get("state") != "done":
        return fail(engine_error_message(st, timeout_min))
    finish_job(jid, slug, t, st, t0)


def finish_job(jid, slug, t, st, t0):
    run_dir = Path(st["run_dir"]).resolve()
    if t["dir"] not in run_dir.parents:
        log(f"job {jid}: thư mục kết quả ngoài workspace trader")
        return upd(jid, status="failed", message="Kết quả nằm sai chỗ trên data-server. Báo quản trị kèm số job.")
    upd(jid, message="Run xong, đang gửi kết quả lên web")
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


def find_run_status(t, jid):
    """File trạng thái rq_run mới nhất của job web<jid> trong lab/runs của trader."""
    best = None
    for p in (t["dir"] / "lab" / "runs").glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if d.get("tag") == f"web{jid}" and (best is None or p.stat().st_mtime > best[0].stat().st_mtime):
            best = (p, d)
    return best


def recover_jobs():
    """Desk vừa khởi động: nhận lại các job web đang dở (run còn chạy thì theo dõi tiếp, xong rồi thì gửi kết quả)."""
    try:
        items = api("GET", "/api/desk/active", timeout=30).get("jobs", [])
    except Exception as e:  # noqa: BLE001
        log("không lấy được danh sách job dở:", repr(e))
        return
    for j in items:
        jid, slug = j["id"], j["slug"]
        t = TRADERS.get(slug)
        smoke = j.get("config", {}).get("type") == "smoke"
        timeout_min = SMOKE_TIMEOUT_MIN if smoke else 200.0
        found = find_run_status(t, jid) if t else None
        if not found:
            log(f"nhận lại job {jid}: không có run -> lỗi")
            upd(jid, status="failed", message="Data-server khởi động lại trước khi job bắt đầu chạy. Hãy bấm \"Chạy lại job này\".")
            continue
        sp, st = found
        try:
            t0 = datetime.fromisoformat(st.get("started", "")).timestamp()
        except ValueError:
            t0 = time.time()
        if st.get("state") == "done":
            log(f"nhận lại job {jid}: run đã xong -> gửi kết quả")
            threading.Thread(target=finish_job, args=(jid, slug, t, st, t0), daemon=True).start()
        elif st.get("state") == "running" and st.get("pid") and pid_alive(st["pid"]):
            log(f"nhận lại job {jid}: run vẫn chạy -> theo dõi tiếp")
            lane = smoke_busy if smoke else heavy_busy
            lane.set()
            upd(jid, message="Data-server vừa khởi động lại, đã nhận lại job đang chạy")

            def _mon(jid=jid, slug=slug, t=t, sp=sp, t0=t0, tm=timeout_min, lane=lane):
                try:
                    monitor_job(jid, slug, t, sp, t0, tm)
                finally:
                    lane.clear()
            threading.Thread(target=_mon, daemon=True).start()
        elif st.get("state") == "failed":
            upd(jid, status="failed", message=engine_error_message(st, timeout_min))
        else:
            log(f"nhận lại job {jid}: run đã chết -> lỗi")
            upd(jid, status="failed", message="Run dừng giữa chừng khi data-server khởi động lại. Hãy bấm \"Chạy lại job này\".")


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
    recover_jobs()
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
