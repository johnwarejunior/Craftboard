// Shared helpers for every Craftboard page.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const money = cents => cents == null ? "–" : "$" + (Math.round(cents) % 100 && Math.abs(cents) < 100000
  ? (cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : Math.round(cents / 100).toLocaleString("en-US"));
const AV = ["#E8428C","#2C5BE8","#138A5E","#8A4FE0","#D9731A","#1B8FA6","#B83A63","#4B5BD6"];
const avatar = h => `<span class="avatar" style="background:${AV[[...(h||"?")].reduce((a,c)=>a+c.charCodeAt(0),0)%AV.length]}" aria-hidden="true">${esc((h||"?")[0].toUpperCase())}</span>`;
const ago = ts => { const s = Date.now()/1000 - ts; if (s < 3600) return Math.max(1, Math.round(s/60)) + "m ago"; if (s < 86400) return Math.round(s/3600) + "h ago"; return Math.round(s/86400) + "d ago"; };

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body || opts.form ? "POST" : "GET"), credentials: "same-origin", headers: {} };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401 && !opts.noRedirect) { location.href = "/login"; throw new Error("Sign in to continue."); }
  if (!res.ok) {
    const d = data && data.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map(x => (x.loc ? x.loc.slice(-1)[0] + ": " : "") + x.msg).join("; ") : "Something went wrong (" + res.status + ").";
    const err = new Error(msg); err.status = res.status; throw err;
  }
  return data;
}

let toastTimer;
function toast(msg) {
  let t = $("#toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 3600);
}

const FLAG_NAMES = { vague: "Vague", red: "Red flag", ok: "Clear", info: "Scope" };
function flagList(flags, names = FLAG_NAMES) {
  return `<ul class="flags">${flags.map(f => `<li class="${f.kind}"><b>${names[f.kind]}</b><span>${esc(f.message)}</span></li>`).join("")}</ul>`;
}
function slotsHtml(filled, total) {
  return `<div class="slots" role="img" aria-label="${filled} of ${total} slots filled">${Array.from({length: total}, (_, i) => `<span class="slot ${i < filled ? "full" : ""}"></span>`).join("")}</div>`;
}
function briefHtml(b, meta = "") {
  return `<div class="brief"><div class="lockline"><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><rect x="2" y="5" width="8" height="6" rx="1.5" fill="currentColor"/><path d="M4 5V3.5a2 2 0 0 1 4 0V5" stroke="currentColor" fill="none" stroke-width="1.4"/></svg>Brief locked on acceptance</div>
    <p style="margin-top:8px">${esc(b.description)}</p>
    <dl>${meta}<dt>Characters</dt><dd>${b.characters ?? 1}</dd><dt>Background</dt><dd>${esc(b.background || "None")}</dd>
    <dt>Usage</dt><dd>${esc(b.usage || "Personal")}</dd>${b.deadline ? `<dt>Needed by</dt><dd>${esc(b.deadline)}</dd>` : ""}
    <dt>References</dt><dd>${(b.references || []).length ? b.references.map(r => esc(r)).join(", ") : "<span class='muted'>None</span>"}</dd></dl></div>`;
}
const STAGES = [["awaiting_deposit","Deposit"],["queued","Queued"],["in_progress","In progress"],["in_review","Client review"],["approved","Approved"],["delivered","Delivered"],["completed","Paid"]];
function timelineHtml(status) {
  const i = STAGES.findIndex(s => s[0] === status);
  return `<div class="timeline">${STAGES.map(([k, n], j) => `<span class="${j < i ? "done" : j === i ? "now" : ""}">${n}</span>`).join("")}</div>`;
}
function revMeterHtml(used, total) {
  return `<span class="revmeter" aria-label="${used} of ${total} revisions used">${Array.from({length: total}, (_, i) => `<span class="${i < used ? "used" : ""}"></span>`).join("")}</span>`;
}
