"""Trang hướng dẫn bộ khung alpha cho AI (công khai, không cần đăng nhập).

Một nguồn duy nhất: các file trong static/kit/. Sửa file ở đó (rồi deploy) là cả 3 dạng đổi theo:
- /guide_ai      HTML dựng sẵn phía server (không cần JavaScript) cho người và AI đọc.
- /guide_ai.md   văn bản thuần (markdown) cho AI đọc trực tiếp.
- /kit/quantechcap_alpha_kit.zip   bản offline cho AI không đọc được web.
Chỉ dùng thư viện chuẩn. Mọi nội dung được escape trước khi đưa vào HTML.
"""
import hashlib
import html
import re
from datetime import datetime, timezone
from pathlib import Path

# Thứ tự hiển thị trên trang. Ghi chú ngắn cho từng file.
KIT_ORDER = [
    ("HUONG_DAN.md", "Hướng dẫn cho trader (tiếng Việt)"),
    ("AI_GUIDE.md", "Hướng dẫn cho AI (bắt buộc đọc)"),
    ("qc_alpha_api.rs", "API engine cung cấp (đọc trước, không nộp file này)"),
    ("alpha_template.rs", "Khung để bắt đầu"),
    ("example_ma_cross_bar.rs", "Ví dụ: tín hiệu theo nến, khớp lệnh theo tick, SL/TP"),
    ("example_tick_range_breakout.rs", "Ví dụ: chỉ dùng tick, giờ trong ngày, Open/Modify/Close, trailing stop"),
    ("reserved_names.txt", "Tên dành riêng của engine"),
    ("local_check.rs", "Kiểm tra kiểu cục bộ bằng rustc (tùy chọn)"),
]
LANG = {".rs": "rust", ".txt": "text", ".md": "markdown"}


def _files(kit_dir: Path):
    return [(n, note, (kit_dir / n).read_text(encoding="utf-8")) for n, note in KIT_ORDER if (kit_dir / n).is_file()]


def version(kit_dir: Path):
    """(ngày cập nhật mới nhất UTC, mã 8 ký tự theo nội dung). Nội dung đổi thì mã đổi."""
    hsh = hashlib.sha256()
    newest = 0.0
    for n, _note in KIT_ORDER:
        p = kit_dir / n
        if p.is_file():
            hsh.update(n.encode() + b"\0" + p.read_bytes() + b"\0")
            newest = max(newest, p.stat().st_mtime)
    day = datetime.fromtimestamp(newest, timezone.utc).strftime("%Y-%m-%d") if newest else "?"
    return day, hsh.hexdigest()[:8]


def _intro(base, day, ver, zip_name):
    page, raw, zipu = f"{base}/guide_ai", f"{base}/guide_ai.md", f"{base}/kit/{zip_name}"
    return f"""# Bộ khung alpha QuanTechCap cho AI

Phiên bản: **{day} · {ver}**. Trang này luôn là bản mới nhất: {page}

## Cách dùng (cho trader)

1. Mở một cuộc chat MỚI với AI có khả năng đọc web (Claude, ChatGPT...). Dán câu sau, kèm ý tưởng giao dịch của bạn:
   `Đọc toàn bộ hướng dẫn tại {raw} trước khi viết. Viết MỘT file alpha .rs đúng theo hướng dẫn đó. Hậu tố tên của tôi là <hậu tố>. Ý tưởng: ...`
2. Mỗi lần bắt đầu alpha mới, yêu cầu AI đọc lại link để dùng bộ khung mới nhất. Kiểm tra AI nêu đúng số phiên bản ở trên.
3. AI không đọc được link (một số AI hoặc gói miễn phí không truy cập web): tải [bản offline (.zip)]({zipu}) hoặc [bản văn bản (.md)]({raw}), đính kèm toàn bộ vào chat.
4. Nộp file `.rs` ở trang "Nộp alpha" trên web (cần đăng nhập). Hậu tố tên của bạn hiện trên trang đó.

## For the AI assistant

This page is the complete and current QuanTechCap alpha kit, version {day} {ver}. Everything you need is below:
the trader guide, `AI_GUIDE.md` (rules you must follow), the engine API, a template and two examples.
Read all of it before writing code, follow `AI_GUIDE.md` strictly, and ignore any older copy of the kit.
Tell the trader which kit version you used. Plain-text version of this page: {raw}
"""


def bundle_md(kit_dir: Path, base: str, zip_name: str) -> str:
    day, ver = version(kit_dir)
    parts = [_intro(base, day, ver, zip_name)]
    for n, note, text in _files(kit_dir):
        parts.append(f"\n---\n\n# File: {n}\n\n_{note}_\n")
        if n.endswith(".md"):
            parts.append(_demote(text))
        else:
            fence = "````" if "```" in text else "```"
            parts.append(f"{fence}{LANG.get(Path(n).suffix, '')}\n{text.rstrip()}\n{fence}\n")
    return "\n".join(parts)


def _demote(md: str) -> str:
    """Hạ cấp tiêu đề của file .md con một bậc (# -> ##) để trang có một tiêu đề cấp 1 cho mỗi file."""
    out, fence = [], False
    for ln in md.splitlines():
        if ln.lstrip().startswith("```"):
            fence = not fence
        if not fence and re.match(r"#{1,5} ", ln):
            ln = "#" + ln
        out.append(ln)
    return "\n".join(out) + "\n"


# ---------------- markdown -> HTML (tập con đủ cho bộ khung) ----------------
_INLINE = re.compile(r"(`+)(.+?)\1|\*\*(.+?)\*\*|\[([^\]]+)\]\((https?://[^)\s]+|/[^)\s]*)\)|(https?://[^\s<)`]*[^\s<)`.,;:!?])")


def _inline(s: str) -> str:
    out, pos = [], 0
    for m in _INLINE.finditer(s):
        out.append(html.escape(s[pos:m.start()]))
        if m.group(2) is not None:
            out.append("<code>" + html.escape(m.group(2)) + "</code>")
        elif m.group(3) is not None:
            out.append("<b>" + _inline(m.group(3)) + "</b>")
        elif m.group(4) is not None:
            out.append(f'<a href="{html.escape(m.group(5))}">{html.escape(m.group(4))}</a>')
        else:
            u = html.escape(m.group(6))
            out.append(f'<a href="{u}">{u}</a>')
        pos = m.end()
    out.append(html.escape(s[pos:]))
    return "".join(out)


def _slug(text, used):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "muc"
    base, i = s, 2
    while s in used:
        s, i = f"{base}-{i}", i + 1
    used.add(s)
    return s


def md_to_html(md: str):
    """Trả (html, mục lục [(cấp, id, chữ)]). Hỗ trợ: tiêu đề, đoạn, danh sách lồng (- và 1.), khối mã, ---,
    `mã`, **đậm**, link. Không chấp nhận HTML thô (mọi thứ đều được escape)."""
    out, toc, used = [], [], set()
    para = []
    stack = []            # [(thụt lề, "ul"/"ol")]
    code = None           # (fence, lang, lines)

    def flush_para():
        if para:
            out.append("<p>" + _inline(" ".join(x.strip() for x in para)) + "</p>")
            para.clear()

    def close_lists(to_indent=-1):
        while stack and stack[-1][0] > to_indent:
            out.append(f"</li></{stack.pop()[1]}>")

    for ln in md.splitlines():
        if code is not None:
            if ln.strip() == code[0]:
                body = html.escape("\n".join(code[2]))
                cls = f' class="lang-{code[1]}"' if code[1] else ""
                out.append(f"<pre><code{cls}>{body}</code></pre>")
                code = None
            else:
                code[2].append(ln[code[3]:] if ln[:code[3]].strip() == "" else ln)
            continue
        m = re.match(r"^(\s*)(`{3,})(\w*)\s*$", ln)
        if m:
            flush_para()
            if not stack or len(m.group(1)) == 0:
                close_lists()
            code = (m.group(2), m.group(3), [], len(m.group(1)))
            continue
        if not ln.strip():
            flush_para()
            continue
        m = re.match(r"^(#{1,6}) (.*)$", ln)
        if m:
            flush_para(); close_lists()
            lvl, text = len(m.group(1)), m.group(2).strip()
            hid = _slug(text, used)
            if lvl <= 2:
                toc.append((lvl, hid, text))
            out.append(f'<h{lvl} id="{hid}">{_inline(text)}</h{lvl}>')
            continue
        if re.match(r"^-{3,}\s*$", ln):
            flush_para(); close_lists()
            out.append("<hr>")
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.) (.*)$", ln)
        if m:
            flush_para()
            ind, kind = len(m.group(1)), ("ol" if m.group(2)[0].isdigit() else "ul")
            close_lists(ind)
            if stack and stack[-1][0] == ind and stack[-1][1] == kind:
                out.append("</li><li>")
            else:
                if stack and stack[-1][0] == ind:          # cùng cấp nhưng đổi loại danh sách
                    out.append(f"</li></{stack.pop()[1]}>")
                stack.append((ind, kind))
                out.append(f"<{kind}><li>")
            out.append(_inline(m.group(3)))
            continue
        if stack and ln[:1].isspace():                  # dòng nối tiếp của mục danh sách
            out.append(" " + _inline(ln.strip()))
            continue
        close_lists()
        m = re.match(r"^_(.+)_$", ln.strip())
        if m:
            out.append('<p class="note">' + _inline(m.group(1)) + "</p>")
            continue
        para.append(ln)
    flush_para()
    if code is not None:
        out.append("<pre><code>" + html.escape("\n".join(code[2])) + "</code></pre>")
    close_lists()
    return "\n".join(out), toc


def page_html(kit_dir: Path, base: str, zip_name: str) -> str:
    md = bundle_md(kit_dir, base, zip_name)
    body, toc = md_to_html(md)
    day, ver = version(kit_dir)
    nav = "".join(f'<li class="l{lvl}"><a href="#{hid}">{html.escape(t)}</a></li>' for lvl, hid, t in toc)
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bộ khung alpha QuanTechCap cho AI ({day} {ver})</title>
<meta name="description" content="QuanTechCap alpha kit: complete guide, API, template and examples for writing an alpha. Version {day} {ver}.">
<link rel="alternate" type="text/markdown" href="/guide_ai.md">
<link rel="stylesheet" href="/static/guide.css"></head>
<body><header><a class="brand" href="/">QuanTechCap</a><span class="sp"></span>
<a href="/guide_ai.md">Bản văn bản (.md)</a><a href="/kit/{zip_name}">Tải bản offline (.zip)</a></header>
<div class="wrap"><nav class="toc"><b>Mục lục</b><ul>{nav}</ul></nav>
<main class="doc">
{body}
</main></div></body></html>
"""
