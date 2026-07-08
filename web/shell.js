// Shared app shell: sidebar nav + auth gate. Every authenticated page
// (dashboard.html, users.html, etc.) includes theme.css + this file, then
// calls AximShell.init({ active: 'dashboard' }) once on load.
const AximShell = (() => {
  const ICONS = {
    dashboard: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="1.5" y="1.5" width="6" height="6" rx="1.2"/><rect x="8.5" y="1.5" width="6" height="4" rx="1.2"/><rect x="8.5" y="7.5" width="6" height="7" rx="1.2"/><rect x="1.5" y="9.5" width="6" height="5" rx="1.2"/></svg>',
    sessions: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="6.3"/><path d="M6.3 5.5l4 2.5-4 2.5z" fill="currentColor" stroke="none"/></svg>',
    telegram: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M14 2L2 7.5l4.2 1.6M14 2L9.8 14l-3.6-4.9M14 2L6.2 9.1"/></svg>',
    inspector: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="6.8" cy="6.8" r="4.3"/><path d="M10.2 10.2L14 14"/></svg>',
    money: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="6.3"/><path d="M8 4.5v7M10 6.2c0-1-.9-1.7-2-1.7s-2 .6-2 1.6c0 2.2 4 1.1 4 3.2 0 1-.9 1.7-2 1.7s-2-.7-2-1.7"/></svg>',
    trades: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1.5 8.5l3-3 2.5 2.5 3.5-4.5 4 4"/><path d="M11 3.5h3.5V7"/></svg>',
    stats: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 14V2M2 14h12"/><rect x="4" y="9" width="2.2" height="5" fill="currentColor" stroke="none"/><rect x="7.4" y="6" width="2.2" height="8" fill="currentColor" stroke="none"/><rect x="10.8" y="3.5" width="2.2" height="10.5" fill="currentColor" stroke="none"/></svg>',
    pocketoption: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="1.5" y="4.5" width="13" height="8" rx="1.6"/><path d="M1.5 7h13"/></svg>',
    users: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="6" cy="5.3" r="2.3"/><path d="M1.6 14c.5-2.7 2.2-4.2 4.4-4.2s3.9 1.5 4.4 4.2"/><circle cx="11.6" cy="5.5" r="1.8"/><path d="M10.5 9.9c1.8.2 3 1.6 3.4 4"/></svg>',
    logs: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="2.5" y="1.5" width="11" height="13" rx="1.4"/><path d="M5 5h6M5 8h6M5 11h4"/></svg>',
    settings: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="2.3"/><path d="M8 2v1.6M8 12.4V14M14 8h-1.6M3.6 8H2M12.1 3.9l-1.1 1.1M5 10l-1.1 1.1M12.1 12.1L11 11M5 6L3.9 3.9"/></svg>',
    rules: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="3" cy="4" r="1.8"/><path d="M4.8 4h3.7c1 0 1.5.5 1.5 1.5v2M8 4l2 2-2 2"/><path d="M4.8 12h6.7"/><circle cx="3" cy="12" r="1.8"/></svg>',
    lab: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M6.2 1.8h3.6M6.8 1.8v3.8L3.4 12c-.5.9.2 2 1.2 2h7.9c1 0 1.7-1.1 1.2-2L10.3 5.6V1.8"/><path d="M5.4 9.5h5.2"/></svg>',
    funds: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="1.5" y="4" width="13" height="9.5" rx="1.6"/><path d="M1.5 6.8h13"/><circle cx="11.3" cy="10.2" r="1.3" fill="currentColor" stroke="none"/></svg>',
    guide: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="6.3"/><path d="M6.1 6.2c.2-1 1-1.6 1.9-1.6 1 0 1.9.6 1.9 1.7 0 1.4-1.9 1.3-1.9 3"/><circle cx="8" cy="11.2" r="0.15" fill="currentColor" stroke="currentColor" stroke-width="0.9"/></svg>',
  };

  const NAV_ITEMS = [
    { key: "dashboard", label: "Mission Control", href: "/dashboard", icon: ICONS.dashboard },
    { key: "funds", label: "Funds", href: "/funds", icon: ICONS.funds },
    { key: "sessions", label: "Trading Sessions", href: "/sessions", icon: ICONS.sessions },
    { key: "telegram", label: "Signal Sources", href: "/telegram", icon: ICONS.telegram },
    { key: "inspector", label: "Signal Inspector", href: "/inspector", icon: ICONS.inspector },
    { key: "money", label: "Risk Engine", href: "/risk", icon: ICONS.money },
    { key: "rules", label: "Rule Builder", href: "/rules", icon: ICONS.rules },
    { key: "lab", label: "Strategy Lab", href: "/strategy-lab", icon: ICONS.lab },
    { key: "trades", label: "Trade Center", href: "/trades", icon: ICONS.trades },
    { key: "stats", label: "Performance", href: "/performance", icon: ICONS.stats },
    { key: "pocketoption", label: "Broker", href: "/broker", icon: ICONS.pocketoption },
    { key: "users", label: "Users", href: "/users", icon: ICONS.users, adminOnly: true },
    { key: "logs", label: "Logs", href: "/logs", icon: ICONS.logs, adminOnly: true },
    { key: "settings", label: "Settings", href: "/settings", icon: ICONS.settings },
    { key: "guide", label: "Help / Guide", href: "/guide", icon: ICONS.guide },
  ];

  async function fetchJSON(url, opts) {
    const res = await fetch(url, { credentials: "same-origin", ...opts });
    if (!res.ok) {
      const err = new Error(`${url} -> ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  function initials(email) {
    return (email || "?").slice(0, 2).toUpperCase();
  }

  function renderSidebar(root, user, activeKey) {
    const isAdmin = user.role === "owner" || user.role === "admin";
    const items = NAV_ITEMS.filter(i => !i.adminOnly || isAdmin);
    root.innerHTML = `
      <div class="sidebar-logo"><span class="mark">A</span> AXIM</div>
      <div class="nav-group">
        ${items.map(i => `
          <a class="nav-item ${i.key === activeKey ? "active" : ""}" href="${i.href}">
            ${i.icon}<span>${i.label}</span>
          </a>
        `).join("")}
      </div>
      <div class="nav-spacer"></div>
      <div class="sidebar-footer">
        <div class="user-chip">
          <div class="avatar">${initials(user.email)}</div>
          <div style="overflow:hidden;">
            <div class="email">${user.email}</div>
            <div class="role">${user.role} &middot; ${user.access_tier}</div>
          </div>
        </div>
        <div class="row" style="margin-top:8px;">
          <button class="subtle" style="width:100%;" onclick="AximShell.logout()">Sign out</button>
        </div>
      </div>
    `;
  }

  async function logout() {
    try { await fetchJSON("/api/auth/logout", { method: "POST" }); } catch (e) {}
    window.location.href = "/login";
  }

  let developerMode = false;

  // ---- Live-mode trade confirmation gate (docs/AXIM_APP_PLAN.md) -----
  // Polls core/database.py's pending_trade_confirmations table (via
  // api/sessions.py) from EVERY page, since an operator could be
  // anywhere in the app when a Live trade needs a decision. The actual
  // wait/timeout/fail-closed logic lives entirely server-side in
  // core/session_manager.wait_for_trade_confirmation - this is purely
  // the display + Confirm/Reject actions.
  let currentConfirmation = null;
  let confirmCountdownTimer = null;
  let confirmPollInFlight = false;

  function injectConfirmModal() {
    if (document.getElementById("axim-confirm-modal")) return;
    const modal = document.createElement("div");
    modal.className = "modal-backdrop";
    modal.id = "axim-confirm-modal";
    modal.innerHTML = `
      <div class="modal" style="width:440px;">
        <div class="banner danger" style="margin-bottom:14px;">LIVE TRADE - CONFIRMATION REQUIRED</div>
        <div class="confirm-trade-headline" id="axim-confirm-headline">-</div>
        <div class="stat-row"><span class="stat-label">Expiry</span><span class="stat-value" id="axim-confirm-expiry">-</span></div>
        <div class="stat-row"><span class="stat-label">Amount</span><span class="stat-value" id="axim-confirm-amount">-</span></div>
        <div class="confirm-countdown-track"><div class="confirm-countdown-fill" id="axim-confirm-fill" style="width:100%;"></div></div>
        <div class="muted" id="axim-confirm-countdown-text" style="margin-bottom:14px;">&nbsp;</div>
        <div class="row">
          <button class="danger" style="flex:1;" onclick="AximShell._rejectPendingTrade()">Reject</button>
          <button class="primary" style="flex:1;" onclick="AximShell._confirmPendingTrade()">Confirm Trade</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  }

  function renderConfirmModal(row) {
    document.getElementById("axim-confirm-headline").textContent = `${row.asset || "-"} ${row.direction || ""}`;
    document.getElementById("axim-confirm-expiry").textContent = row.expiry || "-";
    document.getElementById("axim-confirm-amount").textContent = row.amount != null ? `$${Number(row.amount).toFixed(2)}` : "-";
    document.getElementById("axim-confirm-modal").style.display = "flex";
    updateConfirmCountdown();
  }

  function updateConfirmCountdown() {
    if (!currentConfirmation) return;
    const requestedAt = new Date(currentConfirmation.requested_at).getTime();
    const timeoutMs = (currentConfirmation.timeout_seconds || 45) * 1000;
    const elapsed = Date.now() - requestedAt;
    const remaining = Math.max(0, Math.ceil((timeoutMs - elapsed) / 1000));
    const pct = Math.max(0, Math.min(100, ((timeoutMs - elapsed) / timeoutMs) * 100));
    const fill = document.getElementById("axim-confirm-fill");
    const text = document.getElementById("axim-confirm-countdown-text");
    if (fill) fill.style.width = pct + "%";
    if (text) text.textContent = remaining > 0
      ? `Expires in ${remaining}s - if no one responds, this trade is automatically rejected.`
      : "Expiring now...";
  }

  function closeConfirmModal() {
    const modal = document.getElementById("axim-confirm-modal");
    if (modal) modal.style.display = "none";
    currentConfirmation = null;
  }

  async function _confirmPendingTrade() {
    if (!currentConfirmation) return;
    try {
      await fetchJSON(`/api/sessions/pending-confirmations/${currentConfirmation.trade_id}/confirm`, { method: "POST" });
    } catch (e) {}
    closeConfirmModal();
    pollPendingConfirmations();
  }

  async function _rejectPendingTrade() {
    if (!currentConfirmation) return;
    try {
      await fetchJSON(`/api/sessions/pending-confirmations/${currentConfirmation.trade_id}/reject`, { method: "POST" });
    } catch (e) {}
    closeConfirmModal();
    pollPendingConfirmations();
  }

  async function pollPendingConfirmations() {
    // Guard against overlap between the 2s setInterval tick and the
    // manual poll fired right after a Confirm/Reject click: without
    // this, two in-flight fetches can resolve out of order and the
    // later-arriving (but earlier-sent, now-stale) response overwrites
    // currentConfirmation with an already-decided trade - the next
    // click then silently 409s against the wrong trade_id while the
    // real pending one lingers and reappears. Found via live testing,
    // not hypothetical.
    if (confirmPollInFlight) return;
    confirmPollInFlight = true;
    try {
      const rows = await fetchJSON("/api/sessions/pending-confirmations");
      if (rows.length) {
        // Oldest first (API already sorts this way) - show one at a
        // time; resolving it reveals the next on the following poll.
        currentConfirmation = rows[0];
        renderConfirmModal(currentConfirmation);
      } else if (currentConfirmation) {
        closeConfirmModal();
      }
    } catch (e) {
      // Not logged in yet, or a transient network hiccup - never let a
      // failed poll throw an unhandled rejection into the page.
    } finally {
      confirmPollInFlight = false;
    }
  }

  function startConfirmationPolling() {
    injectConfirmModal();
    pollPendingConfirmations();
    setInterval(pollPendingConfirmations, 2000);
    confirmCountdownTimer = setInterval(updateConfirmCountdown, 1000);
  }

  async function init(opts) {
    let user;
    try {
      user = await fetchJSON("/api/auth/me");
    } catch (e) {
      window.location.href = "/login";
      return null;
    }
    try {
      developerMode = (await fetchJSON("/api/settings/developer-mode")).enabled;
    } catch (e) {
      developerMode = false;
    }
    const shellRoot = document.getElementById("app-shell");
    shellRoot.classList.add("app-shell");
    const sidebar = document.createElement("nav");
    sidebar.className = "sidebar";
    sidebar.id = "sidebar";
    shellRoot.insertBefore(sidebar, shellRoot.firstChild);
    renderSidebar(sidebar, user, opts.active);
    startConfirmationPolling();
    return user;
  }

  // Every technical/operational surface (raw ids, pids, heartbeats,
  // process internals) should check this before rendering rather than
  // being on by default - see docs/AXIM_APP_PLAN.md's design principle
  // that AXIM reads like a wealth management platform, not a monitoring
  // dashboard, unless the operator has explicitly opted into
  // Settings > Developer.
  function isDeveloperMode() { return developerMode; }

  return { init, logout, fetchJSON, isDeveloperMode, _confirmPendingTrade, _rejectPendingTrade };
})();
