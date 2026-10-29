"""QuanTechCap - kiểm tra mã nguồn alpha do trader nộp (chạy trên data-server).

Hai lớp:
  1. static_check(src): phân tích token mã Rust theo DANH SÁCH CHO PHÉP. Alpha chỉ được dùng API engine
     (Tick, Bar, AccountView, OrderSide, Signal, AlphaInput, ParameterSet, Alpha, HashMap, HashSet) và một
     phần nhỏ của std (tính toán, cấu trúc dữ liệu). Cấm: file, mạng, tiến trình, biến môi trường, unsafe,
     extern, static, macro ngoài danh sách, thuộc tính ngoài danh sách, tên nội bộ của engine.
  2. compile_check(src): biên dịch thử (chỉ kiểm kiểu, --emit=metadata) trong khung engine thật.
     Chỉ trả về lỗi nằm trong file alpha; lỗi phía engine chỉ trả thông điệp, không trả dòng mã engine.

Lớp 1 phải chạy và đạt TRƯỚC lớp 2 (không đưa mã chưa kiểm vào rustc).
Không có công cụ tĩnh nào chắn tuyệt đối; lớp chặn cuối là tài khoản Windows quyền thấp + data-is.

Dùng tay:  python qc_alpha_check.py <alpha.rs> [--compile] [--template <runner_template.rs>]
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

DEFAULT_TEMPLATE = Path(r"C:\Users\zicza\Documents\rust-quant\backtest-optimzie-rust-tool\src\runner_template.rs")

MAX_BYTES = 200_000
MAX_LINES = 5_000
MAX_PARAMS = 64
PERIODS = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
REQUIRED_PERIOD_RE = re.compile(r'const\s+REQUIRED_PERIOD\s*:\s*&\s*(?:\'static\s+)?str\s*=\s*"([^"]*)"\s*;')


def required_period(src):
    """Khung bắt buộc alpha tự khai báo (const REQUIRED_PERIOD: &str = "M5";), None nếu không khai báo."""
    found = REQUIRED_PERIOD_RE.findall(strip_comments(src))
    return found[0] if len(found) == 1 and found[0] in PERIODS else None
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,80}$")

API_NAMES = {"Tick", "Bar", "AccountView", "OrderSide", "Signal", "AlphaInput", "ParameterSet", "Alpha", "HashMap", "HashSet"}

# Từ khóa cấm dùng (dưới dạng identifier)
FORBIDDEN_KEYWORDS = {
    "unsafe": "không được dùng unsafe",
    "extern": "không được dùng extern (FFI, extern crate)",
    "static": "không được dùng static (biến toàn cục dùng chung giữa các biến thể chạy song song); dùng trường trong struct",
    "crate": "không được dùng crate:: (truy cập nội bộ engine)",
    "super": "không được dùng super:: (truy cập nội bộ engine)",
    "mod": "không được khai báo module con",
    "async": "không được dùng async",
    "await": "không được dùng await",
    "asm": "không được dùng asm",
    "macro_rules": "không được tự định nghĩa macro",
    "global_asm": "không được dùng asm",
}

ALLOWED_MACROS = {"vec", "format", "matches", "assert", "assert_eq", "assert_ne", "debug_assert", "debug_assert_eq",
                  "debug_assert_ne", "panic", "unreachable"}

ALLOWED_ATTRS = {"derive", "allow", "inline", "must_use", "cold", "rustfmt"}
ALLOWED_DERIVES = {"Clone", "Copy", "Debug", "PartialEq", "Eq", "PartialOrd", "Ord", "Hash", "Default"}

# Module std/core được phép (đoạn ngay sau std::)
ALLOWED_STD = {"collections", "cmp", "f32", "f64", "i8", "i16", "i32", "i64", "i128", "isize", "u8", "u16", "u32",
               "u64", "u128", "usize", "fmt", "ops", "iter", "convert", "option", "result", "string", "vec", "boxed",
               "num", "mem", "hash", "marker", "default", "clone", "borrow", "char", "str", "slice", "array", "rc",
               "cell", "sync"}
STD_ROOTS = {"std", "core", "alloc"}

RUST_KEYWORDS = {"as", "break", "const", "continue", "else", "enum", "false", "fn", "for", "if", "impl", "in", "let",
                 "loop", "match", "move", "mut", "pub", "ref", "return", "self", "Self", "struct", "trait", "true",
                 "type", "use", "where", "while", "dyn", "yield", "box"} | set(FORBIDDEN_KEYWORDS)

BIDI = {"\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069", "\u200e", "\u200f"}


class Tok:
    __slots__ = ("kind", "val", "line")

    def __init__(self, kind, val, line):
        self.kind, self.val, self.line = kind, val, line

    def __repr__(self):
        return f"{self.kind}:{self.val}@{self.line}"


class TokError(Exception):
    pass


PUNCT3 = ("..=", "...", "<<=", ">>=")
PUNCT2 = ("::", "->", "=>", "==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/=", "%=", "^=", "&=", "|=",
          "<<", ">>", "..")


def tokenize(src):
    """Tokenizer Rust đủ dùng cho kiểm tra: bỏ comment, gom chuỗi/char/số, phân biệt lifetime với char."""
    toks = []
    i, n, line = 0, len(src), 1

    def add(kind, val):
        toks.append(Tok(kind, val, line))

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        # comment
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if src.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif src.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    if src[i] == "\n":
                        line += 1
                    i += 1
            if depth:
                raise TokError("comment /* không đóng")
            continue
        # raw string / byte string / c string: r"..", r#".."#, br"..", b"..", c"..", cr".."
        m = re.match(r'(b|c)?r(#*)"', src[i:i + 300])
        if m:
            hashes = m.group(2)
            end = '"' + hashes
            j = src.find(end, i + m.end())
            if j < 0:
                raise TokError("chuỗi raw không đóng")
            line += src.count("\n", i, j)
            add("str", src[i + m.end():j])
            i = j + len(end)
            continue
        if c == '"' or (c in "bc" and i + 1 < n and src[i + 1] == '"'):
            j = i + (2 if c != '"' else 1)
            buf = []
            while j < n and src[j] != '"':
                if src[j] == "\\":
                    buf.append(src[j:j + 2])
                    j += 2
                    continue
                if src[j] == "\n":
                    line += 1
                buf.append(src[j])
                j += 1
            if j >= n:
                raise TokError("chuỗi không đóng")
            add("str", "".join(buf))
            i = j + 1
            continue
        # char literal / byte char / lifetime
        if c == "'" or (c == "b" and i + 1 < n and src[i + 1] == "'"):
            k = i + (2 if c == "b" else 1)
            m = re.match(r"(\\(x[0-9A-Fa-f]{2}|u\{[0-9A-Fa-f]{1,6}\}|[nrt\\0'\"])|[^\\'\n])'", src[k:k + 14])
            if m:
                add("char", m.group(1))
                i = k + m.end()
                continue
            if c == "'":
                m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", src[i + 1:i + 100])
                if m:
                    add("lifetime", m.group(0))
                    i += 1 + m.end()
                    continue
            raise TokError(f"ký tự lạ ở dòng {line}")
        # raw identifier
        if src.startswith("r#", i):
            m = re.match(r"r#([A-Za-z_][A-Za-z0-9_]*)", src[i:i + 120])
            if m:
                add("ident", m.group(1))
                i += m.end()
                continue
        # identifier (chỉ ASCII)
        if c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", src[i:i + 200])
            if not m or (i + m.end() < n and (src[i + m.end()].isalnum() and not src[i + m.end()].isascii())):
                raise TokError(f"tên (identifier) chỉ được dùng ký tự ASCII (dòng {line})")
            add("ident", m.group(0))
            i += m.end()
            continue
        if c.isdigit():
            m = re.match(r"0[xob][0-9A-Fa-f_]+([iu](8|16|32|64|128|size))?|[0-9][0-9_]*(\.[0-9][0-9_]*)?([eE][+-]?[0-9_]+)?"
                         r"(f32|f64|[iu](8|16|32|64|128|size))?", src[i:i + 100])
            val = m.group(0)
            add("num", val)
            i += len(val)
            continue
        if not c.isascii():
            raise TokError(f"ký tự không phải ASCII ngoài comment/chuỗi (dòng {line}): U+{ord(c):04X}")
        for p in PUNCT3 + PUNCT2:
            if src.startswith(p, i):
                add("punct", p)
                i += len(p)
                break
        else:
            add("punct", c)
            i += 1
    return toks


def engine_reserved_names(template_text):
    """Tên top-level của engine (fn/struct/enum/const/...) + tên import ở đầu file, trừ API công khai."""
    names = set()
    for m in re.finditer(r'^(?:pub(?:\([^)]*\))?\s+)?(?:unsafe\s+)?(?:extern\s+"[^"]*"\s+)?'
                         r"(?:fn|struct|enum|const|static|type|trait|mod|union)\s+([A-Za-z_]\w*)",
                         template_text, re.M):
        names.add(m.group(1))
    head = template_text.split("};", 1)[0] if template_text.lstrip().startswith("use ") else ""
    for m in re.finditer(r"[A-Za-z_]\w*", head):
        names.add(m.group(0))
    names -= {"use", "std", "self"}
    names -= API_NAMES
    names -= {"collections", "alpha_impl"}
    return names


def _split_group(toks, start):
    """toks[start] là '{'. Trả về (danh sách token đầu mỗi phần tử cấp 1, chỉ số sau '}')."""
    depth, firsts, expect = 0, [], True
    j = start
    while j < len(toks):
        t = toks[j]
        if t.val == "{":
            depth += 1
            if depth == 1:
                expect = True
                j += 1
                continue
        elif t.val == "}":
            depth -= 1
            if depth == 0:
                return firsts, j + 1
        elif t.val == "," and depth == 1:
            expect = True
            j += 1
            continue
        if depth == 1 and expect:
            firsts.append(t)
            expect = False
        j += 1
    return firsts, j


def strip_comments(src):
    """Bỏ comment // và /* */ (giữ nguyên chuỗi)."""
    out, i, n = [], 0, len(src)
    NL = chr(10)
    while i < n:
        if src.startswith("//", i):
            j = src.find(NL, i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if src.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif src.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    if src[i] == NL:
                        out.append(NL)
                    i += 1
            continue
        boundary = i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")
        m = re.match(r'(b|c)?r(#*)"', src[i:i + 300]) if src[i] in "bcr" and boundary else None
        if m:
            end = '"' + m.group(2)
            j = src.find(end, i + m.end())
            j = n if j < 0 else j + len(end)
            out.append(src[i:j])
            i = j
            continue
        if src[i] == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
            continue
        if src[i] == "'":
            m = re.match(r"'(\\(x[0-9A-Fa-f]{2}|u\{[0-9A-Fa-f]{1,6}\}|[nrt\\0'\"])|[^\\'\n])'", src[i:i + 14])
            if m:
                out.append(m.group(0))
                i += m.end()
                continue
        out.append(src[i])
        i += 1
    return "".join(out)


def static_check(src, reserved):
    """Trả về (errors, info). errors: danh sách chuỗi 'dòng N: lý do'."""
    errors, info = [], {"params": [], "name": None, "required_period": None}

    def err(line, msg):
        if len(errors) < 50:
            errors.append(f"dòng {line}: {msg}" if line else msg)

    if isinstance(src, bytes):
        try:
            src = src.decode("utf-8")
        except UnicodeDecodeError:
            return ["file phải là UTF-8"], info
    if len(src.encode("utf-8")) > MAX_BYTES:
        return [f"file quá lớn (tối đa {MAX_BYTES // 1000} KB)"], info
    if src.count("\n") > MAX_LINES:
        return [f"file quá dài (tối đa {MAX_LINES} dòng)"], info
    if "\x00" in src:
        return ["file chứa ký tự NUL"], info
    for lno, ln in enumerate(src.splitlines(), 1):
        if any(ch in BIDI for ch in ln):
            err(lno, "chứa ký tự điều khiển hướng chữ (bidi), không cho phép")
        for ch in ln:
            if unicodedata.category(ch) in ("Cc",) and ch not in "\t\r":
                err(lno, f"chứa ký tự điều khiển U+{ord(ch):04X}")
                break
    if errors:
        return errors, info
    src = src.lstrip("\ufeff")
    try:
        toks = tokenize(src)
    except TokError as e:
        return [str(e)], info

    n = len(toks)
    # Tên do chính alpha định nghĩa (che tên import của engine). Phân theo namespace của Rust.
    local_types, local_values = set(), set()
    for k in range(n - 1):
        kw, nm = toks[k].val, toks[k + 1]
        if toks[k].kind != "ident" or nm.kind != "ident":
            continue
        if kw in ("struct", "enum", "type", "trait", "union"):
            local_types.add(nm.val)
            if kw == "struct" and k + 2 < n and toks[k + 2].val in ("(", ";"):
                local_values.add(nm.val)       # tuple/unit struct có cả hàm dựng
        elif kw in ("fn", "const"):
            local_values.add(nm.val)
    in_use = False
    for k, t in enumerate(toks):
        if t.kind == "ident" and t.val == "use":
            in_use = True
        elif t.val == ";":
            in_use = False
        prev = toks[k - 1] if k else None
        nxt = toks[k + 1] if k + 1 < n else None
        nxt2 = toks[k + 2] if k + 2 < n else None

        if t.kind == "punct" and t.val == "#":
            if nxt and nxt.val == "!":
                err(t.line, "không được dùng thuộc tính cấp file (#![...])")
                continue
            if nxt and nxt.val == "[" and nxt2 and nxt2.kind == "ident":
                if nxt2.val not in ALLOWED_ATTRS:
                    err(t.line, f"thuộc tính #[{nxt2.val}] không được phép (chỉ: {', '.join(sorted(ALLOWED_ATTRS))})")
                elif nxt2.val == "derive":
                    j, depth = k + 3, 0
                    while j < n:
                        v = toks[j].val
                        if v == "(":
                            depth += 1
                        elif v == ")":
                            depth -= 1
                            if depth <= 0:
                                break
                        elif toks[j].kind == "ident" and toks[j].val not in ALLOWED_DERIVES:
                            err(toks[j].line, f"derive({toks[j].val}) không được phép")
                        j += 1
            else:
                err(t.line, "thuộc tính không hợp lệ")
            continue

        if t.kind != "ident":
            continue
        v = t.val
        after_dot = prev is not None and prev.val == "."
        after_path = prev is not None and prev.val == "::"

        if v in FORBIDDEN_KEYWORDS and not after_dot:
            # 'static lifetime là lifetime, không phải ident -> không bị bắt nhầm
            err(t.line, FORBIDDEN_KEYWORDS[v])
            continue

        # macro: ident ! ( [ {
        if nxt is not None and nxt.val == "!" and nxt2 is not None and nxt2.val in ("(", "[", "{") and v not in RUST_KEYWORDS:
            if v not in ALLOWED_MACROS:
                err(t.line, f"macro {v}! không được phép (chỉ: {', '.join(sorted(ALLOWED_MACROS))})")
            continue

        # đường dẫn std/core/alloc
        if v in STD_ROOTS and not after_dot and not (nxt is not None and nxt.val == "::"):
            if in_use or after_path:
                err(t.line, f"chỉ dùng {v}::<module>, không đặt bí danh cho {v}")
            continue                      # biến tên std (độ lệch chuẩn) thì không sao
        if v in STD_ROOTS and not after_dot:
            if nxt2 is None:
                err(t.line, "đường dẫn std không hợp lệ")
                continue
            segs = []
            if nxt2.val == "{":
                segs, _ = _split_group(toks, k + 2)
            else:
                segs = [nxt2]
            for s in segs:
                if s.val == "*":
                    err(s.line, f"không được dùng {v}::*")
                elif s.kind != "ident" or s.val not in ALLOWED_STD:
                    err(s.line, f"{v}::{s.val} không được phép (chỉ: {', '.join(sorted(ALLOWED_STD))})")
            continue

        if after_dot:
            continue
        # sau "::" chỉ bỏ qua khi gốc đường dẫn không phải self:: (self::X trỏ vào tên import của engine)
        if after_path and not (k >= 2 and toks[k - 2].val == "self"):
            continue
        if v in reserved:
            path_use = nxt is not None and nxt.val == "::"
            call_use = nxt is not None and nxt.val == "("
            if path_use:
                ok = v in local_types
            elif call_use:
                ok = v in local_values
            else:
                ok = v in local_types or v in local_values
            if not ok:
                err(t.line, f"tên '{v}' trùng tên nội bộ của engine hoặc bị cấm (file, env, I/O...). Hãy đổi tên hoặc tự định nghĩa nó trong alpha")
            continue

    # yêu cầu cấu trúc
    vals = [(t.kind, t.val) for t in toks]

    def has_seq(*seq):
        L = len(seq)
        return any(tuple(x[1] for x in vals[i:i + L]) == seq for i in range(len(vals) - L + 1))

    if not has_seq("pub", "fn", "create_alpha"):
        err(0, "thiếu `pub fn create_alpha(params: &ParameterSet) -> Box<dyn Alpha>`")
    if not has_seq("pub", "fn", "alpha_inputs"):
        err(0, "thiếu `pub fn alpha_inputs() -> Vec<AlphaInput>`")
    if not has_seq("impl", "Alpha", "for"):
        err(0, "thiếu `impl Alpha for <struct của bạn>`")
    # tham số: engine dò bằng regex trên TOÀN văn bản (kể cả comment) -> phải khớp đúng cách engine hiểu
    eng_re = r'AlphaInput::new\s*\(\s*"([^"]+)"\s*,\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))'
    strict_re = eng_re + r"\s*\)"
    code = strip_comments(src)
    raw_params = re.findall(eng_re, src)
    params = re.findall(strict_re, code)
    n_new = len(re.findall(r"AlphaInput::new\s*\(", code))
    if not params:
        err(0, 'cần ít nhất 1 tham số AlphaInput::new("tên", số)')
    if n_new != len(params):
        err(0, 'mỗi tham số phải đúng dạng AlphaInput::new("tên", <số viết thẳng, không dấu _ , không e>), ví dụ AlphaInput::new("period", 20.0)')
    if [p[0] for p in raw_params] != [p[0] for p in params]:
        err(0, "không để chữ AlphaInput::new(...) trong comment hoặc chuỗi (engine sẽ hiểu nhầm là tham số)")
    seen = set()
    for name, _ in params:
        if not NAME_RE.match(name):
            err(0, f"tên tham số không hợp lệ: {name!r} (chỉ chữ, số, _)")
        if name in seen:
            err(0, f"tham số trùng tên: {name}")
        seen.add(name)
    if len(params) > MAX_PARAMS:
        err(0, f"quá nhiều tham số ({len(params)} > {MAX_PARAMS})")
    info["params"] = [p[0] for p in params]
    rp = REQUIRED_PERIOD_RE.findall(code)
    if len(rp) > 1:
        err(0, "REQUIRED_PERIOD chỉ được khai báo một lần")
    elif rp and rp[0] not in PERIODS:
        err(0, f"REQUIRED_PERIOD = {rp[0]!r} không hợp lệ (chỉ: {', '.join(PERIODS)})")
    elif "REQUIRED_PERIOD" in code and not rp:
        err(0, 'khai báo khung bắt buộc đúng dạng: const REQUIRED_PERIOD: &str = "M5";')
    info["required_period"] = rp[0] if len(rp) == 1 and rp[0] in PERIODS else None
    m = re.search(r'fn\s+name\s*\(\s*&\s*self\s*\)\s*->\s*&\s*\'static\s+str\s*\{\s*"([^"]*)"', src)
    info["name"] = m.group(1) if m else None
    if not m:
        err(0, 'fn name(&self) -> &\'static str phải trả về một chuỗi viết thẳng, ví dụ "alpha_abc_trader"')
    return errors, info


def compile_check(src, template_path=DEFAULT_TEMPLATE, rustc="rustc", timeout=120):
    """Biên dịch thử (chỉ kiểm kiểu). Trả về (ok, errors). Chỉ lộ lỗi thuộc file alpha."""
    tpl = Path(template_path).read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="qc_chk_") as td:
        td = Path(td)
        alpha = td / "alpha.rs"
        alpha.write_text(src, encoding="utf-8")
        runner = td / "runner.rs"
        runner.write_text(tpl.replace("__ALPHA_INCLUDE_PATH__", json.dumps(str(alpha))), encoding="utf-8")
        cmd = [rustc, "--crate-type", "cdylib", "--edition=2024", "--emit=metadata", "--error-format=json",
               "-o", str(td / "out.rmeta"), str(runner)]
        flags = 0x00004000 if os.name == "nt" else 0   # BELOW_NORMAL_PRIORITY_CLASS
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=str(td), creationflags=flags)
        except subprocess.TimeoutExpired:
            return False, [f"biên dịch quá {timeout} giây"]
        errors = []
        for ln in r.stderr.splitlines():
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("level") not in ("error", "error: internal compiler error"):
                continue
            msg = d.get("message", "")
            if msg.startswith("aborting due to"):
                continue
            spans = [s for s in d.get("spans", []) if s.get("is_primary")]
            mine = [s for s in spans if Path(s.get("file_name", "")).name == "alpha.rs"]
            if mine:
                s = mine[0]
                txt = (s.get("text") or [{}])[0].get("text", "").strip()
                hint = []
                for ch in d.get("children", []):
                    if ch.get("level") in ("help", "note") and ch.get("message") and not any(
                            Path(x.get("file_name", "")).name == "runner.rs" for x in ch.get("spans", [])):
                        hint.append(ch["message"])
                e = f"dòng {s['line_start']}: {msg}"
                if txt:
                    e += f"  |  {txt[:160]}"
                if hint:
                    e += "  (gợi ý: " + "; ".join(hint)[:200] + ")"
                errors.append(e)
            else:
                # lỗi phía khung engine do alpha gây ra: chỉ trả thông điệp, không trả mã engine
                errors.append(f"(phía engine) {msg}")
        errors = list(dict.fromkeys(errors))   # bỏ trùng, giữ thứ tự
        if r.returncode != 0 and not errors:
            errors.append(f"biên dịch thất bại (mã {r.returncode})")
        return r.returncode == 0, errors[:40]


def check(src, template_path=DEFAULT_TEMPLATE, do_compile=True):
    """Kiểm tra đầy đủ: tĩnh rồi (nếu đạt) biên dịch. Trả về dict kết quả."""
    tpl = Path(template_path).read_text(encoding="utf-8")
    errs, info = static_check(src, engine_reserved_names(tpl))
    res = {"static_ok": not errs, "static_errors": errs, "params": info["params"], "fn_name": info["name"],
           "required_period": info.get("required_period"),
           "compile_ok": None, "compile_errors": []}
    if errs or not do_compile:
        return res
    ok, cerrs = compile_check(src if isinstance(src, str) else src.decode("utf-8"), template_path)
    res["compile_ok"], res["compile_errors"] = ok, cerrs
    return res


def reserved_list(template_path=DEFAULT_TEMPLATE):
    return sorted(engine_reserved_names(Path(template_path).read_text(encoding="utf-8")))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        sys.exit(2)
    tp = DEFAULT_TEMPLATE
    if "--template" in a:
        tp = Path(a[a.index("--template") + 1])
    if a[0] == "--reserved":
        print("\n".join(reserved_list(tp)))
        sys.exit(0)
    src = Path(a[0]).read_bytes()
    try:
        src = src.decode("utf-8")
    except UnicodeDecodeError:
        print(json.dumps({"static_ok": False, "static_errors": ["file phải là UTF-8"]}, ensure_ascii=False))
        sys.exit(1)
    out = check(src, tp, do_compile="--compile" in a)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    sys.exit(0 if out["static_ok"] and out["compile_ok"] is not False else 1)
