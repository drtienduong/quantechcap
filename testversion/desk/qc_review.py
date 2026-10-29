"""QuanTechCap - duyệt alpha trader đã nộp (chạy trên data-server, bởi Dương hoặc agent platform).

  python qc_review.py list [--all]           bản nộp chờ duyệt (--all: cả các trạng thái khác, 30 bản mới nhất)
  python qc_review.py show ID                thông tin + kết quả kiểm tra + đường dẫn mã nguồn
  python qc_review.py approve ID [ghi chú]   kiểm tra lại (sha256 + tĩnh + biên dịch), cài vào thư mục alpha, báo web
  python qc_review.py reject ID lý do        từ chối, báo lý do cho trader trên web

Bản nộp nằm ở <thư mục trader>/submissions/<ID 6 số>_<tên>/ (source.rs + meta.json). Không sửa source.rs bằng tay.
"""
import hashlib
import json
import sys
import time

import qc_desk as D  # dùng chung cấu hình, api() và install_alpha() của desk


def find(sid):
    sid = int(sid)
    for slug, t in D.TRADERS.items():
        for d in (t["dir"] / "submissions").glob(f"{sid:06d}_*"):
            if (d / "meta.json").exists():
                return slug, t, d, json.loads((d / "meta.json").read_text(encoding="utf-8"))
    sys.exit(f"Không tìm thấy bản nộp {sid}")


def all_meta():
    out = []
    for slug, t in D.TRADERS.items():
        for d in (t["dir"] / "submissions").glob("*_*"):
            m = d / "meta.json"
            if m.exists():
                try:
                    out.append((d, json.loads(m.read_text(encoding="utf-8"))))
                except ValueError:
                    pass
    return sorted(out, key=lambda x: x[1].get("id", 0))


def save(d, meta):
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_list(show_all):
    rows = all_meta()
    if not show_all:
        rows = [r for r in rows if r[1].get("status") == "pending_review"]
    else:
        rows = rows[-30:]
    if not rows:
        print("Không có bản nộp nào" + ("" if show_all else " chờ duyệt") + ".")
    for d, m in rows:
        print(f"#{m['id']:<5} {m.get('status',''):15} {m['slug']:16} {m['name']:50} {m.get('size',0):>7} B  {m.get('received','')}")


def cmd_show(sid):
    slug, t, d, m = find(sid)
    print(f"Bản nộp #{m['id']}  trader={slug}  tên={m['name']}  trạng thái={m.get('status')}")
    print(f"Mã nguồn: {d / 'source.rs'}")
    print(f"sha256: {m['sha256']}  ({m.get('size')} byte)  nhận lúc {m.get('received')}")
    c = m.get("check", {})
    print(f"Kiểm tra tĩnh: {'đạt' if c.get('static_ok') else 'KHÔNG đạt'}; biên dịch: {c.get('compile_ok')}")
    for e in c.get("static_errors", []) + c.get("compile_errors", []):
        print("  -", e)
    print("Tham số:", ", ".join(c.get("params", [])))
    for k in ("approved_by", "approved_at", "review_note", "rejected_reason"):
        if m.get(k):
            print(f"{k}: {m[k]}")


def cmd_approve(sid, note):
    slug, t, d, m = find(sid)
    if m.get("status") not in ("pending_review", "review_rejected"):
        sys.exit(f"Bản nộp đang ở trạng thái {m.get('status')}, không duyệt được.")
    raw = (d / "source.rs").read_bytes()
    if hashlib.sha256(raw).hexdigest() != m["sha256"]:
        sys.exit("source.rs đã bị thay đổi so với lúc nộp (sha256 khác). Dừng.")
    src = raw.decode("utf-8")
    res = D.AC.check(src, D.RUNNER_TEMPLATE)       # kiểm lại: engine/bộ luật có thể đã đổi từ lúc nộp
    if not res["static_ok"] or res["compile_ok"] is False:
        print("Kiểm tra lại KHÔNG đạt:")
        for e in res["static_errors"] + res["compile_errors"]:
            print("  -", e)
        sys.exit(1)
    dest = D.install_alpha(t, m["name"], src)
    m.update(status="approved", approved_by="review", approved_at=time.strftime("%Y-%m-%d %H:%M:%S"), review_note=note, check=res)
    save(d, m)
    msg = "Đã duyệt. Alpha đã có trong danh sách." + (f" Ghi chú: {note}" if note else "")
    D.sub_update(m["id"], "approved", msg)
    try:
        D.sync_catalog(force=True)
    except Exception as e:  # noqa: BLE001
        print("Cảnh báo: đồng bộ danh sách alpha lỗi:", repr(e), "(desk sẽ tự đồng bộ trong vòng 1 phút)")
    print(f"Đã duyệt #{m['id']} -> {dest}")


def cmd_reject(sid, reason):
    slug, t, d, m = find(sid)
    if m.get("status") == "approved":
        sys.exit("Bản nộp đã được duyệt và cài; muốn gỡ thì xóa file trong thư mục alpha của trader.")
    m.update(status="review_rejected", rejected_reason=reason, rejected_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    save(d, m)
    D.sub_update(m["id"], "review_rejected", f"Không được duyệt: {reason}"[:480])
    print(f"Đã từ chối #{m['id']}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["list"]:
        cmd_list("--all" in a)
    elif a[:1] == ["show"] and len(a) == 2:
        cmd_show(a[1])
    elif a[:1] == ["approve"] and len(a) >= 2:
        cmd_approve(a[1], " ".join(a[2:]))
    elif a[:1] == ["reject"] and len(a) >= 3:
        cmd_reject(a[1], " ".join(a[2:]))
    else:
        print(__doc__)
