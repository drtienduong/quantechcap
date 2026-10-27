"""QuanTechCap web - BẢN THỬ NGHIỆM (chạy cục bộ; về sau đặt trên VPS sau Caddy/TLS).

Vai trò:
- Trader đăng nhập (email + mật khẩu do Dương cấp), chọn alpha, cấu hình backtest/optimize bằng form, xem kết quả.
- Máy bàn (desk) GỌI RA đây: lấy job, báo tiến độ, đẩy kết quả tóm tắt, trả dữ liệu trade/equity theo yêu cầu.
  Máy chủ web KHÔNG có đường vào máy bàn và KHÔNG chứa data tick hay mã alpha.
- Chỉ dùng thư viện chuẩn của Python.

Chạy:  python qc_web.py        (cấu hình ở web_config.json, tạo bằng qc_admin.py init)
"""
import hashlib
import hmac
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

# Docker: đặt QC_DATA_DIR / QC_WEB_CONFIG trỏ vào volume; không đặt thì giữ như cũ (cạnh file này).
DATA_DIR = Path(os.environ.get("QC_DATA_DIR") or HERE / "data")
DB_PATH = DATA_DIR / "qc.sqlite"
CONFIG_PATH = Path(os.environ.get("QC_WEB_CONFIG") or HERE / "web_config.json")
STATIC = HERE / "static"
STATIC_FILES = {"/static/app.js": "application/javascript; charset=utf-8",
                "/static/style.css": "text/css; charset=utf-8"}
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
  recovery real, is_pass integer, recent_sharpe real, primary key(job_id, run_id));
create table if not exists requests(id integer primary key autoincrement, user_id integer not null, job_id integer not null,
  kind text, payload_json text, status text, result_json text, created real);
create table if not exists meta(k text primary key, v text);
"""


def init_db():
    con = db()
    con.executescript(SCHEMA)
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


def touch_desk(con):
    con.execute("insert into meta(k,v) values('desk_seen',?) on conflict(k) do update set v=excluded.v", (str(time.time()),))
    con.commit()


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
        s = con.execute("select s.token, s.csrf, s.expires, u.id, u.email, u.slug, u.is_admin "
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
        self.route("GET")

    def do_POST(self):
        self.route("POST")

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
                                       "desk_online": time.time() - seen < DESK_ONLINE_SEC, "desk_seen": seen,
                                       "limits": LIMITS, "fx25": C.FX25, "periods": C.PERIODS, "job_types": C.JOB_TYPES})
            if parts == ["alphas"]:
                rows = con.execute("select name, params_json, updated from alphas where user_id=? order by name", (uid,)).fetchall()
                return self.send_json({"ok": True, "alphas": [
                    {"name": r["name"], "params": json.loads(r["params_json"]), "updated": r["updated"]} for r in rows]})
            if parts == ["jobs"] and method == "GET":
                off = max(0, int(q.get("offset", ["0"])[0]))
                lim = min(100, max(1, int(q.get("limit", ["30"])[0])))
                rows = con.execute("select id,type,alpha,status,progress,message,created,finished,summary_json from jobs "
                                   "where user_id=? order by id desc limit ? offset ?", (uid, lim, off)).fetchall()
                total = con.execute("select count(*) from jobs where user_id=?", (uid,)).fetchone()[0]
                return self.send_json({"ok": True, "total": total, "jobs": [self.job_row(r) for r in rows]})
            if parts == ["jobs"] and method == "POST":
                return self.create_job(con, uid)
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
            return self.fail(400, "alpha không có trong danh sách của bạn (máy bàn chưa đồng bộ?)")
        clean, errs = C.validate_job(cfg, json.loads(a["params_json"]), LIMITS)
        if errs:
            return self.send_json({"ok": False, "error": "cấu hình không hợp lệ", "details": errs}, 400)
        cur = con.execute("insert into jobs(user_id,type,alpha,config_json,status,created,last_update) values(?,?,?,?,?,?,?)",
                          (uid, clean["type"], clean["alpha"], json.dumps(clean), "queued", now_iso(), time.time()))
        con.commit()
        self.send_json({"ok": True, "id": cur.lastrowid})

    def job_api(self, con, uid, jid, rest, method, q):
        j = con.execute("select * from jobs where id=? and user_id=?", (jid, uid)).fetchone()
        if not j:
            return self.fail(404, "không có job")
        if not rest and method == "GET":
            d = self.job_row(j)
            d["config"] = json.loads(j["config_json"])
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
                    "dd": "dd_pct", "run_id": "run_id", "recent": "recent_sharpe"}.get(q.get("sort", ["fitness"])[0], "fitness")
            order = "asc" if q.get("dir", ["desc"])[0] == "asc" else "desc"
            off = max(0, int(q.get("offset", ["0"])[0]))
            where = "job_id=?" + (" and is_pass=1" if q.get("pass", ["0"])[0] == "1" else "")
            total = con.execute(f"select count(*) from variants where {where}", (jid,)).fetchone()[0]
            rows = con.execute(f"select * from variants where {where} order by {sort} {order} nulls last, run_id limit ? offset ?",
                               (jid, PAGE, off)).fetchall()
            return self.send_json({"ok": True, "total": total, "offset": off, "page": PAGE, "rows": [
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

    # ---------- API của máy bàn ----------
    def desk_api(self, method, path, q):
        con = db()
        try:
            touch_desk(con)
            parts = [p for p in path.split("/") if p][2:]  # bỏ "api","desk"
            if parts == ["poll"] and method == "GET":
                return self.desk_poll(con, q)
            if parts == ["alphas"] and method == "POST":
                body = self.read_json()
                u = con.execute("select id from users where slug=?", (str(body.get("slug", "")),)).fetchone()
                if not u:
                    return self.fail(404, "slug không tồn tại")
                items = body.get("alphas") if isinstance(body.get("alphas"), list) else []
                con.execute("delete from alphas where user_id=?", (u["id"],))
                for a in items:
                    if isinstance(a.get("name"), str) and C.NAME_RE.match(a["name"]) and isinstance(a.get("params"), dict):
                        con.execute("insert into alphas(user_id,name,params_json,updated) values(?,?,?,?)",
                                    (u["id"], a["name"], json.dumps(a["params"]), now_iso()))
                con.commit()
                return self.send_json({"ok": True, "count": len(items)})
            if len(parts) == 3 and parts[0] == "jobs" and parts[1].isdigit():
                return self.desk_job(con, int(parts[1]), parts[2], self.read_json())
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
        accept = q.get("accept_jobs", ["1"])[0] == "1"
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
                elif accept:
                    j = con.execute("select j.id, j.config_json, u.slug from jobs j join users u on u.id=j.user_id "
                                    "where j.status='queued' order by j.id limit 1").fetchone()
                    if j:
                        con.execute("update jobs set status='dispatched', started=?, last_update=? where id=?",
                                    (now_iso(), time.time(), j["id"]))
                        con.commit()
                        item = {"type": "job", "job": {"id": j["id"], "slug": j["slug"], "config": json.loads(j["config_json"])}}
            touch_desk(con)
            if item or time.time() >= end:
                return self.send_json({"ok": True, "item": item})
            time.sleep(0.5)

    def desk_job(self, con, jid, action, body):
        j = con.execute("select * from jobs where id=?", (jid,)).fetchone()
        if not j:
            return self.fail(404, "không có job")
        if action == "update":
            st = body.get("status")
            if st in ("running", "failed", "cancelled"):
                finished = now_iso() if st in ("failed", "cancelled") else None
                con.execute("update jobs set status=?, finished=coalesce(?, finished) where id=?", (st, finished, jid))
            if "progress" in body:
                con.execute("update jobs set progress=? where id=?", (float(body["progress"]), jid))
            if "message" in body:
                con.execute("update jobs set message=? where id=?", (str(body["message"])[:500], jid))
            con.execute("update jobs set last_update=? where id=?", (time.time(), jid))
            con.commit()
            cancel = bool(con.execute("select cancel from jobs where id=?", (jid,)).fetchone()[0])
            return self.send_json({"ok": True, "cancel": cancel})
        if action == "result":
            for v in body.get("variants", []):
                con.execute("insert or replace into variants(job_id,run_id,symbol,params_json,trades,net_profit,dd_pct,pf,winrate,"
                            "sharpe,tit,fitness,recovery,is_pass,recent_sharpe) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (jid, int(v["run_id"]), v.get("symbol"), json.dumps(v.get("params", {})), v.get("trades"),
                             v.get("net_profit"), v.get("dd_pct"), v.get("pf"), v.get("winrate"), v.get("sharpe"), v.get("tit"),
                             v.get("fitness"), v.get("recovery"), 1 if v.get("is_pass") else 0, v.get("recent_sharpe")))
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
    srv = ThreadingHTTPServer((CFG["host"], int(CFG["port"])), Handler)
    srv.daemon_threads = True
    print(f"QuanTechCap web: http://{CFG['host']}:{CFG['port']}  (db: {DB_PATH})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
