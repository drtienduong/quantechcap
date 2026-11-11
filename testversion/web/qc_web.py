"""QuanTechCap web - BẢN THỬ NGHIỆM (chạy cục bộ; về sau đặt trên VPS sau Caddy/TLS).

Vai trò:
- Trader đăng nhập (email + mật khẩu do Dương cấp), chọn alpha, cấu hình backtest/optimize bằng form, xem kết quả.
- Data-server (desk) GỌI RA đây: lấy job, báo tiến độ, đẩy kết quả tóm tắt, trả dữ liệu trade/equity theo yêu cầu.
  Máy chủ web KHÔNG có đường vào data-server và KHÔNG chứa data tick hay mã alpha.
- Chỉ dùng thư viện chuẩn của Python.

Chạy:  python qc_web.py        (cấu hình ở web_config.json, tạo bằng qc_admin.py init)
"""
import hashlib
import hmac
import io
import zipfile
import http.cookies
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "common"))
import qc_common as C  # noqa: E402
import qc_guide as G  # noqa: E402

# Docker: đặt QC_DATA_DIR / QC_WEB_CONFIG trỏ vào volume; không đặt thì giữ như cũ (cạnh file này).
DATA_DIR = Path(os.environ.get("QC_DATA_DIR") or HERE / "data")
DB_PATH = DATA_DIR / "qc.sqlite"
CONFIG_PATH = Path(os.environ.get("QC_WEB_CONFIG") or HERE / "web_config.json")
STATIC = HERE / "static"
STATIC_FILES = {"/static/app.js": "application/javascript; charset=utf-8",
                "/static/style.css": "text/css; charset=utf-8",
                "/static/guide.css": "text/css; charset=utf-8"}
KIT_DIR = STATIC / "kit"
KIT_FILES = {"HUONG_DAN.md", "AI_GUIDE.md", "qc_alpha_api.rs", "alpha_template.rs", "example_ma_cross_bar.rs",
             "example_tick_range_breakout.rs", "reserved_names.txt", "local_check.rs"}
KIT_ZIP = "quantechcap_alpha_kit.zip"
MAX_ALPHA_BYTES = 200_000
ASK_PER_DAY = int(os.environ.get("QC_ASK_PER_DAY", 20))   # câu hỏi AI tối đa mỗi ngày / trader
ASK_MAX_CHARS = 2000
ASK_STALE_SEC = 600            # câu hỏi đã giao cho data-server quá 10 phút chưa có trả lời -> lỗi, hỏi lại
SUB_PER_HOUR = 30          # số lần nộp alpha tối đa mỗi giờ / trader
SUB_MAX_OPEN = 3           # số bản nộp đang chờ kiểm tra cùng lúc / trader
SUB_STATUSES = ("queued", "checking", "rejected", "pending_review", "approved", "review_rejected", "failed")
# Giám sát vận hành (web tự phát hiện việc bị kẹt và báo cho trader)
SWEEP_SEC = int(os.environ.get("QC_SWEEP_SEC", 30))
SUB_CHECK_STALE_SEC = int(os.environ.get("QC_SUB_STALE_SEC", 180))   # bản nộp "đang kiểm tra" quá 3 phút -> lỗi, nộp lại
JOB_STALE_SEC = int(os.environ.get("QC_JOB_STALE_SEC", 300))         # job không có cập nhật 5 phút khi data-server online -> lỗi
DESK_STABLE_SEC = int(os.environ.get("QC_DESK_STABLE_SEC", 120))     # data-server online liên tục 2 phút mới xét mất theo dõi
DEFAULTS = {"host": "127.0.0.1", "port": 8800, "desk_token": "", "secure_cookie": False,
            "session_hours": 12, "limits": {}}
MAX_BODY = 12 * 1024 * 1024
DESK_ONLINE_SEC = 45
REQUEST_TIMEOUT_SEC = 120
PAGE = 100

_dispatch_lock = threading.Lock()
_login_fail = {}  # (email) -> [timestamps]


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    # Trong container phải nghe 0.0.0.0 để Docker chuyển cổng vào; giới hạn ra ngoài do docker-compose (127.0.0.1:8800).
    if os.environ.get("QC_HOST"):
        cfg["host"] = os.environ["QC_HOST"]
    if os.environ.get("QC_PORT"):
        cfg["port"] = int(os.environ["QC_PORT"])
    # Production sau Caddy/HTTPS: QC_SECURE_COOKIE=1 (cookie chỉ gửi qua HTTPS). Dev http://127.0.0.1 để 0.
    if os.environ.get("QC_SECURE_COOKIE", "").strip():
        cfg["secure_cookie"] = os.environ["QC_SECURE_COOKIE"].strip().lower() in ("1", "true", "yes")
    return cfg


CFG = load_config()
LIMITS = dict(C.DEFAULT_LIMITS, **CFG.get("limits", {}))


def db():
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma journal_mode=WAL")
    con.execute("pragma busy_timeout=30000")
    return con


SCHEMA = """
create table if not exists users(id integer primary key, email text unique not null, pw text not null,
  slug text unique not null, is_admin integer default 0, created text);
create table if not exists sessions(token text primary key, user_id integer not null, csrf text not null, expires real not null);
create table if not exists alphas(user_id integer not null, name text not null, params_json text not null,
  updated text, primary key(user_id, name));
create table if not exists jobs(id integer primary key autoincrement, user_id integer not null, type text, alpha text,
  config_json text, status text, progress real default 0, message text default '', created text, started text,
  finished text, cancel integer default 0, summary_json text, last_update real);
create table if not exists variants(job_id integer not null, run_id integer not null, symbol text, params_json text,
  trades integer, net_profit real, dd_pct real, pf real, winrate real, sharpe real, tit real, fitness real,
  recovery real, is_pass integer, recent_sharpe real, stopped_out integer default 0, rejected_orders integer default 0,
  primary key(job_id, run_id));
create table if not exists requests(id integer primary key autoincrement, user_id integer not null, job_id integer not null,
  kind text, payload_json text, status text, result_json text, created real);
create table if not exists meta(k text primary key, v text);
create table if not exists questions(id integer primary key autoincrement, user_id integer not null, kind text,
  ref_id integer, question text not null, status text, answer text, created text, answered text, dispatched_at real);
create table if not exists submissions(id integer primary key autoincrement, user_id integer not null, name text not null,
  source text, sha256 text, size integer, status text, message text default '', result_json text, created text, updated text);
"""


def init_db():
    con = db()
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("pragma table_info(jobs)")}
    if "progress_at" not in cols:
        con.execute("alter table jobs add column progress_at real")
    if "meta_json" not in {r[1] for r in con.execute("pragma table_info(alphas)")}:
        con.execute("alter table alphas add column meta_json text")
    ucols = {r[1] for r in con.execute("pragma table_info(users)")}
    if "os1_access" not in ucols:   # 0 = trader loại 1 (chỉ IS), 1 = loại 2 (được chạy tới OS1)
        con.execute("alter table users add column os1_access integer default 0")
    jcols = {r[1] for r in con.execute("pragma table_info(jobs)")}
    if "src_job_id" not in jcols:   # job OS1: biến thể nguồn (job IS + run)
        con.execute("alter table jobs add column src_job_id integer")
        con.execute("alter table jobs add column src_run_id integer")
    if "ext_key" not in jcols:      # report offline (chạy ngoài web) nhập từ desk
        con.execute("alter table jobs add column ext_key text")
        con.execute("alter table jobs add column source text")
    con.execute("create unique index if not exists ux_jobs_ext on jobs(ext_key) where ext_key is not null")
    # mỗi biến thể chỉ chạy OS1 một lần (job lỗi/hủy trước khi có kết quả thì được nộp lại)
    con.execute("create unique index if not exists ux_os1_src on jobs(src_job_id, src_run_id) "
                "where type='os1' and status not in ('failed','cancelled')")
    vcols = {r[1] for r in con.execute("pragma table_info(variants)")}
    if "stopped_out" not in vcols:
        con.execute("alter table variants add column stopped_out integer default 0")
    if "rejected_orders" not in vcols:
        con.execute("alter table variants add column rejected_orders integer default 0")
    con.commit()
    con.close()


# ---------- mật khẩu ----------
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def alpha_suffix(slug):
    """Hậu tố tên alpha của trader: slug đổi '-' thành '_' (desk dùng cùng quy ước và tự kiểm lại)."""
    return "_" + slug.replace("-", "_")


def touch_desk(con):
    now = time.time()
    if now - desk_seen(con) > DESK_ONLINE_SEC:      # vừa online trở lại
        con.execute("insert into meta(k,v) values('desk_online_since',?) on conflict(k) do update set v=excluded.v", (str(now),))
    con.execute("insert into meta(k,v) values('desk_seen',?) on conflict(k) do update set v=excluded.v", (str(now),))
    con.commit()


def meta_f(con, k):
    r = con.execute("select v from meta where k=?", (k,)).fetchone()
    return float(r["v"]) if r else 0.0


def utc_hm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M %d/%m") if ts else "?"


def desk_state(con):
    seen = desk_seen(con)
    return {"online": time.time() - seen < DESK_ONLINE_SEC, "seen": seen}


def offline_note(con, what):
    d = desk_state(con)
    if d["online"]:
        return ""
    return (f"Data-server đang offline (liên lạc lần cuối {utc_hm(d['seen'])} UTC). "
            f"{what} sẽ được xử lý tự động khi data-server hoạt động lại, bạn không cần gửi lại.")


def job_notice(con, r):
    """Thông báo vận hành cho trader về một job (không lộ thông tin của trader khác, chỉ số đếm)."""
    st = r["status"]
    if st == "queued":
        off = offline_note(con, "Job")
        if off:
            return off
        smoke = 1 if r["type"] == "smoke" else 0
        ahead = con.execute("select count(*) from jobs where status='queued' and "
                            "((type='smoke') > ? or ((type='smoke') = ? and id < ?))", (smoke, smoke, r["id"])).fetchone()[0]
        busy = con.execute("select count(*) from jobs where status in ('dispatched','running')").fetchone()[0]
        n = ahead + busy
        return ("Đang chờ: data-server đang chạy job khác, trước job này còn " + str(n) + " job.") if n else "Đang chờ data-server nhận job."
    if st in ("dispatched", "running"):
        off = offline_note(con, "Job")
        if off:
            return "Mất liên lạc với data-server khi job đang chạy. Run có thể vẫn tiếp tục; " + off.split(". ", 1)[1]
    if st == "done":
        return zero_trade_notice(con, r)
    return ""


def zero_trade_notice(con, r):
    """Job xong mà không có lệnh (hoặc phần lớn biến thể không có lệnh): nhắc trader kiểm tra cấu hình."""
    n, total, zero = con.execute("select count(*), coalesce(sum(trades),0), sum(case when coalesce(trades,0)=0 then 1 else 0 end) "
                                 "from variants where job_id=?", (r["id"],)).fetchone()
    if not n:
        return ""
    try:
        cfg = json.loads(con.execute("select config_json from jobs where id=?", (r["id"],)).fetchone()[0])
    except Exception:  # noqa: BLE001
        cfg = {}
    hints = []
    a = con.execute("select meta_json from alphas where user_id=(select user_id from jobs where id=?) and name=?",
                    (r["id"], r["alpha"])).fetchone()
    req = json.loads(a["meta_json"] or "{}").get("required_period") if a else None
    if req and cfg.get("period") and cfg["period"] != req:
        hints.append(f"alpha yêu cầu khung {req} nhưng job chạy {cfg['period']}")
    if total == 0:
        hints += ["khung thời gian (alpha có lọc theo khung nến không?)",
                  "chế độ symbol (alpha dùng ..._with_account chỉ giao dịch ở chế độ Portfolio)",
                  "tham số và khoảng thời gian (điều kiện vào lệnh có bao giờ xảy ra không?)"]
        return "Job xong nhưng KHÔNG có lệnh nào. Kiểm tra: " + "; ".join(hints) + "."
    if zero * 2 > n:
        return f"{zero}/{n} biến thể không có lệnh nào." + (" Lưu ý: " + hints[0] + "." if hints else "")
    return ""


def sweep_once():
    """Phát hiện việc bị kẹt, chuyển sang trạng thái lỗi kèm hướng dẫn cho trader."""
    con = db()
    try:
        now = time.time()
        cutoff = datetime.fromtimestamp(now - SUB_CHECK_STALE_SEC, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        con.execute("update submissions set status='failed', updated=?, message=? where status='checking' and updated < ?",
                    (now_iso(), "Data-server không trả kết quả kiểm tra (có thể vừa khởi động lại). Mã đã được xóa khỏi web, "
                                "hãy nộp lại file.", cutoff))
        con.execute("update questions set status='failed', answered=?, answer=? where status='dispatched' and dispatched_at < ?",
                    (now_iso(), "Trợ lý AI chưa trả lời kịp (data-server có thể vừa khởi động lại). Hãy hỏi lại.", now - ASK_STALE_SEC))
        d = desk_state(con)
        stable = d["online"] and now - meta_f(con, "desk_online_since") > DESK_STABLE_SEC
        if stable:
            con.execute("update jobs set status='failed', finished=?, message=? where status in ('dispatched','running') "
                        "and coalesce(last_update, 0) < ?",
                        (now_iso(), "Data-server mất theo dõi job này (có thể vừa khởi động lại hoặc run bị dừng). "
                                    "Hãy bấm \"Chạy lại job này\".", now - JOB_STALE_SEC))
        con.commit()
    finally:
        con.close()


def sweeper():
    while True:
        try:
            sweep_once()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"lỗi sweeper: {e!r}\n")
        time.sleep(SWEEP_SEC)


def desk_seen(con):
    r = con.execute("select v from meta where k='desk_seen'").fetchone()
    return float(r["v"]) if r else 0.0


class Handler(BaseHTTPRequestHandler):
    server_version = "QuanTechCap/0.1"

    def log_message(self, fmt, *args):
        if os.environ.get("QC_VERBOSE"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---------- tiện ích ----------
    def send_bytes(self, status, body: bytes, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if not getattr(self, "_head", False):
            self.wfile.write(body)

    def send_json(self, obj, status=200, extra=None):
        self.send_bytes(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)

    def fail(self, status, msg):
        self.send_json({"ok": False, "error": msg}, status)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("body quá lớn")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def session(self, con):
        raw = self.headers.get("Cookie", "")
        c = http.cookies.SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return None
        m = c.get("qc_session")
        if not m:
            return None
        s = con.execute("select s.token, s.csrf, s.expires, u.id, u.email, u.slug, u.is_admin, u.os1_access "
                        "from sessions s join users u on u.id=s.user_id where s.token=?", (m.value,)).fetchone()
        if not s or s["expires"] < time.time():
            return None
        return s

    def desk_auth(self):
        tok = CFG.get("desk_token") or ""
        got = self.headers.get("Authorization", "")
        return bool(tok) and got.startswith("Bearer ") and hmac.compare_digest(got[7:], tok)

    # ---------- định tuyến ----------
    def do_GET(self):
        self._head = False
        self.route("GET")

    def do_POST(self):
        self._head = False
        self.route("POST")

    def do_HEAD(self):
        self._head = True
        self.route("GET")

    def route(self, method):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        try:
            if path.startswith("/api/desk/"):
                if not self.desk_auth():
                    return self.fail(401, "desk token sai")
                return self.desk_api(method, path, q)
            if path == "/api/login" and method == "POST":
                return self.login()
            if path.startswith("/api/"):
                return self.user_api(method, path, q)
            if path.startswith("/kit/") and method == "GET":
                return self.kit_file(path[5:])
            if path in ("/guide_ai", "/guide_ai/", "/guide_ai.md", "/guide_ai.txt") and method == "GET":
                return self.guide(path)
            if path in STATIC_FILES and method == "GET":
                return self.send_bytes(200, (STATIC / path.split("/")[-1]).read_bytes(), STATIC_FILES[path])
            if path == "/login" and method == "GET":
                return self.send_bytes(200, (STATIC / "login.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/" and method == "GET":
                con = db()
                try:
                    s = self.session(con)
                finally:
                    con.close()
                if not s:
                    return self.send_bytes(302, b"", "text/plain", [("Location", "/login")])
                return self.send_bytes(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            return self.fail(404, "không tìm thấy")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"lỗi {method} {path}: {e!r}\n")
            try:
                self.fail(500, "lỗi máy chủ")
            except Exception:
                pass

    def guide(self, path):
        """Trang hướng dẫn bộ khung alpha cho AI: CÔNG KHAI (Dương quyết 2026-11-06), không cần đăng nhập.
        Chỉ chứa nội dung static/kit/, không chứa dữ liệu trader, alpha hay đường dẫn data-server."""
        base = (CFG.get("public_url") or "https://research.quantechcap.com").rstrip("/")
        extra = [("X-Robots-Tag", "noindex")]
        if path in ("/guide_ai.md", "/guide_ai.txt"):
            ctype = "text/markdown; charset=utf-8" if path.endswith(".md") else "text/plain; charset=utf-8"
            return self.send_bytes(200, G.bundle_md(KIT_DIR, base, KIT_ZIP).encode("utf-8"), ctype, extra)
        return self.send_bytes(200, G.page_html(KIT_DIR, base, KIT_ZIP).encode("utf-8"), "text/html; charset=utf-8", extra)

    def kit_file(self, name):
        # công khai cùng trang /guide_ai (bản offline cho AI không đọc được web)
        if name == KIT_ZIP:
            # toàn bộ bộ khung trong một file zip (thư mục quantechcap_alpha_kit/)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                base = (CFG.get("public_url") or "https://research.quantechcap.com").rstrip("/")
                # bản gộp một file (giống /guide_ai.md): AI chỉ nhận ít file đính kèm thì dùng file này
                z.writestr("quantechcap_alpha_kit/QUANTECHCAP_GUIDE_AI.md", G.bundle_md(KIT_DIR, base, KIT_ZIP))
                for f in sorted(KIT_FILES):
                    if (KIT_DIR / f).is_file():
                        z.write(KIT_DIR / f, "quantechcap_alpha_kit/" + f)
            return self.send_bytes(200, buf.getvalue(), "application/zip",
                                   [("Content-Disposition", 'attachment; filename="%s"' % KIT_ZIP)])
        if name not in KIT_FILES or not (KIT_DIR / name).is_file():
            return self.fail(404, "không tìm thấy")
        ctype = "text/markdown; charset=utf-8" if name.endswith(".md") else "text/plain; charset=utf-8"
        return self.send_bytes(200, (KIT_DIR / name).read_bytes(), ctype,
                               [("Content-Disposition", 'attachment; filename="%s"' % name)])

    # ---------- đăng nhập ----------
    def login(self):
        try:
            body = self.read_json()
        except Exception:
            return self.fail(400, "JSON không hợp lệ")
        email = str(body.get("email", "")).strip().lower()
        pw = str(body.get("password", ""))
        now = time.time()
        fails = [t for t in _login_fail.get(email, []) if now - t < 600]
        if len(fails) >= 5:
            return self.fail(429, "Sai quá nhiều lần, thử lại sau 10 phút")
        con = db()
        try:
            u = con.execute("select * from users where email=?", (email,)).fetchone()
            ok = bool(u) and check_pw(pw, u["pw"])
            if not u:
                check_pw(pw, "scrypt$00$00")  # làm chậm tương đương để khó dò email
            if not ok:
                fails.append(now)
                _login_fail[email] = fails
                return self.fail(401, "Email hoặc mật khẩu sai")
            _login_fail.pop(email, None)
            tok, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            con.execute("insert into sessions(token,user_id,csrf,expires) values(?,?,?,?)",
                        (tok, u["id"], csrf, now + CFG["session_hours"] * 3600))
            con.execute("delete from sessions where expires < ?", (now,))
            con.commit()
        finally:
            con.close()
        cookie = f"qc_session={tok}; Path=/; HttpOnly; SameSite=Strict; Max-Age={CFG['session_hours'] * 3600}"
        if CFG.get("secure_cookie"):
            cookie += "; Secure"
        self.send_json({"ok": True}, extra=[("Set-Cookie", cookie)])

    # ---------- API của trader ----------
    def user_api(self, method, path, q):
        con = db()
        try:
            s = self.session(con)
            if not s:
                return self.fail(401, "chưa đăng nhập")
            if method == "POST" and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), s["csrf"]):
                return self.fail(403, "CSRF")
            uid = s["id"]
            parts = [p for p in path.split("/") if p][1:]  # bỏ "api"
            if parts == ["logout"] and method == "POST":
                con.execute("delete from sessions where token=?", (s["token"],))
                con.commit()
                return self.send_json({"ok": True}, extra=[("Set-Cookie", "qc_session=; Path=/; Max-Age=0")])
            if parts == ["status"]:
                seen = desk_seen(con)
                return self.send_json({"ok": True, "email": s["email"], "slug": s["slug"], "csrf": s["csrf"],
                                       "desk_notice": offline_note(con, "Job và bản nộp alpha"),
                                       "alpha_suffix": alpha_suffix(s["slug"]), "kit": sorted(KIT_FILES),
                                       "os1_access": bool(s["os1_access"]),
                                       "todate_max": LIMITS["os1_end"] if s["os1_access"] else LIMITS["is_end"],
                                       "desk_online": time.time() - seen < DESK_ONLINE_SEC, "desk_seen": seen,
                                       "limits": LIMITS, "fx25": C.FX25, "symbols": C.symbols_info(), "groups": C.GROUPS, "periods": C.PERIODS, "job_types": C.JOB_TYPES})
            if parts == ["alphas"]:
                rows = con.execute("select name, params_json, meta_json, updated from alphas where user_id=? order by name",
                                   (uid,)).fetchall()
                return self.send_json({"ok": True, "alphas": [
                    {"name": r["name"], "params": json.loads(r["params_json"]), "meta": json.loads(r["meta_json"] or "{}"),
                     "updated": r["updated"]} for r in rows]})
            if parts == ["jobs"] and method == "GET":
                off = max(0, int(q.get("offset", ["0"])[0]))
                lim = min(100, max(1, int(q.get("limit", ["30"])[0])))
                kind = "type='os1'" if q.get("kind", [""])[0] == "os1" else "type!='os1'"
                src = q.get("src", [""])[0]
                if src == "web":
                    kind += " and type!='offline'"
                elif src == "offline":
                    kind += " and type='offline'"
                rows = con.execute("select id,type,alpha,status,progress,message,created,finished,summary_json,src_job_id,src_run_id,source "
                                   f"from jobs where user_id=? and {kind} order by created desc, id desc limit ? offset ?",
                                   (uid, lim, off)).fetchall()
                total = con.execute(f"select count(*) from jobs where user_id=? and {kind}", (uid,)).fetchone()[0]
                n_off = con.execute("select count(*) from jobs where user_id=? and type='offline'", (uid,)).fetchone()[0]
                return self.send_json({"ok": True, "total": total, "offline_total": n_off,
                                       "jobs": [dict(self.job_row(r), notice=job_notice(con, r)) for r in rows]})
            if parts == ["jobs"] and method == "POST":
                return self.create_job(con, uid)
            if parts == ["ask"] and method == "POST":
                return self.create_question(con, uid)
            if parts == ["ask"] and method == "GET":
                rows = con.execute("select id, kind, ref_id, question, status, answer, created, answered from questions "
                                   "where user_id=? order by id desc limit 30", (uid,)).fetchall()
                since = datetime.fromtimestamp(time.time() - 86400, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                used = con.execute("select count(*) from questions where user_id=? and created >= ?", (uid, since)).fetchone()[0]
                note = offline_note(con, "Câu hỏi")
                return self.send_json({"ok": True, "per_day": ASK_PER_DAY, "used_24h": used,
                                       "questions": [dict(r, notice=note if r["status"] == "queued" else "") for r in rows]})
            if parts == ["ispass"] and method == "GET":
                return self.ispass_list(con, uid, q)
            if parts == ["os1"] and method == "POST":
                return self.create_os1(con, uid)
            if parts == ["submissions"] and method == "POST":
                return self.create_submission(con, uid, s["slug"])
            if parts == ["submissions"] and method == "GET":
                rows = con.execute("select id,name,sha256,size,status,message,created,updated from submissions "
                                   "where user_id=? order by id desc limit 50", (uid,)).fetchall()
                note = offline_note(con, "Bản nộp")
                return self.send_json({"ok": True, "submissions": [
                    dict(r, notice=note if r["status"] == "queued" else "") for r in rows]})
            if len(parts) == 2 and parts[0] == "submissions" and parts[1].isdigit() and method == "GET":
                r = con.execute("select id,name,sha256,size,status,message,result_json,created,updated from submissions "
                                "where id=? and user_id=?", (int(parts[1]), uid)).fetchone()
                if not r:
                    return self.fail(404, "không có bản nộp")
                d = dict(r)
                d["result"] = json.loads(d.pop("result_json") or "null")
                return self.send_json({"ok": True, "submission": d})
            if len(parts) >= 2 and parts[0] == "jobs" and parts[1].isdigit():
                return self.job_api(con, uid, int(parts[1]), parts[2:], method, q)
            if len(parts) == 2 and parts[0] == "requests" and parts[1].isdigit() and method == "GET":
                r = con.execute("select * from requests where id=? and user_id=?", (int(parts[1]), uid)).fetchone()
                if not r:
                    return self.fail(404, "không có yêu cầu")
                status = r["status"]
                if status != "done" and time.time() - r["created"] > REQUEST_TIMEOUT_SEC:
                    status = "timeout"
                out = {"ok": True, "status": status}
                if status == "done":
                    out["result"] = json.loads(r["result_json"])
                return self.send_json(out)
            return self.fail(404, "không tìm thấy")
        finally:
            con.close()

    @staticmethod
    def job_row(r):
        d = {k: r[k] for k in r.keys() if k != "summary_json"}
        d["summary"] = json.loads(r["summary_json"]) if r["summary_json"] else None
        return d

    def create_job(self, con, uid):
        try:
            cfg = self.read_json()
        except Exception:
            return self.fail(400, "JSON không hợp lệ")
        if not isinstance(cfg, dict) or not isinstance(cfg.get("alpha"), str):
            return self.fail(400, "thiếu alpha")
        a = con.execute("select params_json from alphas where user_id=? and name=?", (uid, cfg["alpha"])).fetchone()
        if not a:
            return self.fail(400, "alpha không có trong danh sách của bạn (data-server chưa đồng bộ?)")
        os1_access = bool(con.execute("select os1_access from users where id=?", (uid,)).fetchone()[0])
        clean, errs = C.validate_job(cfg, json.loads(a["params_json"]), LIMITS, os1_access=os1_access)
        if errs:
            return self.send_json({"ok": False, "error": "cấu hình không hợp lệ", "details": errs}, 400)
        cur = con.execute("insert into jobs(user_id,type,alpha,config_json,status,created,last_update) values(?,?,?,?,?,?,?)",
                          (uid, clean["type"], clean["alpha"], json.dumps(clean), "queued", now_iso(), time.time()))
        con.commit()
        self.send_json({"ok": True, "id": cur.lastrowid})

    def create_question(self, con, uid):
        """Trader hỏi trợ lý AI. Câu hỏi chờ data-server lấy về; ngữ cảnh chỉ gồm dữ liệu của chính trader."""
        try:
            body = self.read_json()
        except Exception:
            return self.fail(400, "JSON không hợp lệ")
        q = str(body.get("question", "")).strip() if isinstance(body, dict) else ""
        if not q or len(q) > ASK_MAX_CHARS:
            return self.fail(400, f"câu hỏi phải có nội dung và tối đa {ASK_MAX_CHARS} ký tự")
        kind, ref = "general", None
        if body.get("job_id"):
            ref = int(body["job_id"]) if str(body["job_id"]).isdigit() else 0
            if not con.execute("select 1 from jobs where id=? and user_id=?", (ref, uid)).fetchone():
                return self.fail(404, "không có job này")
            kind = "job"
        elif body.get("submission_id"):
            ref = int(body["submission_id"]) if str(body["submission_id"]).isdigit() else 0
            if not con.execute("select 1 from submissions where id=? and user_id=?", (ref, uid)).fetchone():
                return self.fail(404, "không có bản nộp này")
            kind = "submission"
        since = datetime.fromtimestamp(time.time() - 86400, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if con.execute("select count(*) from questions where user_id=? and created >= ?", (uid, since)).fetchone()[0] >= ASK_PER_DAY:
            return self.fail(429, f"đã hỏi đủ {ASK_PER_DAY} câu trong 24 giờ, hãy thử lại sau")
        if con.execute("select count(*) from questions where user_id=? and status in ('queued','dispatched')", (uid,)).fetchone()[0] >= 2:
            return self.fail(429, "đang có câu hỏi chờ trả lời, đợi xong rồi hỏi tiếp")
        cur = con.execute("insert into questions(user_id,kind,ref_id,question,status,created) values(?,?,?,?,?,?)",
                          (uid, kind, ref, q, "queued", now_iso()))
        con.commit()
        return self.send_json({"ok": True, "id": cur.lastrowid})

    def question_payload(self, con, qrow):
        """Dữ liệu web gửi kèm câu hỏi cho data-server: CHỈ của trader hỏi (không mã alpha, không dữ liệu trader khác)."""
        uid = qrow["user_id"]
        u = con.execute("select slug, os1_access from users where id=?", (uid,)).fetchone()
        out = {"id": qrow["id"], "slug": u["slug"], "kind": qrow["kind"], "question": qrow["question"],
               "account": {"os1_access": bool(u["os1_access"]), "alpha_suffix": alpha_suffix(u["slug"]),
                           "todate_max": LIMITS["os1_end"] if u["os1_access"] else LIMITS["is_end"]}}
        if qrow["kind"] == "job":
            j = con.execute("select * from jobs where id=? and user_id=?", (qrow["ref_id"], uid)).fetchone()
            if j:
                n, zero, tot = con.execute("select count(*), sum(case when coalesce(trades,0)=0 then 1 else 0 end), coalesce(sum(trades),0) "
                                           "from variants where job_id=?", (j["id"],)).fetchone()
                stop, rej = con.execute("select coalesce(sum(stopped_out),0), coalesce(sum(rejected_orders),0) from variants where job_id=?",
                                        (j["id"],)).fetchone()
                out["job"] = {"id": j["id"], "type": j["type"], "alpha": j["alpha"], "status": j["status"], "message": j["message"],
                              "created": j["created"], "config": json.loads(j["config_json"] or "{}"),
                              "summary": json.loads(j["summary_json"]) if j["summary_json"] else None, "notice": job_notice(con, j),
                              "variants": n, "variants_zero_trades": zero or 0, "total_trades": tot,
                              "variants_stopped_out": stop, "rejected_orders": rej}
        elif qrow["kind"] == "submission":
            sb = con.execute("select id, name, status, message, result_json, created from submissions where id=? and user_id=?",
                             (qrow["ref_id"], uid)).fetchone()
            if sb:
                out["submission"] = {k: sb[k] for k in ("id", "name", "status", "message", "created")}
                out["submission"]["result"] = json.loads(sb["result_json"] or "null")
        out["recent_jobs"] = [dict(r) for r in con.execute(
            "select id, type, alpha, status, substr(message,1,200) as message, created from jobs where user_id=? "
            "and type!='offline' order by id desc limit 6", (uid,))]
        out["recent_submissions"] = [dict(r) for r in con.execute(
            "select id, name, status, substr(message,1,200) as message, created from submissions where user_id=? order by id desc limit 6", (uid,))]
        out["alphas"] = [{"name": r["name"], "meta": json.loads(r["meta_json"] or "{}"), "params": list(json.loads(r["params_json"]).keys())}
                         for r in con.execute("select name, params_json, meta_json from alphas where user_id=? order by name", (uid,))]
        return out

    ISPASS_SQL = ("from variants v join jobs j on j.id=v.job_id where j.user_id=? and j.type='optimize_is' "
                  "and j.status='done' and v.is_pass=1 and coalesce(v.stopped_out,0)=0")

    def ispass_list(self, con, uid, q):
        """Trang Alpha pass IS: mọi biến thể pass IS (không stop-out) của trader, kèm trạng thái OS1 của từng biến thể."""
        off = max(0, int(q.get("offset", ["0"])[0]))
        lim = min(100, max(1, int(q.get("limit", ["20"])[0])))
        total = con.execute("select count(*) " + self.ISPASS_SQL, (uid,)).fetchone()[0]
        rows = con.execute("select v.job_id, v.run_id, v.symbol, v.params_json, v.trades, v.net_profit, v.dd_pct, v.pf, "
                           "v.sharpe, v.recent_sharpe, v.fitness, v.tit, j.alpha, j.config_json, j.finished "
                           + self.ISPASS_SQL + " order by j.id desc, v.fitness desc, v.run_id limit ? offset ?",
                           (uid, lim, off)).fetchall()
        out = []
        for r in rows:
            cfg = json.loads(r["config_json"] or "{}")
            o = con.execute("select id, status, summary_json from jobs where user_id=? and type='os1' and src_job_id=? "
                            "and src_run_id=? order by id desc limit 1", (uid, r["job_id"], r["run_id"])).fetchone()
            os1 = None
            if o:
                sm = json.loads(o["summary_json"]) if o["summary_json"] else {}
                os1 = {"job_id": o["id"], "status": o["status"], "os_pass": (sm.get("os1") or {}).get("os_pass")}
            out.append({"job_id": r["job_id"], "run_id": r["run_id"], "symbol": r["symbol"], "alpha": r["alpha"],
                        "period": cfg.get("period"), "fromdate": cfg.get("fromdate"), "todate": cfg.get("todate"),
                        "params": json.loads(r["params_json"] or "{}"),
                        "opt_keys": [k for k, v in (cfg.get("params") or {}).items() if isinstance(v, list) and len(v) == 3 and v[1]],
                        "trades": r["trades"], "net_profit": r["net_profit"], "dd_pct": r["dd_pct"], "pf": r["pf"],
                        "sharpe": r["sharpe"], "recent_sharpe": r["recent_sharpe"], "fitness": r["fitness"], "tit": r["tit"],
                        "finished": r["finished"], "os1": os1})
        return self.send_json({"ok": True, "total": total, "offset": off, "rows": out})

    def create_os1(self, con, uid):
        """Nộp các biến thể đã pass IS để chạy OS1. Mỗi biến thể chỉ một lần (trừ khi lần trước lỗi/hủy).
        Data-server kiểm lại độc lập (biến thể có pass IS không) và tự dựng cấu hình từ report IS."""
        try:
            body = self.read_json()
        except Exception:
            return self.fail(400, "JSON không hợp lệ")
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list) or not items or len(items) > 50:
            return self.fail(400, "chọn từ 1 đến 50 biến thể")
        created, skipped = [], []
        for it in items:
            try:
                sj, sr = int(it["job_id"]), int(it["run_id"])
            except (KeyError, TypeError, ValueError):
                return self.fail(400, "mục không hợp lệ")
            v = con.execute("select v.symbol, v.params_json, j.alpha, j.config_json " + self.ISPASS_SQL +
                            " and v.job_id=? and v.run_id=?", (uid, sj, sr)).fetchone()
            if not v:
                skipped.append({"job_id": sj, "run_id": sr, "reason": "không phải biến thể pass IS của bạn"})
                continue
            cfg = json.loads(v["config_json"] or "{}")
            if (cfg.get("todate") or "") > LIMITS["is_end"]:
                skipped.append({"job_id": sj, "run_id": sr, "reason": "job gốc đã chạy qua 2024-01-01"})
                continue
            prev = con.execute("select id from jobs where user_id=? and type='os1' and src_job_id=? and src_run_id=? "
                               "and status not in ('failed','cancelled')", (uid, sj, sr)).fetchone()
            if prev:
                skipped.append({"job_id": sj, "run_id": sr, "reason": f"đã chạy OS1 (job #{prev['id']})"})
                continue
            ocfg = {"type": "os1", "alpha": v["alpha"], "src_job_id": sj, "src_run_id": sr, "symbol": v["symbol"],
                    "params": json.loads(v["params_json"] or "{}"), "period": cfg.get("period"),
                    "symbols_mode": cfg.get("symbols_mode"), "symbols": cfg.get("symbols"),
                    "fromdate": cfg.get("fromdate"), "todate": LIMITS["os1_end"], "src_todate": cfg.get("todate")}
            try:
                cur = con.execute("insert into jobs(user_id,type,alpha,config_json,status,created,last_update,src_job_id,src_run_id) "
                                  "values(?,?,?,?,?,?,?,?,?)", (uid, "os1", v["alpha"], json.dumps(ocfg), "queued", now_iso(),
                                                                time.time(), sj, sr))
            except sqlite3.IntegrityError:
                skipped.append({"job_id": sj, "run_id": sr, "reason": "đã chạy OS1"})
                continue
            created.append(cur.lastrowid)
        con.commit()
        return self.send_json({"ok": True, "created": created, "skipped": skipped})

    def create_submission(self, con, uid, slug):
        """Trader nộp MÃ NGUỒN alpha. Web chỉ giữ mã tới khi data-server kéo về (rồi xóa). Không biên dịch/chạy gì ở đây."""
        try:
            body = self.read_json()
        except Exception:
            return self.fail(400, "JSON không hợp lệ")
        name, src = body.get("name"), body.get("source")
        # Trader gõ tên bất kỳ; platform tự thêm hậu tố cố định của tài khoản (desk tự kiểm lại và sửa fn name()).
        name, name_err = C.full_alpha_name(name, alpha_suffix(slug))
        if name_err:
            return self.fail(400, name_err)
        if not isinstance(src, str) or not src.strip():
            return self.fail(400, "thiếu mã nguồn")
        raw = src.encode("utf-8")
        if len(raw) > MAX_ALPHA_BYTES:
            return self.fail(400, f"file quá lớn (tối đa {MAX_ALPHA_BYTES // 1000} KB)")
        since = datetime.fromtimestamp(time.time() - 3600, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        n_hour = con.execute("select count(*) from submissions where user_id=? and created >= ?", (uid, since)).fetchone()[0]
        if n_hour >= SUB_PER_HOUR:
            return self.fail(429, f"nộp quá {SUB_PER_HOUR} lần trong 1 giờ, thử lại sau")
        n_open = con.execute("select count(*) from submissions where user_id=? and status in ('queued','checking')",
                             (uid,)).fetchone()[0]
        if n_open >= SUB_MAX_OPEN:
            return self.fail(429, "đang có bản nộp chờ kiểm tra, đợi xong rồi nộp tiếp")
        cur = con.execute("insert into submissions(user_id,name,source,sha256,size,status,message,created,updated) "
                          "values(?,?,?,?,?,?,?,?,?)",
                          (uid, name, src, hashlib.sha256(raw).hexdigest(), len(raw), "queued", "Chờ data-server kiểm tra",
                           now_iso(), now_iso()))
        con.commit()
        self.send_json({"ok": True, "id": cur.lastrowid, "name": name})

    def job_api(self, con, uid, jid, rest, method, q):
        j = con.execute("select * from jobs where id=? and user_id=?", (jid, uid)).fetchone()
        if not j:
            return self.fail(404, "không có job")
        if not rest and method == "GET":
            d = self.job_row(j)
            d["config"] = json.loads(j["config_json"])
            d["notice"] = job_notice(con, j)
            return self.send_json({"ok": True, "job": d})
        if rest == ["cancel"] and method == "POST":
            if j["status"] == "queued":
                con.execute("update jobs set status='cancelled', finished=?, message='Đã hủy trước khi chạy' where id=?", (now_iso(), jid))
            elif j["status"] in ("dispatched", "running"):
                con.execute("update jobs set cancel=1 where id=?", (jid,))
            con.commit()
            return self.send_json({"ok": True})
        if rest == ["variants"] and method == "GET":
            sort = {"fitness": "fitness", "sharpe": "sharpe", "net_profit": "net_profit", "trades": "trades",
                    "dd": "dd_pct", "run_id": "run_id", "recent": "recent_sharpe", "pf": "pf", "recovery": "recovery",
                    "winrate": "winrate", "tit": "tit"}.get(q.get("sort", ["fitness"])[0], "fitness")
            order = "asc" if q.get("dir", ["desc"])[0] == "asc" else "desc"
            off = max(0, int(q.get("offset", ["0"])[0]))
            # bộ lọc (giống Rust Quant Viewer): pass IS, có lệnh, symbol, tìm kiếm, khoảng giá trị tham số tối ưu
            where, args = ["job_id=?"], [jid]
            if q.get("pass", ["0"])[0] == "1":
                where.append("is_pass=1")
            if q.get("traded", ["0"])[0] == "1":
                where.append("coalesce(trades,0)>0")
            sym = q.get("symbol", [""])[0]
            if sym:
                where.append("symbol=?")
                args.append(sym[:40])
            term = q.get("q", [""])[0].strip()[:60]
            if term:
                where.append("(cast(run_id as text)=? or symbol like ? or params_json like ?)")
                args += [term, f"%{term}%", f"%{term}%"]
            try:
                pfil = json.loads(q.get("pf", ["[]"])[0])
            except ValueError:
                pfil = []
            allowed = set((json.loads(j["config_json"] or "{}").get("params") or {}).keys())
            for f in pfil if isinstance(pfil, list) else []:
                key = str(f.get("key", "")) if isinstance(f, dict) else ""
                if key not in allowed or not C.NAME_RE.match(key):
                    continue
                for bound, op in (("min", ">="), ("max", "<=")):
                    v = f.get(bound)
                    if v in (None, ""):
                        continue
                    try:
                        num = float(v)
                    except (TypeError, ValueError):
                        continue
                    where.append(f"cast(json_extract(params_json, ?) as real) {op} ?")
                    args += ['$."' + key + '"', num]
            wsql = " and ".join(where)
            total = con.execute(f"select count(*) from variants where {wsql}", args).fetchone()[0]
            rows = con.execute(f"select * from variants where {wsql} order by {sort} {order} nulls last, run_id limit ? offset ?",
                               args + [PAGE, off]).fetchall()
            syms = [r[0] for r in con.execute("select distinct symbol from variants where job_id=? order by symbol", (jid,))]
            return self.send_json({"ok": True, "total": total, "offset": off, "page": PAGE, "symbols": syms, "rows": [
                {**{k: r[k] for k in r.keys() if k not in ("params_json", "job_id")}, "params": json.loads(r["params_json"])}
                for r in rows]})
        if rest == ["request"] and method == "POST":
            try:
                body = self.read_json()
                kind, run_id = body.get("kind"), int(body.get("run_id"))
                offset = max(0, int(body.get("offset", 0)))
            except Exception:
                return self.fail(400, "yêu cầu không hợp lệ")
            if kind not in ("trades", "equity", "detail"):
                return self.fail(400, "kind phải là trades, equity hoặc detail")
            if j["status"] != "done":
                return self.fail(409, "job chưa hoàn tất")
            if not con.execute("select 1 from variants where job_id=? and run_id=?", (jid, run_id)).fetchone():
                return self.fail(404, "không có biến thể này")
            cur = con.execute("insert into requests(user_id,job_id,kind,payload_json,status,created) values(?,?,?,?,?,?)",
                              (uid, jid, kind, json.dumps({"run_id": run_id, "offset": offset, "limit": PAGE}), "pending", time.time()))
            con.commit()
            return self.send_json({"ok": True, "id": cur.lastrowid})
        return self.fail(404, "không tìm thấy")

    # ---------- API của data-server ----------
    def desk_api(self, method, path, q):
        con = db()
        try:
            touch_desk(con)
            parts = [p for p in path.split("/") if p][2:]  # bỏ "api","desk"
            if parts == ["poll"] and method == "GET":
                return self.desk_poll(con, q)
            if parts == ["active"] and method == "GET":
                rows = con.execute("select j.id, j.status, j.config_json, u.slug from jobs j join users u on u.id=j.user_id "
                                   "where j.status in ('dispatched','running') order by j.id").fetchall()
                return self.send_json({"ok": True, "jobs": [{"id": r["id"], "status": r["status"], "slug": r["slug"],
                                                             "config": json.loads(r["config_json"])} for r in rows]})
            if parts == ["alphas"] and method == "POST":
                body = self.read_json()
                u = con.execute("select id from users where slug=?", (str(body.get("slug", "")),)).fetchone()
                if not u:
                    return self.fail(404, "slug không tồn tại")
                items = body.get("alphas") if isinstance(body.get("alphas"), list) else []
                con.execute("delete from alphas where user_id=?", (u["id"],))
                for a in items:
                    if isinstance(a.get("name"), str) and C.NAME_RE.match(a["name"]) and isinstance(a.get("params"), dict):
                        m = a.get("meta") if isinstance(a.get("meta"), dict) else {}
                        meta = {"required_period": m["required_period"]} if m.get("required_period") in C.PERIODS else {}
                        con.execute("insert into alphas(user_id,name,params_json,meta_json,updated) values(?,?,?,?,?)",
                                    (u["id"], a["name"], json.dumps(a["params"]), json.dumps(meta), now_iso()))
                con.commit()
                return self.send_json({"ok": True, "count": len(items)})
            if len(parts) == 3 and parts[0] == "questions" and parts[1].isdigit() and parts[2] == "answer" and method == "POST":
                body = self.read_json()
                ok = bool(body.get("ok"))
                ans = str(body.get("answer", ""))[:8000]
                con.execute("update questions set status=?, answer=?, answered=? where id=? and status='dispatched'",
                            ("done" if ok else "failed", ans, now_iso(), int(parts[1])))
                con.commit()
                return self.send_json({"ok": True})
            if parts == ["import"] and method == "POST":
                return self.desk_import(con, self.read_json())
            if len(parts) == 3 and parts[0] == "jobs" and parts[1].isdigit():
                return self.desk_job(con, int(parts[1]), parts[2], self.read_json())
            if len(parts) == 3 and parts[0] == "submissions" and parts[1].isdigit() and parts[2] == "update":
                body = self.read_json()
                st = body.get("status")
                if st not in SUB_STATUSES:
                    return self.fail(400, "status không hợp lệ")
                res = body.get("result")
                res_json = json.dumps(res, ensure_ascii=False)[:60000] if res is not None else None
                con.execute("update submissions set status=?, message=?, result_json=coalesce(?, result_json), updated=? "
                            "where id=?", (st, str(body.get("message", ""))[:500], res_json, now_iso(), int(parts[1])))
                con.commit()
                return self.send_json({"ok": True})
            if len(parts) == 3 and parts[0] == "requests" and parts[1].isdigit() and parts[2] == "result":
                body = self.read_json()
                res = body if isinstance(body, dict) else {"ok": False, "error": "kết quả không hợp lệ"}
                con.execute("update requests set status='done', result_json=? where id=?",
                            (json.dumps(res, ensure_ascii=False), int(parts[1])))
                con.commit()
                return self.send_json({"ok": True})
            return self.fail(404, "không tìm thấy")
        finally:
            con.close()

    def desk_poll(self, con, q):
        wait = min(30.0, max(0.0, float(q.get("wait", ["25"])[0])))
        # Hàng job: desk báo loại nó đang nhận được (heavy = optimize, smoke = hàng nhanh riêng).
        if "accept" in q:
            lanes = {x for x in q.get("accept", [""])[0].split(",") if x in ("heavy", "smoke")}
        else:   # desk bản cũ
            lanes = {"heavy", "smoke"} if q.get("accept_jobs", ["1"])[0] == "1" else set()
        types = [t for t in C.DISPATCH_TYPES if ("smoke" if t == "smoke" else "heavy") in lanes]
        accept_sub = q.get("accept_sub", ["0"])[0] == "1"
        accept_q = q.get("accept_q", ["0"])[0] == "1"
        end = time.time() + wait
        while True:
            item = None
            with _dispatch_lock:
                r = con.execute("select r.id, r.kind, r.payload_json, r.job_id, u.slug from requests r "
                                "join users u on u.id=r.user_id where r.status='pending' order by r.id limit 1").fetchone()
                if r:
                    con.execute("update requests set status='dispatched' where id=?", (r["id"],))
                    con.commit()
                    item = {"type": "request", "request": {"id": r["id"], "slug": r["slug"], "kind": r["kind"],
                                                           "job_id": r["job_id"], "payload": json.loads(r["payload_json"])}}
                if not item and accept_q:
                    qr = con.execute("select * from questions where status='queued' order by id limit 1").fetchone()
                    if qr:
                        con.execute("update questions set status='dispatched', dispatched_at=? where id=?", (time.time(), qr["id"]))
                        con.commit()
                        item = {"type": "question", "question": self.question_payload(con, qr)}
                if not item and accept_sub:
                    sb = con.execute("select s.id, s.name, s.source, s.sha256, u.slug from submissions s "
                                     "join users u on u.id=s.user_id where s.status='queued' order by s.id limit 1").fetchone()
                    if sb:
                        # giao mã cho data-server rồi XÓA khỏi web
                        con.execute("update submissions set status='checking', source=NULL, "
                                    "message='Data-server đang kiểm tra', updated=? where id=?", (now_iso(), sb["id"]))
                        con.commit()
                        item = {"type": "submission", "submission": {"id": sb["id"], "slug": sb["slug"], "name": sb["name"],
                                                                     "source": sb["source"], "sha256": sb["sha256"]}}
                if not item and types:
                    marks = ",".join("?" * len(types))
                    j = con.execute("select j.id, j.config_json, u.slug from jobs j join users u on u.id=j.user_id "
                                    f"where j.status='queued' and j.type in ({marks}) "
                                    "order by (j.type='smoke') desc, j.id limit 1", types).fetchone()   # smoke được ưu tiên
                    if j:
                        con.execute("update jobs set status='dispatched', started=?, last_update=? where id=?",
                                    (now_iso(), time.time(), j["id"]))
                        con.commit()
                        item = {"type": "job", "job": {"id": j["id"], "slug": j["slug"], "config": json.loads(j["config_json"])}}
            touch_desk(con)
            if item or time.time() >= end:
                return self.send_json({"ok": True, "item": item})
            time.sleep(0.5)

    def desk_import(self, con, body):
        """Desk đăng ký một report offline (Dương/agent chạy ngoài web) cho một tài khoản. Web kiểm lại:
        không bao giờ nhận OS2 (todate > os1_end hoặc thiếu todate); tài khoản loại 1 không nhận report vượt is_end."""
        if not isinstance(body, dict):
            return self.fail(400, "JSON không hợp lệ")
        u = con.execute("select id, os1_access from users where slug=?", (str(body.get("slug", "")),)).fetchone()
        if not u:
            return self.fail(404, "slug không tồn tại")
        key, cfg = body.get("ext_key"), body.get("config")
        if not isinstance(key, str) or not key or len(key) > 400 or not isinstance(cfg, dict):
            return self.fail(400, "thiếu ext_key/config")
        td = str(cfg.get("todate", ""))
        limit = LIMITS["os1_end"] if u["os1_access"] else LIMITS["is_end"]
        if not C.DATE_RE.match(td) or td == "0" or td > limit:
            return self.send_json({"ok": False, "error": f"todate {td or '(trống)'} vượt {limit} hoặc không hợp lệ"}, 400)
        cfg["type"] = "offline"
        alpha = str(body.get("alpha", ""))[:120]
        source = str(body.get("source", "offline"))[:60]
        created = str(body.get("created") or now_iso())[:19]
        j = con.execute("select id, user_id from jobs where ext_key=?", (key,)).fetchone()
        if j and j["user_id"] != u["id"]:
            return self.fail(409, "ext_key thuộc tài khoản khác")
        if j:
            con.execute("update jobs set alpha=?, config_json=?, created=?, source=?, status='done', progress=100, "
                        "message='Report offline', last_update=? where id=?",
                        (alpha, json.dumps(cfg), created, source, time.time(), j["id"]))
            con.execute("delete from variants where job_id=?", (j["id"],))
            jid = j["id"]
        else:
            cur = con.execute("insert into jobs(user_id,type,alpha,config_json,status,progress,message,created,started,last_update,"
                              "ext_key,source) values(?,?,?,?,?,?,?,?,?,?,?,?)",
                              (u["id"], "offline", alpha, json.dumps(cfg), "done", 100, "Report offline", created, created,
                               time.time(), key, source))
            jid = cur.lastrowid
        con.commit()
        return self.send_json({"ok": True, "job_id": jid})

    def desk_job(self, con, jid, action, body):
        j = con.execute("select * from jobs where id=?", (jid,)).fetchone()
        if not j:
            return self.fail(404, "không có job")
        if action == "update":
            st = body.get("status")
            if st is None and j["status"] == "failed":
                # web từng đánh dấu mất theo dõi nhưng desk vẫn đang theo dõi run -> trả lại trạng thái đang chạy
                con.execute("update jobs set status='running', finished=NULL where id=?", (jid,))
            if st in ("running", "failed", "cancelled"):
                finished = now_iso() if st in ("failed", "cancelled") else None
                con.execute("update jobs set status=?, finished=coalesce(?, finished) where id=?", (st, finished, jid))
            if "progress" in body:
                p = float(body["progress"])
                con.execute("update jobs set progress_at=? where id=? and coalesce(progress,-1) != ?", (time.time(), jid, p))
                con.execute("update jobs set progress=? where id=?", (p, jid))
            if "message" in body:
                con.execute("update jobs set message=? where id=?", (str(body["message"])[:500], jid))
            con.execute("update jobs set last_update=? where id=?", (time.time(), jid))
            con.commit()
            cancel = bool(con.execute("select cancel from jobs where id=?", (jid,)).fetchone()[0])
            return self.send_json({"ok": True, "cancel": cancel})
        if action == "result":
            con.execute("update jobs set last_update=? where id=?", (time.time(), jid))
            for v in body.get("variants", []):
                stopped = 1 if v.get("stopped_out") else 0
                con.execute("insert or replace into variants(job_id,run_id,symbol,params_json,trades,net_profit,dd_pct,pf,winrate,"
                            "sharpe,tit,fitness,recovery,is_pass,recent_sharpe,stopped_out,rejected_orders) "
                            "values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (jid, int(v["run_id"]), v.get("symbol"), json.dumps(v.get("params", {})), v.get("trades"),
                             v.get("net_profit"), v.get("dd_pct"), v.get("pf"), v.get("winrate"), v.get("sharpe"), v.get("tit"),
                             v.get("fitness"), v.get("recovery"), 0 if stopped else (1 if v.get("is_pass") else 0),
                             v.get("recent_sharpe"), stopped, int(v.get("rejected_orders") or 0)))
            if body.get("final"):
                con.execute("update jobs set status='done', progress=100, finished=?, summary_json=?, message='Hoàn tất', "
                            "last_update=? where id=?", (now_iso(), json.dumps(body.get("summary", {})), time.time(), jid))
            con.commit()
            return self.send_json({"ok": True})
        return self.fail(404, "không tìm thấy")


def main():
    if not CFG.get("desk_token"):
        sys.exit("Thiếu desk_token. Chạy: python qc_admin.py init")
    init_db()
    threading.Thread(target=sweeper, daemon=True).start()
    srv = ThreadingHTTPServer((CFG["host"], int(CFG["port"])), Handler)
    srv.daemon_threads = True
    print(f"QuanTechCap web: http://{CFG['host']}:{CFG['port']}  (db: {DB_PATH})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
