// Shared app shell: sidebar nav + auth gate. Every authenticated page
// (dashboard.html, users.html, etc.) includes theme.css + this file, then
// calls AximShell.init({ active: 'dashboard' }) once on load.

// Favicon - injected here rather than added to every individual page's
// <head> (dozens of pages, easy for one to drift/be missed) since every
// authenticated page already loads this script. The 3 pre-auth pages
// (login/wizard/reset_password.html) don't load shell.js, so they carry
// their own <link rel="icon"> tag directly instead.
(() => {
  if (document.querySelector('link[rel="icon"]')) return;
  const link = document.createElement("link");
  link.rel = "icon";
  link.type = "image/svg+xml";
  link.href = "/web/favicon.svg";
  document.head.appendChild(link);
})();

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
    capital: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M1.8 14.2h12.4"/><rect x="2.5" y="9.5" width="2.6" height="4.7" fill="currentColor" stroke="none"/><rect x="6.7" y="6.5" width="2.6" height="7.7" fill="currentColor" stroke="none"/><rect x="10.9" y="2.8" width="2.6" height="11.4" fill="currentColor" stroke="none"/></svg>',
    bots: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="5.5" width="10" height="8" rx="1.8"/><path d="M8 5.5V3M6 2.3h4"/><circle cx="6" cy="9.5" r="0.9" fill="currentColor" stroke="none"/><circle cx="10" cy="9.5" r="0.9" fill="currentColor" stroke="none"/><path d="M6 12h4"/></svg>',
    pipeline: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="2.5" cy="3" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="13.5" cy="13" r="1.5"/><path d="M3.8 4.2L6.8 6.8M9.2 9.2l3 2.8"/><path d="M2.5 6.5v3M13.5 6.5v3"/></svg>',
  };

  // Theme toggle icons (UI v2, 2026-07-18) - sun shown while in light
  // mode (click to go dark), moon shown while in dark mode (click to go
  // light) - the icon always represents the CURRENT state, matching the
  // convention most users already expect from other apps.
  const THEME_TOGGLE_ICON_SUN = '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M14.5 8h-1.6M3.1 8H1.5M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1M12.6 12.6l-1.1-1.1M4.5 4.5L3.4 3.4"/></svg>';
  const THEME_TOGGLE_ICON_MOON = '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M13.5 9.8A6 6 0 1 1 6.2 2.5a5 5 0 0 0 7.3 7.3Z"/></svg>';

  // "The AXIS" brand mark (brand/axis-mark.svg is the source of truth -
  // this is the glyph only, no background rect, since .sidebar-logo
  // .mark/.auth-logo .mark already paint the rounded-blue-square badge
  // via CSS). Same 0-100 coordinate space as the master SVG.
  const LOGO_MARK = '<svg width="100%" height="100%" viewBox="0 0 100 100"><path fill-rule="evenodd" fill="#FFFFFF" d="M 50 12 L 84 46 L 50 80 L 16 46 Z M 66 30 L 82 46 L 66 62 L 50 46 Z"/></svg>';

  // Same convention as every page's own escapeHtml() (web/*.html) -
  // encodes quotes too, not just & < >, since escaped text sometimes
  // ends up inside an attribute value elsewhere in this codebase.
  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s ?? "";
    return d.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // ---- Theme (light/dark) - UI v2, 2026-07-18. Every authenticated
  // page also carries a tiny inline script in <head> (before this file
  // loads) that applies the saved/OS-preferred theme to <html> BEFORE
  // first paint, so there is no flash of the wrong theme on navigation -
  // this file is what makes the choice interactive and persists it, not
  // what establishes it on load. Both read the SAME localStorage key. ----
  const THEME_STORAGE_KEY = "axim-theme";

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function updateThemeToggleUI() {
    const dark = currentTheme() === "dark";
    ["", "-mobile"].forEach(suffix => {
      const btn = document.getElementById(`axim-theme-toggle${suffix}`);
      if (!btn) return;
      const icon = btn.querySelector("svg");
      if (icon) icon.outerHTML = dark ? THEME_TOGGLE_ICON_MOON : THEME_TOGGLE_ICON_SUN;
      const label = document.getElementById(`axim-theme-toggle-label${suffix}`);
      if (label) label.textContent = dark ? "Dark Mode" : "Light Mode";
      const sw = document.getElementById(`axim-theme-toggle-switch${suffix}`);
      if (sw) sw.classList.toggle("on", dark);
    });
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme === "dark" ? "dark" : "light");
    try { localStorage.setItem(THEME_STORAGE_KEY, theme); } catch (e) {}
    updateThemeToggleUI();
  }

  function toggleTheme() {
    applyTheme(currentTheme() === "dark" ? "light" : "dark");
  }

  // IA reorganized 2026-07-14 per the approved UI overhaul spec: primary
  // nav trimmed to the 6 daily-use destinations, everything else moved
  // under "More" (progressive disclosure - matches the same grouping
  // already validated in the UI Vision branch). URLs deliberately
  // unchanged from before this reorg (only labels/grouping moved) so
  // existing bookmarks/deep-links keep working. Trade History and
  // Billing aren't in the approved nav list at all (primary or "More") -
  // reachable via a link from a related page instead (Performance links
  // to /trades, Settings/Users link to /billing) per "do not remove
  // existing working capabilities." (2026-07-16: the Performance ->
  // Trade History link had never actually been added despite this
  // comment's original intent - fixed. Capital Strategies is no longer
  // part of this list at all: that whole page was a leftover ~20-strategy
  // catalog from before the "4 official strategies" Money Management
  // Studio redesign (2026-07-13 product-owner directive, commit 6e8866a)
  // superseded and REPLACED it outright, not merely relocated it - so it
  // was removed, along with its dead GET /capital-strategies route, not
  // just unlinked.)
  const PRIMARY_NAV_ITEMS = [
    { key: "dashboard", label: "Home", href: "/dashboard", icon: ICONS.dashboard },
    { key: "sessions", label: "Sessions", href: "/sessions", icon: ICONS.sessions },
    { key: "funds", label: "Funds", href: "/funds", icon: ICONS.funds },
    { key: "telegram", label: "Sources", href: "/telegram", icon: ICONS.telegram },
    { key: "lab", label: "Strategy Lab", href: "/strategy-lab", icon: ICONS.lab },
    { key: "stats", label: "Performance", href: "/performance", icon: ICONS.stats },
  ];
  const MORE_NAV_ITEMS = [
    { key: "money", label: "Money Management", href: "/risk", icon: ICONS.money },
    { key: "automation", label: "Automation Studio", href: "/automation", icon: ICONS.rules },
    { key: "inspector", label: "Signal Inspector", href: "/inspector", icon: ICONS.inspector },
    { key: "pipeline", label: "Live Signal Pipeline", href: "/signal-pipeline", icon: ICONS.pipeline },
    { key: "pocketoption", label: "Broker Accounts", href: "/broker", icon: ICONS.pocketoption },
    { key: "bots", label: "Bot Control Center", href: "/bots", icon: ICONS.bots },
    { key: "logs", label: "Logs", href: "/logs", icon: ICONS.logs, adminOnly: true },
    { key: "users", label: "Users", href: "/users", icon: ICONS.users, adminOnly: true },
    { key: "guide", label: "Help", href: "/guide", icon: ICONS.guide },
    { key: "settings", label: "Settings", href: "/settings", icon: ICONS.settings },
  ];
  const NAV_ITEMS = [...PRIMARY_NAV_ITEMS, ...MORE_NAV_ITEMS];
  // 4 primary + a "More" tab covering the rest - a 6-across mobile bar
  // doesn't fit comfortably, matching the same constraint already
  // resolved in the UI Vision branch's mobile nav.
  const MOBILE_NAV_KEYS = ["dashboard", "sessions", "funds", "telegram"];

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
    const primary = PRIMARY_NAV_ITEMS;
    const more = MORE_NAV_ITEMS.filter(i => !i.adminOnly || isAdmin);
    const moreOpen = more.some(i => i.key === activeKey);
    root.innerHTML = `
      <div class="sidebar-logo"><span class="mark">${LOGO_MARK}</span> <span class="wordmark"><span class="wordmark-primary">AXIM</span><span class="wordmark-secondary">Trader</span></span></div>
      <div class="nav-group">
        ${primary.map(i => `
          <a class="nav-item ${i.key === activeKey ? "active" : ""}" href="${i.href}">
            ${i.icon}<span>${i.label}</span>
          </a>
        `).join("")}
      </div>
      <div class="nav-more ${moreOpen ? "open" : ""}">
        <div class="nav-more-toggle" onclick="this.parentElement.classList.toggle('open')">
          <span>More</span><span class="chev">&#9662;</span>
        </div>
        <div class="nav-more-list">
          ${more.map(i => `
            <a class="nav-item more-item ${i.key === activeKey ? "active" : ""}" href="${i.href}">
              ${i.icon}<span>${i.label}</span>
            </a>
          `).join("")}
        </div>
      </div>
      <div class="nav-spacer"></div>
      <div class="sidebar-footer">
        <button class="theme-toggle" id="axim-theme-toggle" onclick="AximShell.toggleTheme()" title="Switch theme" aria-label="Switch between light and dark theme">
          ${THEME_TOGGLE_ICON_SUN}<span id="axim-theme-toggle-label">Light Mode</span>
          <span class="theme-toggle-switch" id="axim-theme-toggle-switch"><span class="theme-toggle-knob"></span></span>
        </button>
        <div class="notif-bell-wrap">
          <button class="notif-bell" id="axim-notif-bell" onclick="AximShell._toggleNotifDropdown()">
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 6.5a4 4 0 0 1 8 0c0 3.5 1.2 4.5 1.2 4.5H2.8S4 10 4 6.5Z"/><path d="M6.3 13a1.8 1.8 0 0 0 3.4 0"/></svg>
            <span>Notifications</span>
            <span class="notif-count" id="axim-notif-count" style="display:none;">0</span>
          </button>
          <div class="notif-dropdown" id="axim-notif-dropdown">
            <div class="notif-dropdown-header">
              <span>Notifications</span>
              <button class="subtle" onclick="AximShell._markAllNotifsRead()">Mark all read</button>
            </div>
            <div id="axim-notif-list"><div class="notif-empty">Loading...</div></div>
          </div>
        </div>
        <div class="user-chip">
          <div class="avatar">${escapeHtml(initials(user.email))}</div>
          <div style="overflow:hidden;">
            <div class="email">${escapeHtml(user.email)}</div>
            <div class="role">${escapeHtml(user.role)} &middot; ${escapeHtml(user.access_tier)}</div>
          </div>
        </div>
        <div class="row" style="margin-top:8px;">
          <button class="subtle" style="width:100%;" onclick="AximShell.logout()">Sign out</button>
        </div>
      </div>
    `;
    renderMobileNav(user, activeKey, isAdmin);
    updateThemeToggleUI();
    document.addEventListener("click", (e) => {
      const wrap = document.querySelector(".notif-bell-wrap");
      if (wrap && !wrap.contains(e.target)) {
        const dd = document.getElementById("axim-notif-dropdown");
        if (dd) dd.classList.remove("open");
      }
    });
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

  // ---- Generic in-app confirmation dialog - replaces native confirm(),
  // which blocks the tab's renderer entirely (a stuck confirm() during
  // browser-automation testing froze every tab in the window, not just
  // its own, 2026-07-19). One dialog at a time, same as confirm()'s own
  // semantics - a second call before the first resolves replaces it. ----
  let _confirmDialogResolve = null;

  function _injectGenericConfirmModal() {
    if (document.getElementById("axim-generic-confirm-modal")) return;
    const modal = document.createElement("div");
    modal.className = "modal-backdrop";
    modal.id = "axim-generic-confirm-modal";
    modal.innerHTML = `
      <div class="modal" style="width:420px;">
        <div id="axim-gc-title" style="font-weight:650; font-size:15px; margin-bottom:10px;"></div>
        <div id="axim-gc-message" class="muted" style="margin-bottom:20px; font-size:13.5px; line-height:1.5; white-space:pre-line;"></div>
        <div class="row" style="justify-content:flex-end;">
          <button class="subtle" id="axim-gc-cancel">Cancel</button>
          <button class="primary" id="axim-gc-confirm">Confirm</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener("click", (e) => { if (e.target === modal) _resolveConfirmDialog(false); });
    document.getElementById("axim-gc-cancel").addEventListener("click", () => _resolveConfirmDialog(false));
    document.getElementById("axim-gc-confirm").addEventListener("click", () => _resolveConfirmDialog(true));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && modal.style.display === "flex") _resolveConfirmDialog(false);
    });
  }

  function _resolveConfirmDialog(result) {
    const modal = document.getElementById("axim-generic-confirm-modal");
    if (modal) modal.style.display = "none";
    if (_confirmDialogResolve) {
      const resolve = _confirmDialogResolve;
      _confirmDialogResolve = null;
      resolve(result);
    }
  }

  // opts: { title, confirmLabel, cancelLabel, danger } - danger swaps the
  // Confirm button to the .danger style and outlines the modal in red,
  // for destructive/irreversible actions.
  function confirmDialog(message, opts) {
    opts = opts || {};
    _injectGenericConfirmModal();
    return new Promise((resolve) => {
      _confirmDialogResolve = resolve;
      const modal = document.getElementById("axim-generic-confirm-modal");
      document.getElementById("axim-gc-title").textContent = opts.title || "Are you sure?";
      document.getElementById("axim-gc-message").textContent = message;
      const confirmBtn = document.getElementById("axim-gc-confirm");
      confirmBtn.textContent = opts.confirmLabel || "Confirm";
      confirmBtn.className = opts.danger ? "danger" : "primary";
      document.getElementById("axim-gc-cancel").textContent = opts.cancelLabel || "Cancel";
      modal.classList.toggle("danger", !!opts.danger);
      modal.style.display = "flex";
    });
  }

  // ---- In-app notifications (core/rule_engine.py's notify_owner
  // action writes these; polled here so any page reflects new ones
  // without a reload) -----------------------------------------------
  function fmtNotifTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleString();
  }

  async function pollNotifCount() {
    try {
      const { count } = await fetchJSON("/api/notifications/unread-count");
      const badge = document.getElementById("axim-notif-count");
      if (!badge) return;
      if (count > 0) {
        badge.textContent = count > 99 ? "99+" : String(count);
        badge.style.display = "inline-block";
      } else {
        badge.style.display = "none";
      }
    } catch (e) {}
  }

  async function _toggleNotifDropdown() {
    const dd = document.getElementById("axim-notif-dropdown");
    if (!dd) return;
    const opening = !dd.classList.contains("open");
    dd.classList.toggle("open");
    if (opening) await loadNotifList();
  }

  async function loadNotifList() {
    const list = document.getElementById("axim-notif-list");
    try {
      const rows = await fetchJSON("/api/notifications");
      if (!rows.length) {
        list.innerHTML = '<div class="notif-empty">No notifications yet.</div>';
        return;
      }
      list.innerHTML = rows.map(n => `
        <div class="notif-item ${n.read_at ? "" : "unread"}">
          <div class="notif-message">${escapeHtml(n.message)}</div>
          <div class="notif-time">${fmtNotifTime(n.created_at)}</div>
        </div>
      `).join("");
    } catch (e) {
      list.innerHTML = '<div class="notif-empty">Failed to load.</div>';
    }
  }

  async function _markAllNotifsRead() {
    try { await fetchJSON("/api/notifications/read-all", { method: "POST" }); } catch (e) {}
    await loadNotifList();
    await pollNotifCount();
  }

  function startNotifPolling() {
    pollNotifCount();
    setInterval(pollNotifCount, 20000);
    subscribeEvents({
      "notification.created": {
        onEvent: () => {
          pollNotifCount();
          const dropdown = document.getElementById("axim-notif-dropdown");
          if (dropdown && dropdown.classList.contains("open")) loadNotifList();
        },
        onResync: pollNotifCount,
      },
    });
  }

  // ---- Real-time event stream (docs/AXIM_REMOTE_ACCESS.md) - one shared
  // EventSource per page, dispatching to whichever handlers a page
  // registers via AximShell.subscribeEvents(). Purely an enhancement:
  // every page keeps its existing polling as a fallback, so a dropped/
  // unavailable stream degrades to "a bit less instant," never "broken".
  let eventSource = null;
  const eventHandlers = {}; // event_type -> [{ onEvent, onResync }]

  function subscribeEvents(handlers) {
    for (const type in handlers) {
      if (!eventHandlers[type]) eventHandlers[type] = [];
      eventHandlers[type].push(handlers[type]);
      if (eventSource) _bindEventType(type);
    }
    _ensureEventStream();
  }

  function _bindEventType(type) {
    eventSource.addEventListener(type, (e) => {
      let payload = null;
      try { payload = JSON.parse(e.data); } catch (err) {}
      (eventHandlers[type] || []).forEach(h => { try { h.onEvent(payload); } catch (err) {} });
    });
  }

  function _ensureEventStream() {
    if (eventSource) return;
    try {
      eventSource = new EventSource("/api/events/stream");
    } catch (err) {
      return; // browser lacks EventSource support - polling fallback still runs
    }
    eventSource.addEventListener("resync", () => {
      for (const type in eventHandlers) {
        eventHandlers[type].forEach(h => { if (h.onResync) { try { h.onResync(); } catch (err) {} } });
      }
    });
    Object.keys(eventHandlers).forEach(_bindEventType);
    // onerror fires on every disconnect, including normal ones the
    // browser's built-in auto-reconnect (with Last-Event-ID) already
    // handles - nothing to do here but let it retry.
    eventSource.onerror = () => {};
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
    startNotifPolling();
    return user;
  }

  // Below theme.css's 900px breakpoint the sidebar itself is hidden
  // entirely (see the matching @media block) and replaced by a fixed
  // bottom nav bar - the approved mobile pattern (2026-07-14 UI
  // overhaul spec: "an appropriate bottom navigation pattern rather
  // than shrinking the desktop sidebar"), replacing the previous
  // off-canvas hamburger drawer. 4 primary destinations + a "More" tab
  // that opens a bottom sheet with everything else - a 6-across bar
  // doesn't fit comfortably on a phone. Called from renderSidebar()
  // (not init() directly) since it needs the same activeKey/isAdmin
  // filtering the desktop sidebar just computed.
  function renderMobileNav(user, activeKey, isAdmin) {
    document.querySelectorAll(".mobile-header, .mobile-nav, .more-sheet").forEach(el => el.remove());
    const more = MORE_NAV_ITEMS.filter(i => !i.adminOnly || isAdmin);
    const moreActive = more.some(i => i.key === activeKey);

    const mobileHeader = document.createElement("div");
    mobileHeader.className = "mobile-header";
    mobileHeader.innerHTML = `
      <span class="mark">${LOGO_MARK}</span>
      <span class="wordmark"><span class="wordmark-primary">AXIM</span><span class="wordmark-secondary">Trader</span></span>
    `;
    document.body.appendChild(mobileHeader);

    const barItems = PRIMARY_NAV_ITEMS.filter(i => MOBILE_NAV_KEYS.includes(i.key)).map(i => `
      <a class="item ${i.key === activeKey ? "active" : ""}" href="${i.href}">${i.icon}<span>${i.label}</span></a>
    `).join("") + `
      <a class="item ${moreActive ? "active" : ""}" href="#" id="mobile-more-tab">${ICONS.settings}<span>More</span></a>
    `;

    const moreSheet = document.createElement("div");
    moreSheet.className = "more-sheet";
    moreSheet.id = "more-sheet";
    moreSheet.innerHTML = `
      <div class="more-sheet-inner">
        <div class="more-sheet-title">More</div>
        ${more.map(i => `
          <a class="nav-item more-item ${i.key === activeKey ? "active" : ""}" href="${i.href}">${i.icon}<span>${i.label}</span></a>
        `).join("")}
        <button class="theme-toggle" id="axim-theme-toggle-mobile" onclick="AximShell.toggleTheme()" style="width:100%; margin-top:8px;" aria-label="Switch between light and dark theme">
          ${THEME_TOGGLE_ICON_SUN}<span id="axim-theme-toggle-label-mobile">Light Mode</span>
          <span class="theme-toggle-switch" id="axim-theme-toggle-switch-mobile"><span class="theme-toggle-knob"></span></span>
        </button>
      </div>
    `;
    moreSheet.addEventListener("click", (e) => { if (e.target === moreSheet) moreSheet.classList.remove("open"); });
    updateThemeToggleUI();

    const mobileNav = document.createElement("div");
    mobileNav.className = "mobile-nav";
    mobileNav.innerHTML = barItems;

    document.body.appendChild(mobileNav);
    document.body.appendChild(moreSheet);
    document.getElementById("mobile-more-tab").addEventListener("click", (e) => {
      e.preventDefault();
      moreSheet.classList.toggle("open");
    });
  }

  // Every technical/operational surface (raw ids, pids, heartbeats,
  // process internals) should check this before rendering rather than
  // being on by default - see docs/AXIM_APP_PLAN.md's design principle
  // that AXIM reads like a wealth management platform, not a monitoring
  // dashboard, unless the operator has explicitly opted into
  // Settings > Developer.
  function isDeveloperMode() { return developerMode; }

  return {
    init, logout, fetchJSON, isDeveloperMode, _confirmPendingTrade, _rejectPendingTrade,
    _toggleNotifDropdown, _markAllNotifsRead, subscribeEvents, toggleTheme, confirm: confirmDialog,
  };
})();
