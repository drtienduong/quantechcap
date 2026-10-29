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
    nav.replaceChildren(...[["#/", "Alpha"], ["#/submit", "Nộp alpha"], ["#/jobs", "Lịch sử chạy"]].map(([href, label]) =>
      h("a", { href, class: (cur === href || (href === "#/jobs" && cur.startsWith("#/job"))) ? "active" : "" }, label)));
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
      else if (hash === "#/jobs") await pageJobs(main);
      else if (hash === "#/submit") await pageSubmit(main);
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
      : h("p", { class: "muted" }, "Chưa có alpha. Viết alpha theo bộ khung rồi nộp ở trang ", h("a", { href: "#/submit" }, "Nộp alpha"), ".")),
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
  const nameIn = h("input", { type: "text", size: 50, placeholder: "alpha_y_tuong" + suffix });
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
    if (!nameIn.value) nameIn.value = f.name.replace(/\.rs$/i, "");
    info.append(h("p", { class: "muted" }, `${f.name}: ${f.size} byte, ${srcText.split("\n").length} dòng`));
  });
  const btn = h("button", { class: "btn primary", on: { click: async () => {
    info.replaceChildren();
    if (!srcText) { info.append(h("p", { class: "err" }, "Chọn file .rs trước.")); return; }
    const name = nameIn.value.trim();
    if (!name.endsWith(suffix)) { info.append(h("p", { class: "err" }, `Tên phải kết thúc bằng ${suffix}`)); return; }
    btn.disabled = true;
    const j = await api("/api/submissions", { name, source: srcText });
    btn.disabled = false;
    if (!j.ok) { info.append(h("p", { class: "err" }, j.error || "Lỗi")); return; }
    info.append(h("p", { class: "ok" }, `Đã nộp (#${j.id}). Data-server sẽ kiểm tra trong vài giây.`));
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
      h("p", { class: "muted mono" }, `sha256 ${sb.sha256 || ""} · ${sb.size || 0} byte · nộp ${sb.created} UTC`)));
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
      h("div", { class: "row" }, h("label", {}, "File .rs", h("br"), fileIn), h("label", {}, `Tên alpha (kết thúc bằng ${suffix})`, h("br"), nameIn), btn),
      info,
      h("p", { class: "muted" }, "Nộp lại cùng tên = phiên bản mới. Web chỉ giữ mã tạm thời đến khi data-server nhận về.")),
    h("div", { class: "card" }, h("h3", {}, "Bộ khung alpha cho AI của bạn"),
      h("p", {}, "Tải bộ khung, giải nén, đưa toàn bộ cho AI của bạn (Claude, ChatGPT…) kèm ý tưởng giao dịch. Yêu cầu AI đọc AI_GUIDE.md trước."),
      h("a", { href: "/kit/quantechcap_alpha_kit.zip", download: "quantechcap_alpha_kit.zip", class: "btn primary" }, "Tải bộ khung (.zip)"),
      h("p", { class: "muted" }, "Gồm: ", ST.kit.join(", "), "."),
      h("p", { class: "muted" }, "Alpha chỉ được tính toán trên dữ liệu engine đưa vào; không đọc file, mạng, dữ liệu ngoài. Vi phạm sẽ bị từ chối tự động.")),
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
  const isEnd = lim.is_end;
  const f = {
    type: h("select", {}, h("option", { value: "optimize_is" }, "Optimize trên IS"), h("option", { value: "smoke" }, "Smoke test (nhanh, giới hạn tick)")),
    mode: h("select", {}, h("option", { value: "portfolio" }, "Portfolio (chung 1 tài khoản)"), h("option", { value: "independent" }, "Từng symbol riêng")),
    period: h("select", {}, ST.periods.map((p) => h("option", { value: p, selected: p === ((a.meta && a.meta.required_period) || "H1") }, p))),
    from: h("input", { type: "text", value: "2010.01.01", size: 10 }), to: h("input", { type: "text", value: isEnd, size: 10 }),
    deposit: h("input", { type: "number", value: 10000 }), leverage: h("input", { type: "text", value: "1:100", size: 7 }),
    swap: h("select", {}, h("option", { value: "zero" }, "zero (không swap)"), h("option", { value: "synthetic" }, "synthetic")),
    lot: h("input", { type: "number", value: 0.01, step: "any" }), pv: h("input", { type: "number", value: 100000 }),
    comm: h("input", { type: "number", value: 0, step: "any" }), ticks: h("input", { type: "number", value: 200000 }),
  };
  const boxes = ST.fx25.map((s) => h("input", { type: "checkbox", value: s, checked: s === "EURUSD" }));
  const symLabels = boxes.map((b) => h("label", { class: "sym" }, b, b.value));
  const setSyms = (list) => boxes.forEach((b) => { b.checked = list.includes(b.value); });
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
      h("label", {}, "Từ ngày", h("br"), f.from), h("label", {}, "Đến ngày (≤ " + isEnd + ")", h("br"), f.to), ticksWrap),
      periodWarn,
      h("p", { class: "muted" }, "Job IS bị ép đến ", isEnd, ". OS1/OS2 không chạy được ở bản thử này. Smoke: tối đa ",
        lim.smoke_max_days, " ngày, ", lim.smoke_max_symbols, " symbol, được ưu tiên chạy trước các job optimize đang chờ.")),
    h("div", { class: "card" }, h("h3", {}, "Symbol"), h("div", { class: "row" }, h("label", {}, "Chế độ", h("br"), f.mode),
      h("button", { class: "btn", on: { click: () => setSyms(["EURUSD"]) } }, "Chỉ EURUSD"),
      h("button", { class: "btn", on: { click: () => setSyms(ST.fx25.slice(0, 20)) } }, "20 cặp"),
      h("button", { class: "btn", on: { click: () => setSyms(ST.fx25) } }, "25 cặp"),
      h("button", { class: "btn", on: { click: () => setSyms([]) } }, "Bỏ hết")),
      h("div", { class: "symbox" }, symLabels),
      h("p", { class: "warn" }, "Lưu ý: alpha dùng thông tin tài khoản (…_with_account) chỉ giao dịch ở chế độ Portfolio; chế độ \"Từng symbol riêng\" có thể ra 0 lệnh.")),
    h("div", { class: "card" }, h("h3", {}, "Tài khoản"), h("div", { class: "row" },
      h("label", {}, "Deposit", h("br"), f.deposit), h("label", {}, "Leverage", h("br"), f.leverage), h("label", {}, "Swap", h("br"), f.swap),
      h("label", {}, "Lot", h("br"), f.lot), h("label", {}, "Point value/lot", h("br"), f.pv), h("label", {}, "Commission/lot/deal", h("br"), f.comm))),
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

async function pageJobs(main) {
  async function load() {
    const r = await api("/api/jobs?limit=50");
    main.replaceChildren(h("h2", {}, "Lịch sử chạy"), h("div", { class: "card" }, r.jobs.length ? h("table", {},
      h("tr", {}, h("th", {}, "#"), h("th", {}, "Alpha"), h("th", {}, "Loại"), h("th", {}, "Trạng thái"), h("th", {}, "Tiến độ"), h("th", {}, "Ghi chú"), h("th", {}, "Tạo lúc (UTC)")),
      r.jobs.map((j) => h("tr", { class: "click", on: { click: () => { location.hash = "#/job/" + j.id; } } },
        h("td", {}, j.id), h("td", { class: "mono" }, j.alpha), h("td", {}, j.type), h("td", {}, tag(j.status)),
        h("td", {}, j.status === "running" || j.status === "dispatched" ? fmt(j.progress, 0) + "%" : (j.summary ? j.summary.variants + " biến thể" : "")),
        h("td", { class: j.status === "failed" ? "err small" : (j.notice ? "warn small" : "muted small") }, j.notice || (j.status === "done" ? "" : j.message || "")),
        h("td", { class: "muted" }, j.created)))
    ) : h("p", { class: "muted" }, "Chưa có job.")));
  }
  await load();
  timers.push(setInterval(() => load().catch(() => {}), 3000));
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
        const r = await api("/api/jobs", c);
        if (r.ok) location.hash = "#/job/" + r.id;
        else { ev.target.disabled = false; alert((r.error || "Lỗi") + (r.details ? "\n" + r.details.join("\n") : "")); }
      } } }, "Chạy lại job này") : null,
      h("p", {}, `${c.type} · ${c.symbols_mode} · ${c.symbols.length} symbol (${c.symbols.slice(0, 6).join(", ")}${c.symbols.length > 6 ? "…" : ""}) · ${c.period} · ${c.fromdate} → ${c.todate} · swap ${c.swap_mode} · ${c.variants} biến thể`),
      h("p", { class: "mono muted" }, Object.entries(c.params).filter(([, v]) => v[1] > 0).map(([k, v]) => `${k}: ${v[0]}→${v[2]} step ${v[1]}`).join("  |  ") || "(không có lưới)"),
      live ? h("button", { class: "btn danger", on: { click: async () => { await api(`/api/jobs/${id}/cancel`, {}); render(); } } }, "Hủy job") : null,
      job.summary ? h("p", {}, `Kết quả: ${job.summary.variants} biến thể, ${job.summary.is_pass} pass IS, ${job.summary.minutes} phút. Thư mục báo cáo trên data-server: ${job.summary.report || ""}`) : null);
    const ver = job.summary && job.summary.report ? String(job.summary.report).split(/[\/]/).pop() : "";
    const slim = job.status === "done" && job.summary ? h("div", { class: "vwbar" }, h("span", { class: "mono" }, job.alpha), h("span", { class: "mono" }, ver), h("span", {}, `${job.summary.variants} run`)) : null;
    if (job.status === "done" && !viewer) viewer = buildViewer(id, job);
    main.classList.toggle("wide", job.status === "done");
    const doneNote = job.status === "done" && job.notice ? h("div", { class: "banner warnbox" }, job.notice) : null;
    holder.replaceChildren(slim || head, doneNote, job.status === "done" ? viewer : null);
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
  "time_in_loss_percent", "average_trade_hours", "min_marginlevel", "history_quality", "bars", "ticks"];

function drawEquity(canvas, pts) {
  const ctx = canvas.getContext("2d"), W = canvas.width, H = canvas.height, pad = 38;
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
  ctx.fillText(String(pts[0][0]), W - pad - 170, H - 10); ctx.textAlign = "right"; ctx.fillText(String(pts[pts.length - 1][0]), W - pad, H - 10); ctx.textAlign = "left";
}

function buildViewer(id, job) {
  const c = job.config;
  const optKeys = new Set(Object.entries(c.params || {}).filter(([, v]) => v[1] > 0).map(([k]) => vkey(k)));
  const S = { sort: "fitness", dir: "desc", pass: false, offset: 0, sel: null, rows: [], total: 0, page: 100, token: 0 };

  // --- cột trái: bộ lọc + danh sách run ---
  const sortSel = h("select", { on: { change: (e) => { S.sort = e.target.value; S.offset = 0; loadRuns(); } } },
    [["fitness", "Fitness"], ["sharpe", "Custom Sharpe"], ["net_profit", "Profit"], ["dd", "Drawdown %"], ["trades", "Trades"], ["recent", "Sharpe 3 năm"], ["run_id", "Run ID"]]
      .map(([v, l]) => h("option", { value: v }, l)));
  const dirSel = h("select", { on: { change: (e) => { S.dir = e.target.value; S.offset = 0; loadRuns(); } } },
    h("option", { value: "desc" }, "Desc"), h("option", { value: "asc" }, "Asc"));
  const passCb = h("input", { type: "checkbox", on: { change: (e) => { S.pass = e.target.checked; S.offset = 0; loadRuns(); } } });
  const filterBody = h("div", {}, h("label", {}, "Sort by", sortSel), h("label", {}, "Direction", dirSel), h("label", { class: "chk" }, passCb, " Chỉ biến thể pass IS"));
  const toggleBtn = h("button", { class: "vwtoggle", on: { click: () => { filterBody.hidden = !filterBody.hidden; toggleBtn.textContent = filterBody.hidden ? "Show" : "Hide"; } } }, "Hide");
  const runCount = h("span", { class: "muted" }, "0");
  const runList = h("div", { class: "vwruns" });
  const pager = h("div", { class: "pager" });

  function renderRuns() {
    runCount.textContent = `${S.rows.length}/${S.total}`;
    runList.replaceChildren(...(S.rows.length ? S.rows.map((v) => h("div", { class: "vwrun" + (v.run_id === S.sel ? " active" : ""), on: { click: () => selectRun(v) } },
      h("strong", {}, `${v.symbol} ${c.period}`),
      h("div", { class: "vwmeta" }, h("span", {}, Object.entries(v.params).filter(([k]) => optKeys.has(vkey(k))).map(([k, x]) => `${k}=${x}`).join(" ") || "-"), h("span", {}, `run ${v.run_id}${v.is_pass ? " ✓" : ""}`)),
      h("div", { class: "vwmeta" }, h("span", {}, `Sharpe ${vfmt(v.sharpe)}`), h("span", {}, `PF ${vfmt(v.pf)}`), h("span", {}, `P ${vfmt(v.net_profit, 2)}`))))
      : [h("p", { class: "muted" }, "Không có kết quả.")]));
    const end = Math.min(S.offset + S.page, S.total);
    pager.replaceChildren(
      h("button", { class: "btn", disabled: S.offset === 0, on: { click: () => { S.offset = Math.max(0, S.offset - S.page); loadRuns(); } } }, "‹"),
      h("span", { class: "muted" }, S.total ? `${S.offset + 1}–${end} / ${S.total}` : "0"),
      h("button", { class: "btn", disabled: end >= S.total, on: { click: () => { S.offset += S.page; loadRuns(); } } }, "›"));
  }
  async function loadRuns() {
    const r = await api(`/api/jobs/${id}/variants?sort=${S.sort}&dir=${S.dir}&offset=${S.offset}&pass=${S.pass ? 1 : 0}`);
    S.rows = r.rows; S.total = r.total; S.page = r.page;
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
if (document.getElementById("f")) QC_LOGIN();
else if (document.getElementById("app")) QC_APP();
