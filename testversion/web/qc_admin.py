"""QuanTechCap - quản trị (chỉ Dương chạy trên máy chủ web). Không có đăng ký tự do.

  python qc_admin.py init [--quiet]                 tạo web_config.json (có desk_token ngẫu nhiên); --quiet: không in token
  python qc_admin.py show-token                     in desk_token (để dán vào desk_config.json)
  python qc_admin.py add-user EMAIL SLUG [--admin]  tạo tài khoản, in mật khẩu ngẫu nhiên MỘT LẦN
  python qc_admin.py passwd EMAIL                   đặt lại mật khẩu ngẫu nhiên
  python qc_admin.py list-users
"""
import json
import secrets
import sys
from pathlib import Path

import qc_web as W  # noqa: E402


def newpw():
    return secrets.token_urlsafe(12)


def cmd_init(quiet=False):
    """quiet=True (dùng khi container khởi động): không in token ra log; lấy bằng `show-token`."""
    if W.CONFIG_PATH.exists():
        if not quiet:
            print("web_config.json đã có, giữ nguyên:", W.CONFIG_PATH)
        return
    W.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = dict(W.DEFAULTS, desk_token=secrets.token_urlsafe(32))
    W.CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    W.init_db()
    print("Đã tạo", W.CONFIG_PATH)
    if quiet:
        print("desk_token đã tạo (không in ra log). Xem bằng: python qc_admin.py show-token")
    else:
        print("desk_token (dán vào desk_config.json của data-server):", cfg["desk_token"])


def cmd_show_token():
    if not W.CONFIG_PATH.exists():
        sys.exit("Chưa có web_config.json. Chạy: python qc_admin.py init")
    print(json.loads(W.CONFIG_PATH.read_text(encoding="utf-8")).get("desk_token", ""))


def cmd_add_user(email, slug, admin):
    W.init_db()
    email = email.strip().lower()
    pw = newpw()
    con = W.db()
    try:
        con.execute("insert into users(email,pw,slug,is_admin,created) values(?,?,?,?,?)",
                    (email, W.hash_pw(pw), slug, 1 if admin else 0, W.now_iso()))
        con.commit()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Không tạo được (email/slug đã tồn tại?): {e}")
    finally:
        con.close()
    print(f"Đã tạo tài khoản {email} (slug {slug}). Mật khẩu (chỉ hiện một lần): {pw}")


def cmd_passwd(email):
    pw = newpw()
    con = W.db()
    n = con.execute("update users set pw=? where email=?", (W.hash_pw(pw), email.strip().lower())).rowcount
    con.execute("delete from sessions where user_id in (select id from users where email=?)", (email.strip().lower(),))
    con.commit()
    con.close()
    print("Không có tài khoản" if not n else f"Mật khẩu mới (chỉ hiện một lần): {pw}")


def cmd_list():
    W.init_db()
    con = W.db()
    for r in con.execute("select email, slug, is_admin, created from users order by id"):
        print(f"{r['email']:40} slug={r['slug']:20} admin={r['is_admin']} created={r['created']}")
    con.close()


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["init"]:
        cmd_init(quiet="--quiet" in a)
    elif a[:1] == ["show-token"]:
        cmd_show_token()
    elif a[:1] == ["add-user"] and len(a) >= 3:
        cmd_add_user(a[1], a[2], "--admin" in a)
    elif a[:1] == ["passwd"] and len(a) == 2:
        cmd_passwd(a[1])
    elif a[:1] == ["list-users"]:
        cmd_list()
    else:
        print(__doc__)
