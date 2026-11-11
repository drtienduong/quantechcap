"use strict";
/* QuanTechCap - giao diện web (bản thử). Không dùng innerHTML với dữ liệu từ server: mọi chuỗi đi qua textContent. */

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) e.addEventListener(ev, fn);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}
const fmt = (x, d = 2) => (x == null || Number.isNaN(x)) ? "–" : Number(x).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });
const pad = (n) => String(n).padStart(2, "0");
function dt(ms) { if (ms == null) return "–"; const d = new Date(ms); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; }

let ST = null;   // trạng thái đăng nhập + hằng số từ server
let timers = [];
function clearTimers() { timers.forEach(clearInterval); timers = []; }

async function api(path, body) {
  const o = { method: "GET", headers: {} };
  if (body !== undefined) {
    o.method = "POST";
    o.headers["Content-Type"] = "application/json";
    o.headers["X-CSRF-Token"] = ST ? ST.csrf : "";
    o.body = JSON.stringify(body);
  }
  const r = await fetch(path, o);
  if (r.status === 401) { location.href = "/login"; throw new Error("401"); }
  return r.json();
}

/* ---------- đăng nhập ---------- */
function QC_LOGIN() {
  document.getElementById("f").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = document.getElementById("err");
    err.textContent = "";
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: document.getElementById("email").value, password: document.getElementById("pw").value }) });
    const j = await r.json();
    if (j.ok) location.href = "/"; else err.textContent = j.error || "Đăng nhập thất bại";
  });
}

/* ---------- khung ứng dụng ---------- */
function QC_APP() {
  const root = document.getElementById("app");
  const main = h("main");
  const deskBadge = h("span", { class: "badge" }, "data-server: ?");
  const nav = h("div", { id: "nav" });
  const banner = h("div", { class: "banner", hidden: true });
  root.append(h("header", {}, h("span", { class: "brand" }, "QuanTechCap"), nav, h("span", { class: "sp" }), deskBadge,
    h("span", { id: "who" }), h("button", { class: "btn", on: { click: async () => { await api("/api/logout", {}); location.href = "/login"; } } }, "Đăng xuất")), banner, main);

  async function refreshStatus() {
    const s = await api("/api/status");
    ST = s;
    deskBadge.textContent = s.desk_online ? "data-server: online" : "data-server: offline";
    deskBadge.className = "badge " + (s.desk_online ? "on" : "off");
    banner.textContent = s.desk_notice || "";
    banner.hidden = !s.desk_notice;
    document.getElementById("who").textContent = s.email;
  }
  function setNav() {
    const cur = location.hash || "#/";
    nav.replaceChildren(...[["#/", "Alpha"], ["#/submit", "Nộp alpha"], ["#/jobs", "Lịch sử chạy"], ["#/ispass", "Alpha pass IS"], ["#/os1", "Lịch sử OS1"], ["#/ask", "Hỏi AI"]].map(([href, label]) =>
      h("a", { href, class: (cur === href || cur.startsWith(href + "/") || (href === "#/jobs" && cur.startsWith("#/job/"))) ? "active" : "" }, label)),
      h("a", { href: "/guide_ai", target: "_blank", rel: "noopener" }, "Hướng dẫn AI"));
    // điện thoại: menu cuộn ngang -> kéo mục đang mở vào giữa tầm nhìn
    const act = nav.querySelector("a.active");
    if (act && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = Math.max(0, act.offsetLeft - (nav.clientWidth - act.offsetWidth) / 2);
  }
  async function route() {
    clearTimers();
    main.classList.remove("wide");
    setNav();
    const hash = location.hash || "#/";
    main.replaceChildren(h("p", { class: "muted" }, "Đang tải…"));
    try {
      if (hash.startsWith("#/run/")) await pageRun(main, decodeURIComponent(hash.slice(6)));
      else if (hash.startsWith("#/job/")) await pageJob(main, parseInt(hash.slice(6), 10));
      else if (hash === "#/jobs" || hash.startsWith("#/jobs/")) await pageJobs(main, parseInt(hash.slice(7), 10) || 1);
      else if (hash === "#/submit") await pageSubmit(main);
      else if (hash === "#/ask" || hash.startsWith("#/ask?")) await pageAsk(main, new URLSearchParams(hash.split("?")[1] || ""));
      else if (hash === "#/ispass" || hash.startsWith("#/ispass/")) await pageIsPass(main, parseInt(hash.slice(9), 10) || 1);
      else if (hash === "#/os1" || hash.startsWith("#/os1/")) await pageOs1(main, parseInt(hash.slice(6), 10) || 1);
      else await pageAlphas(main);
    } catch (e) { if (String(e) !== "Error: 401") main.replaceChildren(h("p", { class: "err" }, "Lỗi: " + e)); }
  }
  window.addEventListener("hashchange", route);
  (async () => { await refreshStatus(); route(); setInterval(() => refreshStatus().catch(() => {}), 5000); })();
}

/* ---------- trang alpha ---------- */
async function pageAlphas(main) {
  const r = await api("/api/alphas");
  const rows = r.alphas.map((a) => h("tr", {}, h("td", { class: "mono" }, a.name), h("td", {}, Object.keys(a.params).length + " tham số"),
    h("td", {}, (a.meta && a.meta.required_period) ? h("b", {}, a.meta.required_period) : h("span", { class: "muted" }, "tùy chọn")),
    h("td", { class: "muted" }, a.updated), h("td", {}, h("a", { href: "#/run/" + encodeURIComponent(a.name), class: "btn primary" }, "Chạy backtest/optimize"))));
  main.replaceChildren(h("h2", {}, "Alpha của bạn"),
    h("div", { class: "card" }, r.alphas.length ? h("table", {}, h("tr", {}, h("th", {}, "Tên"), h("th", {}, "Tham số"), h("th", {}, "Khung"), h("th", {}, "Đồng bộ"), h("th")), rows)
      : h("p", { class: "muted" }, "Chưa có alpha. Viết alpha theo ", h("a", { href: "/guide_ai", target: "_blank", rel: "noopener" }, "bộ khung"), " rồi nộp ở trang ", h("a", { href: "#/submit" }, "Nộp alpha"), ".")),
    h("p", { class: "muted" }, "Alpha đã duyệt được lưu và chạy trên data-server. Danh sách đồng bộ mỗi phút."));
}

/* ---------- nộp alpha ---------- */
const subLabel = { queued: "chờ kiểm tra", checking: "đang kiểm tra", rejected: "không đạt", pending_review: "chờ duyệt",
  approved: "đã duyệt", review_rejected: "bị từ chối", failed: "lỗi" };
const subTag = (s) => h("span", { class: "tag " + ({ approved: "done", rejected: "failed", review_rejected: "failed", failed: "failed",
  checking: "running", queued: "running" }[s] || "") }, subLabel[s] || s);

async function pageSubmit(main) {
  const suffix = ST.alpha_suffix;
  const fileIn = h("input", { type: "file", accept: ".rs" });
  const nameIn = h("input", { type: "text", size: 40, placeholder: "alpha_y_tuong" });
  // Trader gõ tên bất kỳ; platform tự thêm hậu tố cố định của tài khoản (cùng quy tắc với qc_common.full_alpha_name).
  const fullName = (v) => {
    let b = v.trim().replace(/\.rs$/i, "").replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
    if (b.endsWith(suffix)) b = b.slice(0, -suffix.length).replace(/_+$/, "");
    return b && b !== suffix.slice(1) ? b + suffix : "";
  };
  const preview = h("span", { class: "mono" });
  const showPreview = () => { const f = fullName(nameIn.value); preview.textContent = f ? `Tên alpha sẽ là: ${f}` : ""; };
  nameIn.addEventListener("input", showPreview);
  const info = h("div", {});
  const listBox = h("div", {});
  const detail = h("div", {});
  let srcText = null;
  fileIn.addEventListener("change", async () => {
    info.replaceChildren();
    const f = fileIn.files[0];
    if (!f) return;
    if (f.size > 200000) { info.append(h("p", { class: "err" }, "File quá lớn (tối đa 200 KB).")); srcText = null; return; }
    srcText = await f.text();
    // Tên alpha gợi ý = tên file (bỏ ".rs", bỏ "(1)" trình duyệt thêm khi tải trùng, ký tự lạ -> "_", bỏ hậu tố trader
    // nếu đã có vì platform tự thêm). Trader sửa tùy ý trong ô. Luôn cập nhật khi chọn file mới.
    let base = f.name.replace(/\.rs$/i, "").replace(/\s*\(\d+\)$/, "").replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
    if (base.endsWith(suffix)) base = base.slice(0, -suffix.length).replace(/_+$/, "");
    nameIn.value = base;
    showPreview();
    info.append(h("p", { class: "muted" }, `${f.name}: ${f.size} byte, ${srcText.split("\n").length} dòng`));
  });
  const btn = h("button", { class: "btn primary", on: { click: async () => {
    info.replaceChildren();
    if (!srcText) { info.append(h("p", { class: "err" }, "Chọn file .rs trước.")); return; }
    const name = fullName(nameIn.value);
    if (!name || name.length > 80) {
      info.append(h("p", { class: "err" }, `Tên không hợp lệ: cần ít nhất một chữ hoặc số, cả hậu tố ${suffix} tối đa 80 ký tự. Sửa ô "Tên alpha" rồi nộp lại.`)); return; }
    btn.disabled = true;
    const j = await api("/api/submissions", { name, source: srcText });
    btn.disabled = false;
    if (!j.ok) { info.append(h("p", { class: "err" }, j.error || "Lỗi")); return; }
    info.append(h("p", { class: "ok" }, `Đã nộp (#${j.id}) với tên ${j.name || name}. Data-server sẽ kiểm tra trong vài giây.`));
    fileIn.value = ""; srcText = null;
    await load(); showDetail(j.id);
  } } }, "Nộp alpha");

  async function showDetail(id) {
    const r = await api("/api/submissions/" + id);
    if (!r.ok) { detail.replaceChildren(h("p", { class: "err" }, r.error || "Lỗi")); return; }
    const sb = r.submission, res = sb.result || {};
    const errs = [...(res.static_errors || []), ...(res.compile_errors || [])];
    detail.replaceChildren(h("div", { class: "card" },
      h("h3", {}, `#${sb.id} · `, h("span", { class: "mono" }, sb.name), " ", subTag(sb.status)),
      h("p", {}, sb.message || ""),
      res.params && res.params.length ? h("p", { class: "muted" }, "Tham số: ", h("span", { class: "mono" }, res.params.join(", "))) : null,
      errs.length ? h("div", {}, h("p", {}, `Lỗi (${errs.length}):`), h("ul", {}, errs.map((e) => h("li", { class: "mono err" }, e)))) : null,
      h("p", { class: "muted mono" }, `sha256 ${sb.sha256 || ""} · ${sb.size || 0} byte · nộp ${sb.created} UTC`),
      h("a", { class: "btn", href: "#/ask?sub=" + sb.id }, "Hỏi AI về bản nộp này")));
  }

  async function load() {
    const r = await api("/api/submissions");
    const subs = r.submissions || [];
    listBox.replaceChildren(subs.length ? h("table", {},
      h("tr", {}, h("th", {}, "#"), h("th", {}, "Tên"), h("th", {}, "Trạng thái"), h("th", {}, "Thông báo"), h("th", {}, "Nộp lúc (UTC)")),
      subs.map((x) => h("tr", { class: "click", on: { click: () => showDetail(x.id) } },
        h("td", {}, x.id), h("td", { class: "mono" }, x.name), h("td", {}, subTag(x.status)),
        h("td", { class: x.notice ? "warn" : (x.status === "failed" || x.status === "rejected" || x.status === "review_rejected" ? "err" : "muted") }, x.notice || x.message),
        h("td", { class: "muted" }, x.created))))
      : h("p", { class: "muted" }, "Chưa nộp alpha nào."));
    return subs.some((x) => x.status === "queued" || x.status === "checking");
  }

  main.replaceChildren(
    h("h2", {}, "Nộp alpha"),
    h("div", { class: "card" }, h("h3", {}, "Nộp mã nguồn (.rs)"),
      h("div", { class: "row" }, h("label", {}, "File .rs", h("br"), fileIn), h("label", {}, "Tên alpha", h("br"), nameIn, h("span", { class: "mono muted sfx" }, suffix)), btn),
      h("p", { class: "muted" }, preview),
      info,
      h("p", { class: "muted" }, `Đặt tên tùy ý. Platform tự thêm hậu tố ${suffix} của tài khoản bạn vào tên và vào fn name() trong mã, không cần sửa file.`),
      h("p", { class: "muted" }, "Nộp lại cùng tên = phiên bản mới. Web chỉ giữ mã tạm thời đến khi data-server nhận về."),
      h("p", { class: "muted" }, "Viết alpha theo ", h("a", { href: "/guide_ai", target: "_blank", rel: "noopener" }, "Bộ khung alpha cho AI"), ".")),
    detail,
    h("div", { class: "card" }, h("h3", {}, "Các lần nộp"), listBox));
  let pending = await load();
  timers.push(setInterval(async () => {
    if (!pending) return;
    pending = await load().catch(() => false);
  }, 3000));
  timers.push(setInterval(async () => { pending = await load().catch(() => pending); }, 15000));
}

/* ---------- form chạy ---------- */
async function pageRun(main, name) {
  const r = await api("/api/alphas");
  const a = r.alphas.find((x) => x.name === name);
  if (!a) { main.replaceChildren(h("p", { class: "err" }, "Không tìm thấy alpha " + name)); return; }
  const lim = ST.limits;
  const isEnd = ST.todate_max || lim.is_end;
  const f = {
    type: h("select", {}, h("option", { value: "optimize_is" }, "Optimize trên IS"), h("option", { value: "smoke" }, "Smoke test (nhanh, giới hạn tick)")),
    mode: h("select", {}, h("option", { value: "portfolio" }, "Portfolio (chung 1 tài khoản)"), h("option", { value: "independent" }, "Từng symbol riêng")),
    period: h("select", {}, ST.periods.map((p) => h("option", { value: p, selected: p === ((a.meta && a.meta.required_period) || "H1") }, p))),
    from: h("input", { type: "text", value: "2010.01.01", size: 10 }), to: h("input", { type: "text", value: isEnd, size: 10 }),
    deposit: h("input", { type: "number", value: 10000 }), leverage: h("input", { type: "text", value: "1:1000", size: 7, title: "Tối đa 1:1000. Margin call 100%, stop-out 50%." }),
    swap: h("select", {}, h("option", { value: "zero" }, "zero (không swap)"), h("option", { value: "synthetic" }, "synthetic")),
    lot: h("input", { type: "number", value: 0.01, step: "any" }), pv: h("input", { type: "number", value: 100000 }),
    comm: h("input", { type: "number", value: 0, step: "any" }), ticks: h("input", { type: "number", value: 200000 }),
  };
  const SYM = Object.fromEntries(ST.symbols.map((x) => [x.symbol, x]));
  const boxes = ST.symbols.map((x) => h("input", { type: "checkbox", value: x.symbol, checked: x.symbol === "EURUSD" }));
  const boxOf = Object.fromEntries(boxes.map((b) => [b.value, b]));
  const symGroups = ST.groups.map((g) => h("div", { class: "symgroup" }, h("b", {}, g + ": "),
    ST.symbols.filter((x) => x.group === g).map((x) => h("label", { class: "sym", title: `từ ${x.start} · point value ${x.point_value}/lot${x.swap ? " · có swap" : ""}` },
      boxOf[x.symbol], x.symbol))));
  // Ngày bắt đầu: gõ 0 hoặc ngày sớm hơn dữ liệu -> tự nhảy về ngày bắt đầu SỚM NHẤT của các symbol đang chọn
  const dkey = (v) => String(v || "").replace(/\D/g, "");
  const fromHint = h("div", { class: "muted small" });
  function earliestStart() {
    const sel = boxes.filter((b) => b.checked).map((b) => SYM[b.value].start);
    return sel.length ? sel.reduce((m, x) => (dkey(x) < dkey(m) ? x : m)) : null;
  }
  function fixFrom() {
    const e = earliestStart();
    if (!e) return;
    const v = f.from.value.trim(), k = dkey(v);
    if (v === "0" || (k.length === 8 && k < dkey(e))) {
      f.from.value = e;
      fromHint.textContent = "Tự đặt = ngày bắt đầu dữ liệu sớm nhất của các symbol đã chọn";
    } else if (k.length === 8) fromHint.textContent = "";
  }
  f.from.addEventListener("input", () => { if (f.from.value.trim() === "0") fixFrom(); });
  f.from.addEventListener("change", fixFrom);
  boxes.forEach((b) => b.addEventListener("change", fixFrom));
  const setSyms = (list) => { boxes.forEach((b) => { b.checked = list.includes(b.value); }); fixFrom(); };
  const addSyms = (list) => { boxes.forEach((b) => { if (list.includes(b.value)) b.checked = true; }); fixFrom(); };
  const groupSyms = (g) => ST.symbols.filter((x) => x.group === g).map((x) => x.symbol);
  const pRows = Object.entries(a.params).map(([k, v]) => ({
    k, a: h("input", { type: "number", value: v[0], step: "any" }), s: h("input", { type: "number", value: v[1], step: "any" }), b: h("input", { type: "number", value: v[2], step: "any" }),
  }));
  const reqP = (a.meta && a.meta.required_period) || null;
  const periodWarn = h("p", { class: "err", hidden: true });
  const checkPeriod = () => {
    const bad = reqP && f.period.value !== reqP;
    periodWarn.hidden = !bad;
    periodWarn.textContent = bad ? `Alpha này khai báo khung bắt buộc ${reqP}. Chạy với ${f.period.value} rất có thể ra 0 lệnh.` : "";
    return !bad;
  };
  f.period.addEventListener("change", checkPeriod);
  const cnt = h("b", {});
  const msg = h("div", {});
  const ticksWrap = h("label", {}, "Giới hạn tick (smoke)", h("br"), f.ticks);

  function countVariants() {
    let t = 1;
    for (const p of pRows) {
      const s = parseFloat(p.s.value), x = parseFloat(p.a.value), y = parseFloat(p.b.value);
      if (!(s > 0)) continue;
      if (!(y >= x)) { t = NaN; break; }
      t *= Math.floor((y - x) / s + 1e-9) + 1;
    }
    return t;
  }
  function refresh() {
    const t = countVariants();
    const cap = f.type.value === "smoke" ? lim.max_variants_smoke : lim.max_variants;
    cnt.textContent = Number.isNaN(t) ? "không hợp lệ" : String(t);
    cnt.className = (Number.isNaN(t) || t > cap) ? "err" : "";
    ticksWrap.style.display = f.type.value === "smoke" ? "" : "none";
  }
  [f.type, ...pRows.flatMap((p) => [p.a, p.s, p.b])].forEach((e) => e.addEventListener("input", refresh));
  f.type.addEventListener("change", () => {
    if (f.type.value === "smoke") { f.from.value = "2023.10.01"; f.to.value = isEnd; setSyms(["EURUSD"]); }
    refresh();
  });

  const submit = h("button", { class: "btn primary", on: { click: async () => {
    msg.replaceChildren();
    const params = {};
    for (const p of pRows) params[p.k] = [parseFloat(p.a.value), parseFloat(p.s.value), parseFloat(p.b.value)];
    const cfg = {
      type: f.type.value, alpha: name, symbols_mode: f.mode.value, symbols: boxes.filter((b) => b.checked).map((b) => b.value),
      period: f.period.value, fromdate: f.from.value.trim(), todate: f.to.value.trim(), deposit: parseFloat(f.deposit.value),
      leverage: f.leverage.value.trim(), swap_mode: f.swap.value, lot: parseFloat(f.lot.value), point_value_per_lot: parseFloat(f.pv.value),
      commission_per_trade: parseFloat(f.comm.value), max_ticks: f.type.value === "smoke" ? parseInt(f.ticks.value, 10) || 0 : 0, params,
    };
    if (!checkPeriod() && !confirm(`Alpha yêu cầu khung ${reqP} nhưng bạn chọn ${f.period.value}. Vẫn chạy?`)) return;
    submit.disabled = true;
    const j = await api("/api/jobs", cfg);
    submit.disabled = false;
    if (j.ok) { location.hash = "#/job/" + j.id; return; }
    msg.append(h("p", { class: "err" }, j.error || "Lỗi"), h("ul", {}, (j.details || []).map((d) => h("li", { class: "err" }, d))));
  } } }, "Gửi job về data-server");

  main.replaceChildren(
    h("h2", {}, "Chạy: ", h("span", { class: "mono" }, name)),
    h("div", { class: "card" }, h("div", { class: "row" },
      h("label", {}, "Loại job", h("br"), f.type), h("label", {}, "Khung thời gian" + (reqP ? ` (alpha yêu cầu ${reqP})` : ""), h("br"), f.period),
      h("label", { title: "Gõ 0 để lấy ngày bắt đầu dữ liệu sớm nhất của các symbol đã chọn" }, "Ngày bắt đầu (0 = sớm nhất)", h("br"), f.from, fromHint), h("label", {}, "Đến ngày (≤ " + isEnd + ")", h("br"), f.to), ticksWrap),
      periodWarn,
      h("p", { class: "muted" }, ST.os1_access ? "Tài khoản của bạn được chạy đến " + isEnd + " (IS + OS1). OS2 không chạy qua web. "
        : "Tài khoản của bạn chạy IS đến " + isEnd + ". Biến thể pass IS được nộp chạy OS1 ở trang Alpha pass IS. ", "Smoke: tối đa ",
        lim.smoke_max_days, " ngày, ", lim.smoke_max_symbols, " symbol, được ưu tiên chạy trước các job optimize đang chờ.")),
    h("div", { class: "card" }, h("h3", {}, "Symbol"), h("div", { class: "row" }, h("label", {}, "Chế độ", h("br"), f.mode),
      h("button", { class: "btn", on: { click: () => setSyms(["EURUSD"]) } }, "Chỉ EURUSD"),
      h("button", { class: "btn", on: { click: () => setSyms(ST.fx25.slice(0, 20)) } }, "20 cặp"),
      h("button", { class: "btn", on: { click: () => setSyms(ST.fx25) } }, "25 cặp FX"),
      ST.groups.filter((g) => g !== "FX").map((g) => h("button", { class: "btn", on: { click: () => addSyms(groupSyms(g)) } }, "+ " + g)),
      h("button", { class: "btn", on: { click: () => setSyms([]) } }, "Bỏ hết")),
      h("div", { class: "symbox" }, symGroups),
      h("p", { class: "muted" }, "Point value/lot tự đặt theo symbol: FX 100000 (ô bên dưới), XAUUSD 100, XAGUSD 5000, đồng 25000 lb, dầu 1000, cacao 10 tấn, đường 50 tấn, cà phê 37500 lb, bông 50000 lb, nước cam 15000 lb (3 loại cuối báo giá bằng cent, engine tự quy đổi), chỉ số 1 (HKGIDXHKD 100), crypto 1. ",
        "Swap synthetic chỉ có cho FX và XAUUSD. Dữ liệu *_BN (Binance) là tick tổng hợp từ nến 1 phút. Rê chuột lên symbol để xem ngày bắt đầu dữ liệu."),
      h("p", { class: "warn" }, "Lưu ý: alpha dùng thông tin tài khoản (…_with_account) chỉ giao dịch ở chế độ Portfolio; chế độ \"Từng symbol riêng\" có thể ra 0 lệnh.")),
    h("div", { class: "card" }, h("h3", {}, "Tài khoản"), h("div", { class: "row" },
      h("label", {}, "Deposit", h("br"), f.deposit), h("label", {}, "Leverage", h("br"), f.leverage), h("label", {}, "Swap", h("br"), f.swap),
      h("label", {}, "Lot", h("br"), f.lot), h("label", {}, "Point value/lot (FX)", h("br"), f.pv), h("label", {}, "Commission/lot/deal", h("br"), f.comm))),
    h("div", { class: "card" }, h("h3", {}, "Tham số (start, step, stop; step = 0 là một giá trị)"),
      h("table", {}, h("tr", {}, h("th", {}, "Tham số"), h("th", {}, "Start"), h("th", {}, "Step"), h("th", {}, "Stop")),
        pRows.map((p) => h("tr", {}, h("td", { class: "mono" }, p.k), h("td", {}, p.a), h("td", {}, p.s), h("td", {}, p.b)))),
      h("p", {}, "Số biến thể: ", cnt, " (tối đa ", lim.max_variants, "; smoke ", lim.max_variants_smoke, ")")),
    submit, msg);
  refresh();
}

/* ---------- danh sách job ---------- */
const statusLabel = { queued: "đang chờ", dispatched: "đã giao data-server", running: "đang chạy", done: "xong", failed: "lỗi", cancelled: "đã hủy" };
const tag = (s) => h("span", { class: "tag " + s }, statusLabel[s] || s);

const JOBS_PER_PAGE = 20;
async function pageJobs(main, page) {
  page = Math.max(1, page || 1);
  const head = h("h2", {}, "Lịch sử chạy");
  const box = h("div", { class: "card" }, h("p", { class: "muted" }, "Đang tải…"));
  main.replaceChildren(head, box);
  let last = "", active = false;
  const go = (p) => { location.hash = p > 1 ? "#/jobs/" + p : "#/jobs"; };
  function pagerEl(pages, total) {
    if (pages <= 1) return h("p", { class: "muted small" }, total + " job");
    const nums = [];
    for (let p = 1; p <= pages; p++) {
      if (p === 1 || p === pages || Math.abs(p - page) <= 2) nums.push(p);
      else if (nums[nums.length - 1] !== "…") nums.push("…");
    }
    return h("div", { class: "pager" },
      h("button", { class: "btn", disabled: page <= 1, on: { click: () => go(page - 1) } }, "‹ Mới hơn"),
      nums.map((p) => p === "…" ? h("span", { class: "muted" }, "…")
        : h("button", { class: "btn" + (p === page ? " primary" : ""), on: { click: () => go(p) } }, String(p))),
      h("button", { class: "btn", disabled: page >= pages, on: { click: () => go(page + 1) } }, "Cũ hơn ›"),
      h("span", { class: "muted" }, `Trang ${page}/${pages} · ${total} job`));
  }
  const srcSel = sessionStorage.getItem("qc_jobsrc") || "";
  const srcBar = h("div", { class: "pager" });
  async function load() {
    const r = await api(`/api/jobs?limit=${JOBS_PER_PAGE}&offset=${(page - 1) * JOBS_PER_PAGE}` + (srcSel ? "&src=" + srcSel : ""));
    srcBar.replaceChildren(...(r.offline_total ? [["", "Tất cả"], ["web", "Chạy trên web"], ["offline", `Report offline (${r.offline_total})`]].map(([v, l]) =>
      h("button", { class: "btn" + (v === srcSel ? " primary" : ""), on: { click: () => { sessionStorage.setItem("qc_jobsrc", v); if (location.hash !== "#/jobs") location.hash = "#/jobs"; else window.dispatchEvent(new HashChangeEvent("hashchange")); } } }, l)) : []));
    const pages = Math.max(1, Math.ceil((r.total || 0) / JOBS_PER_PAGE));
    if (page > pages) { go(pages); return; }
    active = r.jobs.some((j) => ["queued", "dispatched", "running"].includes(j.status));
    const key = JSON.stringify(r);
    if (key === last) return;           // không đổi thì không vẽ lại (tránh nháy, giữ thao tác của người dùng)
    last = key;
    box.replaceChildren(...(r.jobs.length ? [srcBar, pagerEl(pages, r.total), h("table", {},
      h("tr", {}, h("th", {}, "#"), h("th", {}, "Alpha"), h("th", {}, "Loại"), h("th", {}, "Nguồn"), h("th", {}, "Trạng thái"), h("th", {}, "Tiến độ"), h("th", {}, "Ghi chú"), h("th", {}, "Tạo lúc (UTC)")),
      r.jobs.map((j) => h("tr", { class: "click", on: { click: () => { location.hash = "#/job/" + j.id; } } },
        h("td", {}, j.id), h("td", { class: "mono" }, j.alpha), h("td", {}, j.type), h("td", { class: "muted" }, j.source || "web"), h("td", {}, tag(j.status)),
        h("td", {}, j.status === "running" || j.status === "dispatched" ? fmt(j.progress, 0) + "%" : (j.summary ? j.summary.variants + " biến thể" : "")),
        h("td", { class: j.status === "failed" ? "err small" : (j.notice ? "warn small" : "muted small") }, j.notice || (j.status === "done" ? "" : j.message || "")),
        h("td", { class: "muted" }, j.created)))),
      pages > 1 ? pagerEl(pages, r.total) : null].filter(Boolean)
      : [srcBar, h("p", { class: "muted" }, "Chưa có job.")]));
  }
  await load();
  let tick = 0;   // có job đang chạy trên trang: cập nhật 3 giây/lần; không thì 15 giây/lần
  timers.push(setInterval(() => { tick++; if (active || tick % 5 === 0) load().catch(() => {}); }, 3000));
}

function engineText(e) {
  if (!e) return "engine cũ (chưa ghi phiên bản)";
  return `engine ${e.version}` + (e.commit && e.commit.includes("dirty") ? " (đang sửa)" : "") + (e.sha ? ` · ${String(e.sha).slice(0, 8)}` : "");
}

/* ---------- Hỏi AI ---------- */
async function pageAsk(main, params) {
  let ref = params.get("job") ? { job_id: params.get("job"), label: "job #" + params.get("job") }
    : params.get("sub") ? { submission_id: params.get("sub"), label: "bản nộp #" + params.get("sub") } : null;
  const ta = h("textarea", { rows: "4", maxlength: "2000", placeholder: "Ví dụ: Vì sao job này không có lệnh nào? Vì sao bản nộp bị từ chối?", class: "askbox" });
  const refBox = h("div", {});
  const info = h("p", { class: "muted" });
  const err = h("p", { class: "err" });
  const list = h("div", {});
  function drawRef() {
    refBox.replaceChildren(ref ? h("p", {}, "Câu hỏi về ", h("b", {}, ref.label), " ",
      h("button", { class: "btn", on: { click: () => { ref = null; drawRef(); } } }, "Bỏ liên kết")) : h("p", { class: "muted" }, "Câu hỏi chung (muốn hỏi về một job hay bản nộp cụ thể, bấm nút \"Hỏi AI\" trong trang đó)."));
  }
  const btn = h("button", { class: "btn primary", on: { click: async () => {
    err.textContent = "";
    const q = ta.value.trim();
    if (!q) { err.textContent = "Nhập câu hỏi trước."; return; }
    btn.disabled = true;
    const body = { question: q };
    if (ref && ref.job_id) body.job_id = ref.job_id;
    if (ref && ref.submission_id) body.submission_id = ref.submission_id;
    const r = await api("/api/ask", body);
    btn.disabled = false;
    if (!r.ok) { err.textContent = r.error || "Lỗi"; return; }
    ta.value = ""; await load();
  } } }, "Gửi câu hỏi");
  main.replaceChildren(h("h2", {}, "Hỏi AI"),
    h("div", { class: "card" }, h("p", { class: "muted" }, "Trợ lý AI xem dữ liệu của chính bạn (job, bản nộp, chẩn đoán từ data-server) để giải thích lỗi và hướng sửa. ",
      "Trợ lý không xem được dữ liệu của người khác, không chạy hay sửa gì, và có thể sai: kiểm tra lại trước khi làm theo."),
      refBox, ta, h("div", { class: "row" }, btn, info), err),
    h("div", { class: "card" }, h("h3", {}, "Câu hỏi của bạn"), list));
  drawRef();
  let pending = false;
  async function load() {
    const r = await api("/api/ask");
    info.textContent = `Đã hỏi ${r.used_24h}/${r.per_day} câu trong 24 giờ`;
    pending = r.questions.some((x) => x.status === "queued" || x.status === "dispatched");
    list.replaceChildren(...(r.questions.length ? r.questions.map((x) => h("div", { class: "qa" },
      h("div", { class: "qa-q" }, h("span", { class: "muted small" }, `#${x.id} · ${x.created} UTC` + (x.kind === "job" ? ` · job #${x.ref_id}` : x.kind === "submission" ? ` · bản nộp #${x.ref_id}` : "")), " ",
        x.status === "done" ? null : tag(x.status === "dispatched" ? "running" : x.status), h("div", { class: "qa-text" }, x.question)),
      x.status === "done" || x.status === "failed" ? h("div", { class: "qa-a" + (x.status === "failed" ? " err" : "") }, x.answer || "")
        : h("div", { class: "qa-a muted" }, x.notice || (x.status === "dispatched" ? "Trợ lý đang trả lời (thường 20-60 giây)…" : "Đang chờ trợ lý nhận câu hỏi…"))))
      : [h("p", { class: "muted" }, "Chưa có câu hỏi nào.")]));
  }
  await load();
  timers.push(setInterval(() => { if (pending) load().catch(() => {}); }, 3000));
}

/* ---------- OS1 ---------- */
const yesNo = (v) => v === true ? "có" : v === false ? "không" : "-";
function os1Tag(o) {
  if (!o) return h("span", { class: "muted" }, "chưa nộp");
  if (o.status === "done") return h("a", { href: "#/job/" + o.job_id }, h("span", { class: "tag " + (o.os_pass ? "done" : "failed") }, o.os_pass ? "OS1 ĐẠT" : "OS1 KHÔNG ĐẠT"));
  if (o.status === "failed" || o.status === "cancelled") return h("a", { href: "#/job/" + o.job_id }, h("span", { class: "tag failed" }, o.status === "failed" ? "lỗi, nộp lại được" : "đã hủy, nộp lại được"));
  return h("a", { href: "#/job/" + o.job_id }, tag(o.status));
}
function os1Head(job) {
  const c = job.config, o = (job.summary && job.summary.os1) || {};
  const pr = Object.entries(c.params || {}).map(([k, v]) => `${k}=${v}`).join(" ");
  return [
    h("p", {}, `OS1 của biến thể run ${c.src_run_id} trong `, h("a", { href: "#/job/" + c.src_job_id }, `job IS #${c.src_job_id}`),
      ` · ${c.symbol || ""} · ${c.period || ""} · chạy liền ${c.fromdate} → ${c.todate}, chỉ hiển thị phần từ 2024.01.01`),
    h("p", { class: "mono muted" }, pr || "-"),
    job.status === "done" ? h("div", { class: "card" },
      h("h3", {}, "Kết quả OS1: ", h("span", { class: "tag " + (o.os_pass ? "done" : "failed") }, o.os_pass ? "ĐẠT" : "KHÔNG ĐẠT")),
      h("p", {}, `Sharpe OS1 ${vfmt(o.os_sharpe)} (cần > 0.7 × Sharpe IS ${vfmt(o.is_sharpe)} = ${vfmt(0.7 * (o.is_sharpe || 0))}) · `,
        `đỉnh mới: ${yesNo(o.os_new_high)} · DD OS1 ${vfmt(o.os_max_dd, 2)} ≤ DD IS ${vfmt(o.is_max_dd, 2)}: ${yesNo(o.os_no_new_dd)}`),
      h("p", {}, `Lợi nhuận OS1 ${vfmt(o.os_profit, 2)} · ${o.os_trades ?? "-"} lệnh · winrate ${vfmt(o.os_winrate, 1)}% · PF ${vfmt(o.os_profit_factor)}`
        + (o.stopped_out ? " · BỊ STOP-OUT" : "") + (o.os_note ? " · " + o.os_note : ""))) : null];
}

async function pageIsPass(main, page) {
  const PER = 20;
  const sel = new Map();
  const box = h("div", { class: "card" }, h("p", { class: "muted" }, "Đang tải…"));
  const btn = h("button", { class: "btn primary", disabled: true }, "Nộp chạy OS1");
  const info = h("span", { class: "muted" });
  const go = (p) => { location.hash = p > 1 ? "#/ispass/" + p : "#/ispass"; };
  function upBtn() { btn.disabled = sel.size === 0; btn.textContent = `Nộp chạy OS1 (${sel.size})`; }
  btn.addEventListener("click", async () => {
    if (!confirm(`Nộp ${sel.size} biến thể chạy OS1?\nMỗi biến thể chỉ chạy OS1 một lần, kết quả được lưu cố định.`)) return;
    btn.disabled = true;
    const r = await api("/api/os1", { items: [...sel.values()] });
    if (!r.ok) { alert(r.error || "Lỗi"); upBtn(); return; }
    const sk = (r.skipped || []).map((x) => `job ${x.job_id} run ${x.run_id}: ${x.reason}`);
    if (sk.length) alert(`Đã nộp ${r.created.length} biến thể.\nBỏ qua:\n` + sk.join("\n"));
    location.hash = "#/os1";
  });
  main.replaceChildren(h("h2", {}, "Alpha pass IS"),
    h("div", { class: "card" }, h("p", { class: "muted" }, "Các biến thể đã pass tiêu chí IS (Sharpe IS > 1, Sharpe 3 năm gần nhất > 0.5, ",
      "thời gian giữ lệnh 1–70%, fitness > 1, không bị stop-out). Chọn biến thể rồi bấm nộp để chạy OS1 (2024–2026). ",
      "Mỗi biến thể chỉ chạy OS1 một lần; OS1 chạy đúng bản alpha và cấu hình đã pass IS."),
      h("div", { class: "row" }, btn, info)), box);
  const r = await api(`/api/ispass?limit=${PER}&offset=${(page - 1) * PER}`);
  const pages = Math.max(1, Math.ceil((r.total || 0) / PER));
  if (page > pages && r.total) { go(pages); return; }
  info.textContent = `${r.total} biến thể pass IS`;
  const pager = pages > 1 ? h("div", { class: "pager" },
    h("button", { class: "btn", disabled: page <= 1, on: { click: () => go(page - 1) } }, "‹ Mới hơn"),
    h("span", { class: "muted" }, `Trang ${page}/${pages}`),
    h("button", { class: "btn", disabled: page >= pages, on: { click: () => go(page + 1) } }, "Cũ hơn ›")) : null;
  box.replaceChildren(...(r.rows.length ? [pager, h("table", {},
    h("tr", {}, h("th"), h("th", {}, "Job"), h("th", {}, "Alpha"), h("th", {}, "Symbol"), h("th", {}, "Khung"), h("th", {}, "Tham số"),
      h("th", { class: "n" }, "Sharpe IS"), h("th", { class: "n" }, "Sharpe 3 năm"), h("th", { class: "n" }, "Fitness"), h("th", { class: "n" }, "PF"),
      h("th", { class: "n" }, "DD %"), h("th", { class: "n" }, "Lệnh"), h("th", {}, "OS1")),
    r.rows.map((v) => {
      const can = !v.os1 || v.os1.status === "failed" || v.os1.status === "cancelled";
      const key = v.job_id + ":" + v.run_id;
      const cb = h("input", { type: "checkbox", disabled: !can, on: { change: (e) => { if (e.target.checked) sel.set(key, { job_id: v.job_id, run_id: v.run_id }); else sel.delete(key); upBtn(); } } });
      const keys = v.opt_keys && v.opt_keys.length ? v.opt_keys : Object.keys(v.params);
      return h("tr", {}, h("td", {}, cb), h("td", {}, h("a", { href: "#/job/" + v.job_id }, "#" + v.job_id), h("span", { class: "muted" }, " run " + v.run_id)),
        h("td", { class: "mono" }, v.alpha), h("td", {}, v.symbol), h("td", {}, v.period),
        h("td", { class: "mono small" }, keys.map((k) => `${k}=${v.params[k]}`).join(" ")),
        h("td", { class: "n" }, fmt(v.sharpe, 3)), h("td", { class: "n" }, fmt(v.recent_sharpe, 3)), h("td", { class: "n" }, fmt(v.fitness, 3)),
        h("td", { class: "n" }, fmt(v.pf, 2)), h("td", { class: "n" }, fmt(v.dd_pct, 2)), h("td", { class: "n" }, v.trades), h("td", {}, os1Tag(v.os1)));
    })), pager].filter(Boolean)
    : [h("p", { class: "muted" }, "Chưa có biến thể nào pass IS. Chạy optimize IS rồi quay lại trang này.")]));
}

async function pageOs1(main, page) {
  const PER = 20;
  const box = h("div", { class: "card" }, h("p", { class: "muted" }, "Đang tải…"));
  const go = (p) => { location.hash = p > 1 ? "#/os1/" + p : "#/os1"; };
  main.replaceChildren(h("h2", {}, "Lịch sử chạy OS1"), box);
  let last = "", active = false;
  async function load() {
    const r = await api(`/api/jobs?kind=os1&limit=${PER}&offset=${(page - 1) * PER}`);
    const pages = Math.max(1, Math.ceil((r.total || 0) / PER));
    if (page > pages && r.total) { go(pages); return; }
    active = r.jobs.some((j) => ["queued", "dispatched", "running"].includes(j.status));
    const key = JSON.stringify(r);
    if (key === last) return;
    last = key;
    const pager = pages > 1 ? h("div", { class: "pager" },
      h("button", { class: "btn", disabled: page <= 1, on: { click: () => go(page - 1) } }, "‹ Mới hơn"),
      h("span", { class: "muted" }, `Trang ${page}/${pages} · ${r.total} lần chạy`),
      h("button", { class: "btn", disabled: page >= pages, on: { click: () => go(page + 1) } }, "Cũ hơn ›")) : null;
    box.replaceChildren(...(r.jobs.length ? [pager, h("table", {},
      h("tr", {}, h("th", {}, "#"), h("th", {}, "Alpha"), h("th", {}, "Nguồn IS"), h("th", {}, "Trạng thái"), h("th", { class: "n" }, "Sharpe IS"),
        h("th", { class: "n" }, "Sharpe OS1"), h("th", { class: "n" }, "Lợi nhuận OS1"), h("th", {}, "Đỉnh mới"), h("th", {}, "Không DD mới"),
        h("th", {}, "Kết quả"), h("th", {}, "Tạo lúc (UTC)")),
      r.jobs.map((j) => {
        const o = (j.summary && j.summary.os1) || null;
        return h("tr", { class: "click", on: { click: () => { location.hash = "#/job/" + j.id; } } },
          h("td", {}, j.id), h("td", { class: "mono" }, j.alpha), h("td", {}, `job #${j.src_job_id} run ${j.src_run_id}`),
          h("td", {}, tag(j.status), j.status === "running" || j.status === "dispatched" ? " " + fmt(j.progress, 0) + "%" : "",
            j.status === "failed" ? h("div", { class: "err small" }, j.message || "") : null),
          h("td", { class: "n" }, o ? fmt(o.is_sharpe, 3) : "–"), h("td", { class: "n" }, o ? fmt(o.os_sharpe, 3) : "–"),
          h("td", { class: "n" }, o ? fmt(o.os_profit, 2) : "–"), h("td", {}, o ? yesNo(o.os_new_high) : "–"), h("td", {}, o ? yesNo(o.os_no_new_dd) : "–"),
          h("td", {}, o ? h("span", { class: "tag " + (o.os_pass ? "done" : "failed") }, o.os_pass ? "ĐẠT" : "KHÔNG ĐẠT") : "–"),
          h("td", { class: "muted" }, j.created));
      })), pager].filter(Boolean)
      : [h("p", { class: "muted" }, "Chưa chạy OS1 lần nào. Chọn biến thể ở trang ", h("a", { href: "#/ispass" }, "Alpha pass IS"), ".")]));
  }
  await load();
  let tick = 0;
  timers.push(setInterval(() => { tick++; if (active || tick % 5 === 0) load().catch(() => {}); }, 3000));
}

/* ---------- chi tiết job ---------- */
async function requestData(jobId, kind, runId, offset, statusEl) {
  statusEl.textContent = "Đang lấy từ data-server…";
  const r = await api(`/api/jobs/${jobId}/request`, { kind, run_id: runId, offset });
  if (!r.ok) { statusEl.textContent = r.error || "Lỗi"; return null; }
  for (let i = 0; i < 100; i++) {
    await new Promise((res) => setTimeout(res, 600));
    const q = await api("/api/requests/" + r.id);
    if (q.status === "done") { statusEl.textContent = ""; return q.result; }
    if (q.status === "timeout") { statusEl.textContent = "Data-server không phản hồi (offline?)."; return null; }
  }
  statusEl.textContent = "Quá thời gian chờ."; return null;
}

function chart(points) {
  const W = 900, Hh = 230, m = 36, ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${Hh}`); svg.setAttribute("class", "chart");
  if (points.length < 2) return svg;
  const vals = points.flatMap((p) => [p[1], p[2]]);
  const lo = Math.min(...vals), hi = Math.max(...vals), sp = hi - lo || 1;
  const x = (i) => m + (W - 2 * m) * i / (points.length - 1), y = (v) => Hh - m - (Hh - 2 * m) * (v - lo) / sp;
  const line = (col, idx) => { const pl = document.createElementNS(ns, "polyline"); pl.setAttribute("fill", "none"); pl.setAttribute("stroke", col); pl.setAttribute("stroke-width", "1.5");
    pl.setAttribute("points", points.map((p, i) => `${x(i).toFixed(1)},${y(p[idx]).toFixed(1)}`).join(" ")); return pl; };
  const txt = (t, xx, yy, anchor) => { const e = document.createElementNS(ns, "text"); e.setAttribute("x", xx); e.setAttribute("y", yy); e.setAttribute("font-size", "11"); e.setAttribute("fill", "#667085"); if (anchor) e.setAttribute("text-anchor", anchor); e.textContent = t; return e; };
  svg.append(line("#9aa7bd", 1), line("#1f6feb", 2), txt(fmt(hi, 0), 4, m), txt(fmt(lo, 0), 4, Hh - m), txt(points[0][0], m, Hh - 8), txt(points[points.length - 1][0], W - m, Hh - 8, "end"), txt("xanh: equity, xám: balance", W / 2, 14, "middle"));
  return svg;
}

async function pageJob(main, id) {
  const holder = h("div");
  main.replaceChildren(holder);
  let viewer = null;

  async function render() {
    const { job } = await api("/api/jobs/" + id);
    const c = job.config;
    const live = job.status === "queued" || job.status === "dispatched" || job.status === "running";
    const head = h("div", { class: "card" },
      h("h2", {}, `Job #${job.id} · `, h("span", { class: "mono" }, job.alpha), " ", tag(job.status)),
      live ? h("div", { class: "bar" }, (() => { const i = h("i"); i.style.width = Math.max(2, job.progress || 0) + "%"; return i; })()) : null,
      job.notice ? h("p", { class: "warn" }, job.notice) : null,
      h("p", { class: job.status === "failed" ? "err" : "muted" }, job.message || ""),
      (job.status === "failed" || job.status === "cancelled") ? h("button", { class: "btn primary", on: { click: async (ev) => {
        ev.target.disabled = true;
        const r = c.type === "os1" ? await api("/api/os1", { items: [{ job_id: c.src_job_id, run_id: c.src_run_id }] }) : await api("/api/jobs", c);
        if (r.ok && c.type === "os1") { if (r.created && r.created.length) location.hash = "#/job/" + r.created[0]; else { ev.target.disabled = false; alert((r.skipped || []).map((x) => x.reason).join("\n") || "Không nộp được"); } return; }
        if (r.ok) location.hash = "#/job/" + r.id;
        else { ev.target.disabled = false; alert((r.error || "Lỗi") + (r.details ? "\n" + r.details.join("\n") : "")); }
      } } }, "Chạy lại job này") : null,
      c.type === "os1" ? os1Head(job) : [
      h("p", {}, `${c.type} · ${c.symbols_mode} · ${c.symbols.length} symbol (${c.symbols.slice(0, 6).join(", ")}${c.symbols.length > 6 ? "…" : ""}) · ${c.period} · ${c.fromdate} → ${c.todate} · swap ${c.swap_mode} · ${c.variants} biến thể`),
      h("p", { class: "mono muted" }, Object.entries(c.params).filter(([, v]) => v[1] > 0).map(([k, v]) => `${k}: ${v[0]}→${v[2]} step ${v[1]}`).join("  |  ") || "(không có lưới)")],
      live ? h("button", { class: "btn danger", on: { click: async () => { await api(`/api/jobs/${id}/cancel`, {}); render(); } } }, "Hủy job") : null,
      !live ? h("a", { class: "btn", href: "#/ask?job=" + job.id }, "Hỏi AI về job này") : null,
      job.summary && c.type !== "os1" ? h("p", {}, `Kết quả: ${job.summary.variants} biến thể, ${job.summary.is_pass} pass IS, ${job.summary.minutes} phút. Thư mục báo cáo trên data-server: ${job.summary.report || ""}`) : null);
    const ver = job.summary && job.summary.report ? String(job.summary.report).split(/[\/]/).pop() : "";
    const eng = job.summary ? engineText(job.summary.engine) : "";
    const slim = job.status === "done" && job.summary ? h("div", { class: "vwbar" }, h("span", { class: "mono" }, job.alpha),
      c.type === "offline" ? h("span", { class: "tag" }, "offline · " + (job.source || job.summary.source || "")) : null,
      h("span", { class: "mono" }, ver), h("span", { class: "muted", title: (job.summary.engine && job.summary.engine.commit) || "" }, eng),
      h("span", {}, `${job.summary.variants} run`)) : null;
    if (job.status === "done" && !viewer) viewer = buildViewer(id, job);
    main.classList.toggle("wide", job.status === "done");
    const doneNote = job.status === "done" && job.notice ? h("div", { class: "banner warnbox" }, job.notice) : null;
    holder.replaceChildren(...[c.type === "os1" && job.status === "done" ? head : (slim || head), doneNote, job.status === "done" ? viewer : null].filter(Boolean));
    if (!live) clearTimers();
  }
  await render();
  timers.push(setInterval(() => render().catch(() => {}), 2500));
}

/* ---------- giao diện kết quả (theo bố cục Rust Quant Viewer) ---------- */
const vfmt = (v, d = 3) => {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("en-US", { maximumFractionDigits: Math.abs(n) >= 1000 ? 2 : d });
};
const vtone = (v) => { const n = Number(v); return Number.isFinite(n) && n !== 0 ? (n > 0 ? "pos" : "neg") : ""; };
const vkey = (k) => String(k || "").trim().toLowerCase().replace(/^inp_?/, "").replace(/^input_?/, "");
const vkv = (obj) => Object.entries(obj).map(([k, v]) => h("div", { class: "vwkvrow" }, h("span", {}, k), h("strong", {}, vfmt(v))));
const vpanel = (title, ...kids) => h("section", { class: "vwp" }, h("div", { class: "vwhead" }, h("h3", {}, title)), ...kids);
const METRIC_NAMES = ["net_profit", "custom_sharpe", "fitness", "profit_factor", "expected_payoff", "reward_risk", "recovery_factor",
  "sharpe_ratio", "gross_profit", "gross_loss", "balance_dd", "equity_dd", "max_drawdown", "max_drawdown_pct", "winrate", "trades", "deals",
  "profit_trades", "loss_trades", "long_trades", "short_trades", "long_win_pct", "short_win_pct", "max_profit_trade", "max_loss_trade", "con_profit_max_money", "con_loss_max_money", "time_in_trade_percent",
  "time_in_loss_percent", "average_trade_hours", "min_marginlevel", "account_leverage", "margin_call_level", "stopout_level",
  "stopped_out", "stopout_time", "stopout_closes", "rejected_orders", "history_quality", "bars", "ticks",
  "os_pass", "os_sharpe", "is_sharpe", "os_profit", "os_new_high", "os_no_new_dd", "os_max_dd", "is_max_dd", "os_dd_pct",
  "os_trades", "os_winrate", "os_profit_factor", "os_gross_profit", "os_gross_loss", "os_days", "os_note"];

function drawEquity(canvas, pts) {
  // vẽ theo kích thước hiển thị thật x độ nét màn hình (điện thoại, Z Fold gập/mở)
  canvas._pts = pts;
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth || 900, H = canvas.clientHeight || 300, pad = W < 480 ? 30 : 38;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) { canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#fbfcfd"; ctx.fillRect(0, 0, W, H);
  ctx.font = "12px Segoe UI";
  if (!pts.length) { ctx.fillStyle = "#68717d"; ctx.fillText("Không có dữ liệu equity", 20, 30); return; }
  const vals = pts.map((r) => Number(r[2]));
  const lo = Math.min(...vals), hi = Math.max(...vals), sp = hi - lo || 1;
  ctx.strokeStyle = "#d9dee5"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) { const y = pad + (H - pad * 2) * i / 4; ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(W - pad, y); ctx.stroke(); }
  ctx.strokeStyle = "#116a7b"; ctx.lineWidth = 2; ctx.beginPath();
  pts.forEach((r, i) => {
    const x = pad + (W - pad * 2) * i / Math.max(1, pts.length - 1), y = H - pad - (Number(r[2]) - lo) / sp * (H - pad * 2);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = "#68717d";
  ctx.fillText(`max ${vfmt(hi, 2)}`, pad, 18); ctx.fillText(`min ${vfmt(lo, 2)}`, pad, H - 10);
  ctx.fillText(String(pts[0][0]), Math.max(pad + 90, W - pad - 170), H - 10); ctx.textAlign = "right"; ctx.fillText(String(pts[pts.length - 1][0]), W - pad, H - 10); ctx.textAlign = "left";
}

function buildViewer(id, job) {
  const c = job.config;
  const optKeys = new Set(Object.entries(c.params || {}).filter(([, v]) => v[1] > 0).map(([k]) => vkey(k)));
  const S = { sort: "fitness", dir: "desc", pass: false, traded: false, q: "", symbol: "", offset: 0, sel: null, rows: [], total: 0, page: 100, token: 0 };

  // --- cột trái: bộ lọc + danh sách run ---
  const sortSel = h("select", { on: { change: (e) => { S.sort = e.target.value; S.offset = 0; loadRuns(); } } },
    [["fitness", "Fitness"], ["sharpe", "Custom Sharpe"], ["net_profit", "Profit"], ["pf", "Profit Factor"], ["recovery", "Recovery Factor"],
     ["dd", "Drawdown %"], ["winrate", "Winrate"], ["tit", "Time in trade"], ["trades", "Trades"], ["recent", "Sharpe 3 năm"], ["run_id", "Run ID"]]
      .map(([v, l]) => h("option", { value: v }, l)));
  const dirSel = h("select", { on: { change: (e) => { S.dir = e.target.value; S.offset = 0; loadRuns(); } } },
    h("option", { value: "desc" }, "Desc"), h("option", { value: "asc" }, "Asc"));
  const passCb = h("input", { type: "checkbox", on: { change: (e) => { S.pass = e.target.checked; S.offset = 0; loadRuns(); } } });
  let tmr = null;
  const reload = () => { clearTimeout(tmr); tmr = setTimeout(() => { S.offset = 0; loadRuns(); }, 300); };
  const searchIn = h("input", { type: "search", placeholder: "Run ID, symbol, tham số", on: { input: (e) => { S.q = e.target.value; reload(); } } });
  const symSel = h("select", { on: { change: (e) => { S.symbol = e.target.value; S.offset = 0; loadRuns(); } } }, h("option", { value: "" }, "Tất cả symbol"));
  const symWrap = h("label", {}, "Symbol", symSel);
  symWrap.style.display = "none";
  const tradedCb = h("input", { type: "checkbox", on: { change: (e) => { S.traded = e.target.checked; S.offset = 0; loadRuns(); } } });
  // khoảng giá trị cho các tham số được tối ưu (bước > 0), như mục Optimized inputs của Rust Quant Viewer
  const optParams = Object.entries(c.params || {}).filter(([, v]) => Array.isArray(v) && v[1] > 0);
  const pRanges = optParams.map(([k, v]) => ({ k, min: h("input", { type: "number", step: "any", placeholder: "Min " + v[0], on: { input: reload } }),
    max: h("input", { type: "number", step: "any", placeholder: "Max " + v[2], on: { input: reload } }) }));
  const pfPayload = () => pRanges.map((r) => ({ key: r.k, min: r.min.value, max: r.max.value })).filter((r) => r.min !== "" || r.max !== "");
  const clearBtn = h("button", { class: "vwtoggle", on: { click: () => { pRanges.forEach((r) => { r.min.value = ""; r.max.value = ""; }); S.offset = 0; loadRuns(); } } }, "Clear");
  const paramBox = pRanges.length ? h("div", { class: "vwparams" }, h("div", { class: "vwhead" }, h("strong", {}, "Tham số tối ưu"), clearBtn),
    pRanges.map((r) => h("div", { class: "vwprange" }, h("span", { class: "mono" }, r.k), h("div", {}, r.min, r.max)))) : null;
  const filterBody = h("div", {}, h("label", {}, "Tìm", searchIn), symWrap, paramBox,
    h("label", {}, "Sort by", sortSel), h("label", {}, "Direction", dirSel),
    h("label", { class: "chk" }, passCb, " Chỉ biến thể pass IS"), h("label", { class: "chk" }, tradedCb, " Chỉ biến thể có lệnh"));
  const toggleBtn = h("button", { class: "vwtoggle", on: { click: () => { filterBody.hidden = !filterBody.hidden; toggleBtn.textContent = filterBody.hidden ? "Show" : "Hide"; } } }, "Hide");
  const runCount = h("span", { class: "muted" }, "0");
  const runList = h("div", { class: "vwruns" });
  const pager = h("div", { class: "pager" });

  function renderRuns() {
    runCount.textContent = `${S.rows.length}/${S.total}`;
    runList.replaceChildren(...(S.rows.length ? S.rows.map((v) => h("div", { class: "vwrun" + (v.run_id === S.sel ? " active" : ""), on: { click: () => selectRun(v) } },
      h("strong", {}, `${v.symbol} ${c.period}`),
      h("div", { class: "vwmeta" }, h("span", {}, Object.entries(v.params).filter(([k]) => optKeys.has(vkey(k))).map(([k, x]) => `${k}=${typeof x === "number" ? +x.toPrecision(10) : x}`).join(" ") || "-"), h("span", {}, `run ${v.run_id}${v.is_pass ? " ✓" : ""}`)),
      v.stopped_out ? h("div", { class: "vwmeta" }, h("span", { class: "tag failed", title: "Margin level xuống dưới mức stop-out: engine đóng lệnh và dừng biến thể này" }, "STOP-OUT")) : null,
      h("div", { class: "vwmeta" }, h("span", {}, `Sharpe ${vfmt(v.sharpe)}`), h("span", {}, `PF ${vfmt(v.pf)}`), h("span", {}, `P ${vfmt(v.net_profit, 2)}`))))
      : [h("p", { class: "muted" }, "Không có kết quả.")]));
    const end = Math.min(S.offset + S.page, S.total);
    pager.replaceChildren(
      h("button", { class: "btn", disabled: S.offset === 0, on: { click: () => { S.offset = Math.max(0, S.offset - S.page); loadRuns(); } } }, "‹"),
      h("span", { class: "muted" }, S.total ? `${S.offset + 1}–${end} / ${S.total}` : "0"),
      h("button", { class: "btn", disabled: end >= S.total, on: { click: () => { S.offset += S.page; loadRuns(); } } }, "›"));
  }
  async function loadRuns() {
    const qs = new URLSearchParams({ sort: S.sort, dir: S.dir, offset: S.offset, pass: S.pass ? 1 : 0, traded: S.traded ? 1 : 0 });
    if (S.q.trim()) qs.set("q", S.q.trim());
    if (S.symbol) qs.set("symbol", S.symbol);
    const pf = pfPayload();
    if (pf.length) qs.set("pf", JSON.stringify(pf));
    const r = await api(`/api/jobs/${id}/variants?${qs}`);
    S.rows = r.rows; S.total = r.total; S.page = r.page;
    if (symSel.options.length === 1 && (r.symbols || []).length > 1) { symSel.append(...r.symbols.map((x) => h("option", { value: x }, x))); symWrap.style.display = ""; }
    renderRuns();
    if (S.sel === null && S.rows.length) selectRun(S.rows[0]);
  }

  // --- cột giữa: thẻ chỉ số, equity, lệnh ---
  const runTitle = h("h3", {}, "Chọn một run"), runSub = h("p", { class: "muted" }, "Chọn một run trong danh sách bên trái.");
  const cards = h("div", { class: "vwcards" });
  const eqInfo = h("span", { class: "muted" });
  const canvas = h("canvas", { class: "vwc", width: "1200", height: "360" });
  const tradeInfo = h("span", { class: "muted" }), tradeBtn = h("button", { class: "btn", disabled: true }, "Load trades");
  const tradeBox = h("div", { class: "vwtw" });
  const tradePager = h("div", { class: "pager" });
  // --- cột phải ---
  const inputsBox = h("div", { class: "vwkv" }), testerBox = h("div", { class: "vwkv" }), metricsBox = h("div", { class: "vwkv" });

  async function selectRun(v) {
    S.sel = v.run_id; const tok = ++S.token;
    renderRuns();
    runTitle.textContent = `${v.symbol} ${c.period} | run ${v.run_id}`;
    runSub.textContent = `${job.alpha} | job #${id}`;
    tradeBtn.disabled = false; tradeBox.replaceChildren(); tradePager.replaceChildren(); tradeInfo.textContent = "";
    tradeBtn.onclick = () => loadTrades(v.run_id, 0);
    cards.replaceChildren(); inputsBox.replaceChildren(); testerBox.replaceChildren(); metricsBox.replaceChildren();
    const st = h("p", { class: "muted" });
    eqInfo.textContent = "Đang lấy từ data-server…";
    // chi tiết (metrics/tester/inputs) và equity: lấy theo yêu cầu từ data-server
    const [det, eq] = await Promise.all([requestData(id, "detail", v.run_id, 0, st), requestData(id, "equity", v.run_id, 0, st)]);
    if (tok !== S.token) return;   // người dùng đã chọn run khác
    if (det && det.ok) {
      const m = det.metrics, optP = Object.entries(det.parameters || {}).filter(([k]) => optKeys.has(vkey(k)));
      const items = [["Custom Sharpe", m.custom_sharpe, true], ["Fitness", m.fitness, true], ["Profit", m.net_profit, true], ["Equity DD", m.equity_dd ?? m.max_drawdown, false],
        ["Time In Trade %", m.time_in_trade_percent, false], ["Total Trade", m.trades, false], ["Profit Factor", m.profit_factor, true], ...optP.map(([k, x]) => [k, x, false])];
      cards.replaceChildren(...items.map(([l, x, sg]) => h("div", { class: "vwcard" }, h("span", {}, l), h("strong", { class: sg ? vtone(x) : "" }, vfmt(x)))));
      inputsBox.replaceChildren(...vkv(det.parameters || {}));
      const tt = Object.assign({}, det.tester || {});
      if (String(tt.symbol || "").toUpperCase().startsWith("PORTFOLIO") && (c.symbols || []).length) tt.symbol = c.symbols.join(", ");
      testerBox.replaceChildren(...vkv(tt));
      metricsBox.replaceChildren(...vkv(Object.fromEntries(METRIC_NAMES.filter((n) => m[n] !== undefined).map((n) => [n, m[n]]))));
    } else {
      cards.replaceChildren(h("p", { class: "err" }, (det && det.error) || "Không lấy được chi tiết (data-server offline?)"));
    }
    if (eq && eq.ok) { drawEquity(canvas, eq.points); eqInfo.textContent = `${eq.points.length}/${eq.total_points} points`; }
    else { drawEquity(canvas, []); eqInfo.textContent = (eq && eq.error) || "Không có equity"; }
    st.textContent = "";
  }

  async function loadTrades(runId, offset) {
    const st = h("span", { class: "muted" });
    tradeInfo.replaceChildren(st);
    const res = await requestData(id, "trades", runId, offset, st);
    if (!res) return;
    if (!res.ok) { tradeBox.replaceChildren(h("p", { class: "err" }, res.error || "Lỗi")); return; }
    const end = res.offset + res.rows.length;
    tradeInfo.textContent = `${res.rows.length}/${res.total}`;
    tradePager.replaceChildren(
      h("button", { class: "btn", disabled: res.offset === 0, on: { click: () => loadTrades(runId, Math.max(0, res.offset - 100)) } }, "‹ Trước"),
      h("span", { class: "muted" }, res.total ? `${res.offset + 1}–${end} / ${res.total}` : "0"),
      h("button", { class: "btn", disabled: end >= res.total, on: { click: () => loadTrades(runId, res.offset + 100) } }, "Sau ›"));
    if (!res.rows.length) { tradeBox.replaceChildren(h("p", { class: "muted pad8" }, "Không có trades")); return; }
    const cols = ["trade_id", "symbol", "side", "open_time_ms", "open_price", "close_time_ms", "close_price", "lots", "profit", "commission", "reason_open", "reason_close"];
    tradeBox.replaceChildren(h("table", { class: "vwt" }, h("thead", {}, h("tr", {}, cols.map((x) => h("th", {}, x)))),
      h("tbody", {}, res.rows.map((r) => h("tr", {}, cols.map((x) => h("td", { class: x === "profit" ? vtone(r[x]) : "" },
        x.endsWith("_ms") ? dt(r[x]) : (typeof r[x] === "number" ? vfmt(r[x], 5) : (r[x] ?? "-")))))))));
  }

  const el = h("div", { class: "vw" },
    h("aside", { class: "vwside" },
      h("section", { class: "vwp" }, h("div", { class: "vwhead" }, h("h3", {}, "Filters"), toggleBtn), filterBody),
      h("section", { class: "vwp vwrunsp" }, h("div", { class: "vwhead" }, h("h3", {}, "Runs"), runCount), runList, pager)),
    h("section", { class: "vwmain" },
      h("section", { class: "vwp" }, h("div", { class: "vwhead" }, h("div", {}, runTitle, runSub)), cards),
      h("section", { class: "vwp" }, h("div", { class: "vwhead" }, h("h3", {}, "Daily Equity"), eqInfo), canvas),
      h("section", { class: "vwp" }, h("div", { class: "vwhead" }, h("h3", {}, "Trades Preview"), h("div", {}, tradeInfo, " ", tradeBtn)), tradeBox, tradePager)),
    h("aside", { class: "vwdet" }, vpanel("Inputs", inputsBox), vpanel("Tester", testerBox), vpanel("Metrics", metricsBox)));
  drawEquity(canvas, []);
  loadRuns().catch((e) => runList.replaceChildren(h("p", { class: "err" }, String(e))));
  return el;
}

/* khoi dong tu dong (CSP chan script nhung trong HTML) */
if (location.hash === "#/guide_ai") location.replace("/guide_ai");   // trang hướng dẫn công khai, không cần đăng nhập
else if (document.getElementById("f")) QC_LOGIN();
else if (document.getElementById("app")) QC_APP();

/* ---------- điện thoại: bảng -> thẻ (gắn nhãn cột cho từng ô), vẽ lại biểu đồ khi đổi kích thước ---------- */
function labelTables(root) {
  root.querySelectorAll("table:not(.vwt):not(.cards)").forEach((t) => {
    const rows = [...t.querySelectorAll("tr")];
    const hr = rows.find((r) => r.querySelector("th"));
    if (!hr) return;
    const labels = [...hr.children].map((c) => c.textContent.trim());
    hr.classList.add("thr");
    rows.forEach((r) => { if (r !== hr) [...r.children].forEach((td, i) => {
      if (td.tagName !== "TD") return;
      td.dataset.label = labels[i] || "";
      if (!td.children.length && !td.textContent.trim()) td.classList.add("emp");   // điện thoại: ẩn ô trống
    }); });
    t.classList.add("cards");
  });
}
let _lblPending = false;
new MutationObserver(() => {
  if (_lblPending) return;
  _lblPending = true;
  requestAnimationFrame(() => { _lblPending = false; labelTables(document); });
}).observe(document.documentElement, { childList: true, subtree: true });
let _rsz = null;
window.addEventListener("resize", () => {
  clearTimeout(_rsz);
  _rsz = setTimeout(() => document.querySelectorAll("canvas.vwc").forEach((c) => { if (c._pts) drawEquity(c, c._pts); }), 150);
});
