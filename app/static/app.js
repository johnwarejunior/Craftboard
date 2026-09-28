// Craftboard creator app: queue, requests, waitlist, goal agent, insights, settings.
const S = { me: null, dash: null, tab: (location.hash || "#queue").slice(1), open: null };
const TABS = [["queue", "Queue"], ["requests", "Requests"], ["waitlist", "Waitlist"], ["agent", "Goal agent"], ["insights", "Insights"], ["settings", "Settings"]];
const STATUS = {
  awaiting_deposit: ["sun", "Awaiting deposit"], queued: ["blue", "Queued"], in_progress: ["pink", "In progress"],
  in_review: ["sun", "Client review"], approved: ["green", "Approved"], delivered: ["green", "Awaiting balance"], completed: ["green", "Paid"],
};
const PLAN_NEED = { waitlist: "Studio", analytics: "Studio", goal_agent: "Pro", claude_screening: "Pro" };
const feat = f => S.me && S.me.features[f];
const cx = n => `<span class="cx" aria-label="${n} of 5">${[1, 2, 3, 4, 5].map(i => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</span> <span class="muted">${n}/5</span>`;
const gate = f => `<div class="empty"><p style="margin-bottom:8px"><b>This needs the ${PLAN_NEED[f]} plan.</b></p>
  <p class="muted">You're on ${esc(S.me.plan)}. Plans are flat monthly prices with no cut of your sales. In this MVP an admin switches plans with <code>python -m app.cli set-plan ${esc(S.me.email)} ${PLAN_NEED[f].toLowerCase()}</code>.</p></div>`;

async function copy(text, msg = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(msg); } catch (e) { prompt("Copy this:", text); }
}

// ------------------------------------------------------------------ chrome
async function refresh() {
  [S.me, S.dash] = await Promise.all([api("/api/me"), api("/api/dashboard")]);
  renderChrome();
}
function renderChrome() {
  const i = S.dash.intake, open = i.open;
  $("#planBadge").textContent = S.me.plan + " plan";
  $("#intakeLink").href = "/c/" + S.me.handle;
  $("#hero").innerHTML = `
    <div>
      <h1>${open ? "Commissions open" : "Commissions closed"}</h1>
      <p class="sub">${i.filled} of ${i.slots} slots filled. ${S.me.intake_mode === "auto"
        ? (open ? "Intake closes on its own when the last slot fills." : (i.waitlist_enabled ? "New requests join the waitlist automatically." : "Your form shows as closed until a slot opens."))
        : `Intake is set to always ${S.me.intake_mode}.`}</p>
      ${slotsHtml(i.filled, i.slots)}
    </div>
    <div class="hero-side">
      <span class="status-pill ${open ? "open" : "closed"}">${open ? "Taking requests" : i.waitlist_enabled ? "Waitlist only" : "Closed"}</span>
      <div class="wl"><strong>${i.waitlist}</strong>on the waitlist</div>
      <div class="seg" role="group" aria-label="Intake mode">${["auto", "open", "closed"].map(m =>
        `<button data-action="mode" data-m="${m}" aria-pressed="${S.me.intake_mode === m}">${m[0].toUpperCase() + m.slice(1)}</button>`).join("")}</div>
    </div>`;
  const counts = { requests: S.dash.pending_requests, waitlist: i.waitlist_enabled ? i.waitlist : 0 };
  $("#tabs").innerHTML = TABS.map(([id, l]) => `<button role="tab" aria-selected="${S.tab === id}" data-action="tab" data-t="${id}">${l}${counts[id] ? `<span class="count">${counts[id]}</span>` : ""}</button>`).join("");
}

async function render() {
  const views = { queue: viewQueue, requests: viewRequests, waitlist: viewWaitlist, agent: viewAgent, insights: viewInsights, settings: viewSettings };
  try { $("#main").innerHTML = await (views[S.tab] || viewQueue)(); }
  catch (e) { $("#main").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; }
}
async function reload() { await refresh(); await render(); }

// ------------------------------------------------------------------ queue
function statusLabel(c) {
  if (c.status === "in_progress" && c.revisions_used) return `Revision ${c.revisions_used} of ${c.revisions_included}`;
  return STATUS[c.status][1];
}
function detail(c) {
  let actions = "";
  if (c.status === "awaiting_deposit") actions = `<span class="small muted" style="align-self:center">Waiting on the ${money(c.deposit_cents)} deposit.</span>
    <button class="btn ghost sm" data-action="markPaid" data-id="${c.id}">Mark deposit paid</button>`;
  else if (c.status === "queued") actions = `<button class="btn sm" data-action="start" data-id="${c.id}">Start work</button>`;
  else if (c.status === "in_progress") actions = `<div class="inline"><label class="f small">Hours<input type="number" min="0.25" step="0.25" id="hrs-${c.id}" style="width:90px"></label>
    <button class="btn ghost sm" data-action="hours" data-id="${c.id}">Log hours</button></div>
    <button class="btn sm" data-action="wip" data-id="${c.id}">Send WIP to client</button>`;
  else if (c.status === "in_review") actions = `<span class="small muted" style="align-self:center">Waiting on @${esc(c.client)} to approve or ask for changes in their portal.</span>`;
  else if (c.status === "approved") actions = `<div class="inline"><label class="f small">Final files<input type="file" multiple id="files-${c.id}"></label>
    <label class="f small">Hours this stage<input type="number" min="0" step="0.25" id="dhrs-${c.id}" style="width:90px"></label></div>
    <button class="btn pink sm" data-action="deliver" data-id="${c.id}">Deliver and request ${money(c.balance_cents)}</button>`;
  else if (c.status === "delivered") actions = `<span class="small muted" style="align-self:center">Delivered. Files unlock for the client once the ${money(c.balance_cents)} balance is paid.</span>
    <button class="btn ghost sm" data-action="markPaid" data-id="${c.id}">Mark balance paid</button>`;
  const paid = c.payments.filter(p => p.status === "paid");
  const revs = c.revision_requests.map(r => {
    const tone = r.kind === "revision" ? "info" : "red";
    const label = r.kind === "revision" ? "Revision" : r.kind === "paid_revision" ? "Paid revision" : "Upgrade";
    const st = r.kind === "revision" ? "counted" : `${money(r.quote_cents)} · ${r.status}`;
    return `<li class="${tone}"><b>${label}</b><span>${esc(r.text)} <span class="muted">(${st}${r.reasons.length ? ": " + esc(r.reasons.join(", ")) : ""})</span></span></li>`;
  }).join("");
  const meta = `<dt>Type</dt><dd>${esc(c.type || "–")}</dd><dt>Price</dt><dd>${money(c.price_cents)}${c.upgrades_cents ? ` <span class="muted">incl. ${money(c.upgrades_cents)} in upgrades</span>` : ""}</dd><dt>Hours logged</dt><dd>${c.hours_logged || 0}</dd>`;
  return `<div class="detail">
    ${timelineHtml(c.status)}
    ${briefHtml(c.brief, meta)}
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap" class="small">Revisions ${revMeterHtml(c.revisions_used, c.revisions_included)}
      <span class="muted">${c.revisions_used} of ${c.revisions_included} used</span><span class="spacer"></span>
      ${paid.map(p => `<span class="tag green">${p.kind === "deposit" ? "Deposit" : "Balance"} paid · ${money(p.amount_cents)}</span>`).join(" ")}</div>
    ${revs ? `<ul class="flags">${revs}</ul>` : ""}
    ${c.files.length ? `<p class="small muted">Delivered: ${c.files.map(f => esc(f.filename)).join(", ")}</p>` : ""}
    <div class="row-actions">${actions}</div>
    <div class="link-row small"><span class="muted">Client portal</span><code>${esc(location.origin)}/p/${esc(c.portal_token)}</code>
      <button class="btn ghost sm" data-action="copyPortal" data-token="${esc(c.portal_token)}">Copy link</button>
      <span class="spacer"></span><button class="linkbtn small" data-action="cancel" data-id="${c.id}">Cancel commission</button></div>
  </div>`;
}
async function viewQueue() {
  const d = S.dash;
  const items = d.queue.map(c => `<li>
      <button class="qitem" aria-expanded="${S.open === c.id}" data-action="toggle" data-id="${c.id}">
        ${avatar(c.client)}
        <span class="qmeta"><span class="who">@${esc(c.client)}</span><span class="what">${esc(c.type || c.title)} · ${money(c.price_cents)}</span></span>
        <span class="qstat"><span class="tag ${STATUS[c.status][0]}">${statusLabel(c)}</span></span>
      </button>${S.open === c.id ? detail(c) : ""}</li>`).join("");
  const setup = !d.has_types ? `<p class="notice" style="margin-bottom:16px">Add your commission types in <a href="#settings" data-action="tab" data-t="settings">Settings</a> so clients can request work from your link.</p>` : "";
  return `${setup}<div class="grid2">
    <section>
      <div class="sectionhead"><h2>Your queue</h2><span class="muted small">Oldest first. Select one to work on it.</span></div>
      ${d.queue.length ? `<ul class="qlist">${items}</ul>` : `<div class="empty">Your queue is empty. Share your intake link, then accept a request to fill a slot.</div>`}
    </section>
    <aside class="panel">
      <div class="sectionhead"><h2>Last 30 days</h2></div>
      <div class="stats" style="grid-template-columns:1fr 1fr;margin-bottom:14px">
        <div class="stat"><div class="v">${money(d.last_30_revenue_cents)}</div><div class="l">delivered</div></div>
        <div class="stat"><div class="v">${d.effective_hourly_cents ? money(d.effective_hourly_cents) : "–"}</div><div class="l">per hour, effective</div></div>
      </div>
      ${d.recent.length ? `<p class="small muted" style="margin-bottom:8px">Recently completed</p>
      <ul class="flags">${d.recent.map(x => `<li><span style="flex:1">@${esc(x.client)} · ${esc(x.type || "")}</span><span class="muted">${money(x.price_cents)}</span></li>`).join("")}</ul>` : ""}
      <div class="divider"></div>
      <p class="small muted" style="margin-bottom:8px">Share this link in your bio, Discord or email:</p>
      <div class="link-row"><code>${esc(location.origin)}/c/${esc(S.me.handle)}</code><button class="btn ghost sm" data-action="copyIntake">Copy</button></div>
    </aside>
  </div>`;
}

// ------------------------------------------------------------------ requests
async function viewRequests() {
  const { requests, intake, claude_available } = await api("/api/requests");
  if (!requests.length) return `<div class="empty">No requests waiting. New briefs from your link land here, already screened.</div>`;
  const list = requests.map(r => {
    const s = r.screening, b = r.brief;
    const verdict = s.verdict === "red" ? ["red", "Red flags"] : s.verdict === "vague" ? ["sun", "Needs detail"] : ["green", "Ready"];
    const cl = r.claude ? `<div class="claude-out"><b>Claude's read (${esc(r.claude.risk)} risk):</b> ${esc(r.claude.summary)}${r.claude.questions.length ? `<ul>${r.claude.questions.map(q => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}</div>` : "";
    return `<article class="req">
      <div style="display:flex;gap:12px;align-items:center">${avatar(r.client)}<div style="flex:1;min-width:0"><div style="font-weight:600">@${esc(r.client)}</div>
        <div class="small muted">${esc(r.type_name)} · from ${money(r.base_price_cents)} · ${ago(r.created_at)}</div></div>
        ${r.status === "info_requested" ? `<span class="tag blue">Asked for details</span>` : `<span class="tag ${verdict[0]}">${verdict[1]}</span>`}</div>
      <p style="font-size:15px">${esc(b.description)}</p>
      <p class="small muted">${b.characters} character${b.characters > 1 ? "s" : ""} · background: ${esc(b.background)} · ${esc(b.usage)} use${b.deadline ? " · needed by " + esc(b.deadline) : ""}${b.references.length ? ` · ${b.references.length} reference${b.references.length > 1 ? "s" : ""}` : ""}</p>
      <div class="small">Complexity ${cx(s.complexity)}</div>
      ${flagList(s.flags)}
      ${r.status === "info_requested" && r.creator_note ? `<p class="small notice">You asked: ${esc(r.creator_note)}</p>` : ""}
      ${cl}<div id="ask-${r.id}"></div>
      <div class="row-actions">
        <div class="inline"><label class="f small">Price override<input type="number" min="0" step="any" id="price-${r.id}" placeholder="auto" style="width:100px"></label>
        <button class="btn sm" data-action="accept" data-id="${r.id}" ${intake.filled >= intake.slots ? "disabled" : ""}>Accept and lock brief</button></div>
        ${r.status === "pending" && s.verdict === "vague" ? `<button class="btn ghost sm" data-action="ask" data-id="${r.id}">Ask for details</button>` : ""}
        <button class="btn ghost sm" data-action="decline" data-id="${r.id}">Decline</button>
        ${feat("claude_screening") && claude_available ? `<button class="btn ghost sm" data-action="claude" data-id="${r.id}">Second read from Claude</button>` : ""}
      </div>
    </article>`;
  }).join("");
  return `<div class="grid2"><section class="panel" style="padding:0">${list}</section>
    <aside>
      <div class="sectionhead"><h2>How screening works</h2></div>
      <p class="muted" style="margin-bottom:10px">Every brief is checked when it arrives: vague details, red flags (unpaid "exposure", discount asks, rush timelines, pay-later, NFT/AI-training use, copying another artist, commercial use on a personal license) and a complexity score against the commission type.</p>
      <p class="muted">Accepting locks the brief, fills a slot, prices extra characters from your type settings, and sends the client to the deposit. Leave the override blank to use the automatic price.</p>
      ${intake.filled >= intake.slots ? `<p class="tag sun" style="margin-top:14px">All slots are full. Deliver a commission or add a slot to accept more.</p>` : ""}
    </aside></div>`;
}

// ------------------------------------------------------------------ waitlist
async function viewWaitlist() {
  if (!feat("waitlist")) return gate("waitlist");
  const list = await api("/api/waitlist");
  if (!list.length) return `<div class="empty">Nobody's waiting. When your slots are full, new requests join this list on their own.</div>`;
  return `<section class="panel"><div class="sectionhead"><h2>Waitlist</h2><span class="small muted">First in, first invited</span></div>
    <div id="inviteOut"></div>
    <div class="scroll-x"><table class="plain"><thead><tr><th>Client</th><th>Wants</th><th>Note</th><th>Joined</th><th></th></tr></thead><tbody>
    ${list.map(w => `<tr><td>@${esc(w.client)}${w.status === "invited" ? ` <span class="tag green">invited</span>` : ""}</td><td>${esc(w.type_name || "–")}</td>
      <td class="small">${esc((w.note || "").slice(0, 90))}</td><td class="small muted">${ago(w.created_at)}</td>
      <td style="white-space:nowrap"><button class="btn sm" data-action="invite" data-id="${w.id}">Invite</button> <button class="btn ghost sm" data-action="unwait" data-id="${w.id}">Remove</button></td></tr>`).join("")}
    </tbody></table></div></section>`;
}

// ------------------------------------------------------------------ goal agent
async function viewAgent() {
  if (!feat("goal_agent")) return gate("goal_agent");
  const { goal, claude_available } = await api("/api/goals/active");
  const p = goal && goal.plan;
  const form = `<section class="panel" style="margin-bottom:22px">
    <div class="sectionhead"><h2>${goal ? "Set a new goal" : "Set a goal"}</h2><span class="small muted">The pricing engine works backward from your own history.</span></div>
    <form id="goalForm" class="inline" novalidate>
      <label class="f">Revenue goal ($)<input type="number" name="amount" min="1" step="any" value="${p ? p.goal_cents / 100 : 5000}" style="width:130px"></label>
      <label class="f">In weeks<input type="number" name="weeks" min="1" max="52" value="${goal ? p.weeks_requested : 4}" style="width:90px"></label>
      <label class="f">Hours a week<input type="number" name="hours" min="1" max="100" value="${goal ? p.hours_per_week : S.me.hours_per_week}" style="width:100px"></label>
      <button class="btn" type="submit">Build my plan</button>
    </form></section>`;
  const steps = `<div class="steps" aria-label="How the goal agent works">${["Set a goal", "Read your history", "Build a plan", "Check in weekly"].map((s, i) => `<div class="${goal || i === 0 ? "on" : ""}">${s}</div>`).join("")}</div>`;
  if (!goal) return steps + form;
  const ck = goal.checkin, rec = p.recommended;
  return `${steps}
  <section class="panel" style="margin-bottom:22px">
    <p class="small muted">Your plan</p>
    <p class="bigline">${esc(p.headline)}</p>
    ${p.insights.map(x => `<div class="insight"><h3>${esc(x.label)}</h3><p>${esc(x.text)}</p></div>`).join("")}
  </section>
  <div class="grid2" style="margin-bottom:22px">
    <section class="panel">
      <div class="sectionhead"><h2>Price options</h2><span class="small muted">Capacity ${p.safe_per_month}/month at a safe pace</span></div>
      <div class="scroll-x"><table class="opts"><thead><tr><th>Average price</th><th>Commissions</th><th>Per month</th><th>Fits?</th></tr></thead><tbody>
      ${p.options.map(o => `<tr class="${o.raise_pct === rec.raise_pct ? "pick" : ""}"><td>${money(o.avg_price_cents)}${o.raise_pct ? ` <span class="muted small">+${o.raise_pct}%</span>` : ""}</td>
        <td>${o.commissions}</td><td>${o.per_month}</td><td>${o.fits ? (o.risky ? `<span class="tag sun">Risky raise</span>` : `<span class="tag green">Yes</span>`) : `<span class="tag red">Over capacity</span>`}</td></tr>`).join("")}
      </tbody></table></div>
      ${p.extended ? `<p class="small muted" style="margin-top:10px">No price fits ${p.weeks_requested} week${p.weeks_requested === 1 ? "" : "s"} at a safe pace, so the plan stretches to ${p.plan_weeks} weeks at ${money(rec.avg_price_cents)}: about ${Math.round(p.per_month)} a month.</p>` : ""}
    </section>
    <section class="panel">
      <div class="sectionhead"><h2>Weekly check-in</h2><span class="small muted">Week ${ck.week} of ${ck.of_weeks}</span></div>
      <div class="progress" aria-label="${ck.percent}% of goal"><i style="width:${ck.percent}%"></i></div>
      <p style="margin:10px 0"><b>${money(ck.earned_cents)}</b> of ${money(p.goal_cents)} · <span class="tag ${ck.on_track ? "green" : "sun"}">${ck.on_track ? "On track" : `${money(ck.gap_cents)} behind`}</span></p>
      <p class="small muted" style="margin-bottom:10px">${ck.commissions_remaining} more commissions at ${money(rec.avg_price_cents)}, about ${ck.per_week_needed} a week.</p>
      <div class="weeks">${p.milestones.map(m => `<div class="week ${ck.earned_cents >= m.revenue_cents ? "hit" : ""}">Week ${m.week}<b>${money(m.revenue_cents)}</b>${m.commissions} done</div>`).join("")}</div>
    </section>
  </div>
  <section class="panel" style="margin-bottom:22px">
    <div class="sectionhead"><h2>Talk it through</h2><span class="small muted">Claude explains the plan. It doesn't change the numbers.</span></div>
    ${goal.narrative ? `<div class="claude-out pre">${esc(goal.narrative)}</div>` : `<p class="muted">${claude_available ? "No explanation yet." : "Add ANTHROPIC_API_KEY to your .env to have Claude explain the plan in plain words."}</p>`}
    <div class="row-actions" style="margin-top:12px">
      ${claude_available ? `<button class="btn ghost sm" data-action="narrate" data-id="${goal.id}">${goal.narrative ? "Explain again" : "Explain this plan"}</button>` : ""}
      <button class="linkbtn small" data-action="endGoal" data-id="${goal.id}">End this goal</button>
    </div>
  </section>
  ${form}`;
}

// ------------------------------------------------------------------ insights
function barChart(values, labels) {
  const W = 560, H = 200, pad = 28, bw = (W - pad * 2) / values.length, max = Math.max(...values, 1);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${labels.map((l, i) => l + ": " + money(values[i])).join(", ")}">
    ${values.map((v, i) => { const h = (H - pad * 2) * (v / max), x = pad + i * bw + bw * 0.18, y = H - pad - h;
      return `<rect x="${x}" y="${y}" width="${bw * 0.64}" height="${h}" rx="3" fill="var(--pink)" opacity="${i === values.length - 1 ? 1 : .55}"/>
      <text x="${x + bw * 0.32}" y="${y - 6}" text-anchor="middle">${money(v)}</text><text x="${x + bw * 0.32}" y="${H - 8}" text-anchor="middle">${labels[i]}</text>`; }).join("")}
  </svg>`;
}
async function historyTable(limit = 25) {
  const h = await api("/api/history");
  if (!h.length) return `<div class="empty">No completed commissions yet. Import past work from a spreadsheet in Settings to get useful numbers from day one.</div>`;
  return `<div class="scroll-x"><table class="plain"><thead><tr><th>Completed</th><th>Client</th><th>Type</th><th>Price</th><th>Hours</th><th>Revisions</th></tr></thead><tbody>
    ${h.slice(0, limit).map(x => `<tr><td class="small">${new Date(x.completed_at * 1000).toLocaleDateString()}</td><td>@${esc(x.client_handle)}</td><td>${esc(x.type_name || "–")}</td>
      <td>${money(x.price_cents)}</td><td>${x.hours_logged || "–"}</td><td>${x.revisions_used}</td></tr>`).join("")}
    </tbody></table></div>${h.length > limit ? `<p class="small muted" style="margin-top:8px">Showing ${limit} of ${h.length}. Export the full history from Settings.</p>` : ""}`;
}
async function viewInsights() {
  const hist = `<section class="panel" style="margin-top:28px"><div class="sectionhead"><h2>Commission history</h2></div>${await historyTable()}</section>`;
  if (!feat("analytics")) return gate("analytics") + hist;
  const a = await api("/api/analytics");
  if (!a.delivered_180d) return `<div class="empty">Insights appear once you've completed commissions. Import past work in Settings to start with real numbers.</div>` + hist;
  const pc = a.pace, lvl = { high: ["red", "High"], rising: ["sun", "Rising"], low: ["green", "Low"] }[pc.level];
  const maxE = Math.max(...a.by_type.map(t => t.effective_hourly_cents || 0), 1);
  const labels = ["150–180d", "120–150d", "90–120d", "60–90d", "30–60d", "Last 30d"];
  const withHours = a.by_type.filter(t => t.effective_hourly_cents);
  const best = withHours[0], worst = withHours[withHours.length - 1];
  return `<div class="stats">
    <div class="stat"><div class="v">${a.effective_hourly_cents ? money(a.effective_hourly_cents) + "/h" : "–"}</div><div class="l">effective hourly rate</div></div>
    <div class="stat"><div class="v">${money(a.avg_price_cents)}</div><div class="l">average commission</div></div>
    <div class="stat"><div class="v">${Math.round(a.repeat_revenue_share * 100)}%</div><div class="l">revenue from repeat clients</div></div>
    <div class="stat"><div class="v">${a.delivered_180d}</div><div class="l">delivered in 180 days</div></div>
  </div>
  <div class="grid2">
    <section class="panel"><div class="sectionhead"><h2>Revenue by 30-day stretch</h2></div>${barChart(a.revenue_by_30d_cents, labels)}</section>
    <section class="panel">
      <div class="sectionhead"><h2>Burnout risk</h2><span class="tag ${lvl[0]}">${lvl[1]}</span></div>
      <div class="gauge" aria-label="Risk ${pc.burnout_risk} of 100"><i style="left:${pc.burnout_risk}%"></i></div>
      <p class="small muted" style="display:flex;justify-content:space-between"><span>Rested</span><span>Overcommitted</span></p>
      <div class="divider"></div>
      <ul class="flags small">
        <li><span style="flex:1">Last 30 days vs. your average</span><b style="background:none;color:var(--ink)">${pc.pace_ratio >= 1 ? "+" : ""}${Math.round((pc.pace_ratio - 1) * 100)}%</b></li>
        <li><span style="flex:1">Median turnaround, recent vs. before</span><b style="background:none;color:var(--ink)">${a.turnaround_recent_days ? Math.round(a.turnaround_recent_days) + "d" : "–"} vs ${a.turnaround_before_days ? Math.round(a.turnaround_before_days) + "d" : "–"}</b></li>
        <li><span style="flex:1">Hours of work in your queue</span><b style="background:none;color:var(--ink)">${pc.active_hours}h</b></li>
      </ul>
      ${pc.overloaded ? `<p class="notice" style="margin-top:12px">You're working faster than your normal pace. Consider closing intake for two weeks or adding a rush fee.</p>` : ""}
    </section>
  </div>
  <div class="grid2" style="margin-top:28px">
    <section class="panel"><div class="sectionhead"><h2>What each type really pays</h2></div>
      ${withHours.map(t => `<div class="hbar"><span>${esc(t.type)}</span><span class="track"><i style="width:${t.effective_hourly_cents / maxE * 100}%"></i></span><b style="text-align:right">${money(t.effective_hourly_cents)}/h</b></div>`).join("") || `<p class="muted">Log hours on commissions to see this.</p>`}
      ${best && worst && best !== worst ? `<p class="small muted" style="margin-top:10px">${esc(best.type)} earns ${money(best.effective_hourly_cents)} an hour; ${esc(worst.type.toLowerCase())} earns ${money(worst.effective_hourly_cents)}.</p>` : ""}
      <div class="divider"></div>
      <p class="small muted">Revisions per commission: ${a.by_type.map(t => `${esc(t.type)} ${t.avg_revisions}`).join(", ")}. Scope-creep upgrades: ${a.by_type.map(t => `${esc(t.type)} ${Math.round(t.scope_creep_rate * 100)}%`).join(", ")}.</p>
    </section>
    <section class="panel"><div class="sectionhead"><h2>Top clients</h2><span class="small muted">180-day value</span></div>
      <ul class="flags">${a.top_clients.map(c => `<li style="align-items:center">${avatar(c.client)}<span style="flex:1">@${esc(c.client)} <span class="muted small">${c.commissions} commission${c.commissions > 1 ? "s" : ""}</span></span><b style="background:none;color:var(--ink);font-size:15px">${money(c.revenue_cents)}</b></li>`).join("")}</ul>
    </section>
  </div>${hist}`;
}

// ------------------------------------------------------------------ settings
async function viewSettings() {
  const me = S.me, types = await api("/api/types");
  const num = (name, label, val, attrs = "") => `<label class="f">${label}<input type="number" name="${name}" value="${val}" ${attrs}></label>`;
  return `<div class="grid2">
  <section class="panel">
    <div class="sectionhead"><h2>Studio settings</h2></div>
    <form id="profileForm" class="fields" novalidate>
      <label class="f">Display name<input type="text" name="name" value="${esc(me.name)}"></label>
      <div class="two">${num("slots", "Commission slots", me.slots, 'min="1" max="30"')}${num("revisions_included", "Revisions included", me.revisions_included, 'min="0" max="10"')}</div>
      <div class="two">${num("deposit_pct", "Deposit on acceptance (%)", me.deposit_pct, 'min="0" max="100"')}${num("hours_per_week", "Hours you work a week", me.hours_per_week, 'min="1" max="100" step="0.5"')}</div>
      <label class="f">Intake mode<select name="intake_mode">${[["auto", "Auto: open while slots are free"], ["open", "Always open"], ["closed", "Closed"]].map(([v, l]) => `<option value="${v}" ${me.intake_mode === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="f">Message on your intake form<textarea name="intake_message" rows="3" maxlength="1000">${esc(me.intake_message || "")}</textarea></label>
      ${me.features.max_active ? `<p class="small muted">The ${esc(me.plan)} plan allows ${me.features.max_active} active commissions at a time.</p>` : ""}
      <button class="btn" type="submit">Save settings</button>
    </form>
  </section>
  <section class="panel">
    <div class="sectionhead"><h2>Commission types</h2><span class="small muted">Price changes are recorded for the goal agent</span></div>
    ${types.length ? `<div class="scroll-x"><table class="plain"><thead><tr><th>Name</th><th>Price ($)</th><th>Est. hours</th><th>Active</th><th></th></tr></thead><tbody>
      ${types.map(t => `<tr><td>${esc(t.name)}</td><td><input type="number" min="0" step="any" id="tp-${t.id}" value="${t.base_price_cents / 100}"></td>
        <td><input type="number" min="0.25" step="0.25" id="th-${t.id}" value="${t.est_hours}"></td>
        <td><input type="checkbox" id="ta-${t.id}" ${t.active ? "checked" : ""} aria-label="Active"></td>
        <td><button class="btn ghost sm" data-action="saveType" data-id="${t.id}">Save</button></td></tr>`).join("")}
    </tbody></table></div>` : `<p class="muted" style="margin-bottom:12px">No types yet. Add what you sell below.</p>`}
    <div class="divider"></div>
    <form id="typeForm" class="fields" novalidate>
      <div class="two"><label class="f">Name<input type="text" name="name" placeholder="Bust portrait" required></label>${num("base_price", "Base price ($)", 95, 'min="0" step="any"')}</div>
      <label class="f">Description<input type="text" name="description" placeholder="Shoulders up, flat colour, simple background"></label>
      <div class="two">${num("est_hours", "Est. hours", 3.5, 'min="0.25" step="0.25"')}${num("characters_included", "Characters included", 1, 'min="1" max="20"')}</div>
      ${num("extra_character_pct", "Each extra character adds (%)", 60, 'min="0" max="500"')}
      <button class="btn" type="submit">Add type</button>
    </form>
  </section>
  </div>
  <div class="grid2" style="margin-top:28px">
  <section class="panel">
    <div class="sectionhead"><h2>Import past commissions</h2></div>
    <p class="small muted" style="margin-bottom:10px">Upload a CSV with columns <code>client, type, price, completed_at</code> (YYYY-MM-DD). Optional: <code>hours, accepted_at, revisions</code>. Your history powers insights and the goal agent from day one.</p>
    <form id="csvForm" class="inline" novalidate><input type="file" name="file" accept=".csv,text/csv" required><button class="btn sm" type="submit">Import</button></form>
    <div id="csvOut" style="margin-top:10px"></div>
    <div class="divider"></div>
    <div class="sectionhead"><h2>Export</h2></div>
    <p class="small muted" style="margin-bottom:10px">Your client list and earnings are yours. Download them anytime.</p>
    <button class="btn ghost sm" data-action="export">Download history (CSV)</button>
  </section>
  <section class="panel">
    <div class="sectionhead"><h2>Payments</h2></div>
    <p class="small muted" style="margin-bottom:10px">Clients pay you directly through Stripe Connect. Craftboard adds no fee on top; Stripe's standard card processing applies.</p>
    ${me.stripe_account_id ? `<p class="okbox" style="margin-bottom:10px">Stripe account connected</p>` : ""}
    <button class="btn sm" data-action="stripe">${me.stripe_account_id ? "Finish or update Stripe setup" : "Connect Stripe"}</button>
    <div class="divider"></div>
    <div class="sectionhead"><h2>Account</h2></div>
    <dl class="kv"><dt>Email</dt><dd>${esc(me.email)}</dd><dt>Handle</dt><dd>@${esc(me.handle)}</dd><dt>Plan</dt><dd class="plan-badge">${esc(me.plan)}</dd></dl>
  </section></div>`;
}

// ------------------------------------------------------------------ actions
const ACTIONS = {
  tab: async el => { S.tab = el.dataset.t; history.replaceState(null, "", "#" + S.tab); renderChrome(); await render(); },
  toggle: async el => { const id = +el.dataset.id; S.open = S.open === id ? null : id; await render(); },
  mode: async el => { S.me = await api("/api/me", { method: "PATCH", body: { intake_mode: el.dataset.m } }); await reload(); },
  copyIntake: () => copy(location.origin + "/c/" + S.me.handle, "Intake link copied"),
  copyPortal: el => copy(location.origin + "/p/" + el.dataset.token, "Portal link copied. Send it to your client."),
  start: async el => { await api(`/api/commissions/${el.dataset.id}/start`, { method: "POST" }); toast("Started. Log hours as you go."); await reload(); },
  wip: async el => { await api(`/api/commissions/${el.dataset.id}/submit-wip`, { method: "POST" }); toast("WIP sent. The client can approve or ask for changes."); await reload(); },
  hours: async el => {
    const v = parseFloat($(`#hrs-${el.dataset.id}`).value);
    if (!(v > 0)) return toast("Enter the hours you worked.");
    await api(`/api/commissions/${el.dataset.id}/hours`, { body: { hours: v } }); toast(`Logged ${v}h`); await reload();
  },
  deliver: async el => {
    const id = el.dataset.id, files = $(`#files-${id}`).files, h = parseFloat($(`#dhrs-${id}`).value);
    if (!files.length) return toast("Attach the final files first.");
    const fd = new FormData(); [...files].forEach(f => fd.append("files", f)); if (h > 0) fd.append("hours", h);
    el.disabled = true;
    try { await api(`/api/commissions/${id}/deliver`, { form: fd }); toast("Delivered. Files unlock when the balance is paid."); await reload(); }
    finally { el.disabled = false; }
  },
  markPaid: async el => {
    if (!confirm("Record this payment as received outside Craftboard?")) return;
    await api(`/api/commissions/${el.dataset.id}/mark-paid`, { method: "POST" }); toast("Payment recorded"); await reload();
  },
  cancel: async el => {
    if (!confirm("Cancel this commission? It frees the slot and closes the client's portal.")) return;
    await api(`/api/commissions/${el.dataset.id}/cancel`, { method: "POST" }); S.open = null; toast("Cancelled"); await reload();
  },
  accept: async el => {
    const v = $(`#price-${el.dataset.id}`).value;
    const c = await api(`/api/requests/${el.dataset.id}/accept`, { body: { price: v === "" ? null : parseFloat(v) } });
    S.open = c.id; S.tab = "queue"; history.replaceState(null, "", "#queue");
    await copy(location.origin + "/p/" + c.portal_token, `Accepted at ${money(c.price_cents)}. Portal link copied for @${c.client}.`);
    await reload();
  },
  ask: async el => {
    const r = await api(`/api/requests/${el.dataset.id}/ask`, { body: { note: "" } });
    await reload();
    const box = $(`#ask-${el.dataset.id}`);
    if (box) box.innerHTML = `<div class="notice">${esc(r.message)}</div>`;
    copy(r.message, "Follow-up question copied. Send it to the client.");
  },
  decline: async el => {
    const note = prompt("Optional note for your records:", "") ?? null; if (note === null) return;
    await api(`/api/requests/${el.dataset.id}/decline`, { body: { note } }); toast("Declined"); await reload();
  },
  claude: async el => { el.disabled = true; el.textContent = "Reading…"; try { await api(`/api/requests/${el.dataset.id}/claude-review`, { method: "POST" }); } finally { await render(); } },
  invite: async el => {
    const r = await api(`/api/waitlist/${el.dataset.id}/invite`, { method: "POST" });
    await reload();
    $("#inviteOut").innerHTML = `<div class="notice" style="margin-bottom:12px">Send to @${esc(r.client)}${r.email ? ` (${esc(r.email)})` : ""}: ${esc(r.message)}</div>`;
    copy(r.message, "Invite message copied");
  },
  unwait: async el => { await api(`/api/waitlist/${el.dataset.id}/remove`, { method: "POST" }); await reload(); },
  narrate: async el => { el.disabled = true; el.textContent = "Claude is writing…"; try { await api(`/api/goals/${el.dataset.id}/narrate`, { method: "POST" }); } finally { await render(); } },
  endGoal: async el => { if (!confirm("End this goal?")) return; await api(`/api/goals/${el.dataset.id}`, { method: "DELETE" }); await render(); },
  saveType: async el => {
    const id = el.dataset.id;
    await api(`/api/types/${id}`, { method: "PATCH", body: { base_price: parseFloat($(`#tp-${id}`).value), est_hours: parseFloat($(`#th-${id}`).value), active: $(`#ta-${id}`).checked } });
    toast("Saved"); await reload();
  },
  stripe: async () => { const r = await api("/api/stripe/connect", { method: "POST" }); location.href = r.url; },
  export: async () => {
    const h = await api("/api/history");
    const q = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const iso = t => t ? new Date(t * 1000).toISOString().slice(0, 10) : "";
    const csv = ["client,type,price,hours,revisions,accepted_at,completed_at",
      ...h.map(x => [x.client_handle, x.type_name, (x.price_cents / 100).toFixed(2), x.hours_logged, x.revisions_used, iso(x.accepted_at), iso(x.completed_at)].map(q).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = `craftboard-${S.me.handle}-history.csv`; a.click();
  },
};

document.addEventListener("click", async e => {
  const el = e.target.closest("[data-action]"); if (!el) return;
  e.preventDefault();
  try { await ACTIONS[el.dataset.action](el); } catch (err) { toast(err.message); }
});

document.addEventListener("submit", async e => {
  const f = e.target; e.preventDefault();
  const data = Object.fromEntries(new FormData(f));
  try {
    if (f.id === "profileForm") {
      const body = { name: data.name, slots: +data.slots, revisions_included: +data.revisions_included, deposit_pct: +data.deposit_pct,
        hours_per_week: +data.hours_per_week, intake_mode: data.intake_mode, intake_message: data.intake_message };
      S.me = await api("/api/me", { method: "PATCH", body }); toast("Settings saved"); await reload();
    } else if (f.id === "typeForm") {
      await api("/api/types", { body: { name: data.name, description: data.description, base_price: +data.base_price, est_hours: +data.est_hours,
        characters_included: +data.characters_included, extra_character_pct: +data.extra_character_pct } });
      toast("Type added"); await reload();
    } else if (f.id === "csvForm") {
      const r = await api("/api/import/csv", { form: new FormData(f) });
      await reload();
      $("#csvOut").innerHTML = `<p class="okbox">Imported ${r.imported} commission${r.imported === 1 ? "" : "s"}.</p>${r.errors.length ? `<ul class="small err">${r.errors.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}`;
    } else if (f.id === "goalForm") {
      const btn = f.querySelector("button"); btn.disabled = true; btn.textContent = "Building…";
      try { await api("/api/goals", { body: { amount: +data.amount, weeks: +data.weeks, hours_per_week: +data.hours || null } }); }
      finally { await render(); }
    }
  } catch (err) { toast(err.message); }
});

$("#logout").onclick = async () => { try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {} location.href = "/login"; };
window.addEventListener("hashchange", () => { const t = location.hash.slice(1); if (t && t !== S.tab && TABS.some(x => x[0] === t)) { S.tab = t; reload().catch(e => toast(e.message)); } });
if (!TABS.some(x => x[0] === S.tab)) S.tab = "queue";
reload().catch(e => { $("#main").innerHTML = `<div class="empty err">${esc(e.message)}</div>`; });

// Keep the view current when clients act in their portals: refresh on focus and every 30s,
// but never while the creator is typing or has picked files.
async function softRefresh() {
  if (document.hidden || !S.dash) return;
  const a = document.activeElement;
  if (a && /INPUT|TEXTAREA|SELECT/.test(a.tagName)) return;
  if ([...document.querySelectorAll("input[type=file]")].some(f => f.files.length)) return;
  try {
    const before = JSON.stringify(S.dash);
    await refresh();
    if (JSON.stringify(S.dash) !== before && ["queue", "requests", "waitlist"].includes(S.tab)) await render();
  } catch (e) {}
}
window.addEventListener("focus", softRefresh);
document.addEventListener("visibilitychange", softRefresh);
setInterval(softRefresh, 30000);
