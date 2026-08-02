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

  // AXIM V2 brand lock (2026-07-26 final build directive): the approved
  // mark is the provided image asset, used directly - not redrawn,
  // vectorized, recolored, or reinterpreted. The custom X's upper-right
  // segment intentionally stops short of the center crossing; that
  // detail only exists correctly in the source PNG, which is why this is
  // an <img>, not an inline SVG approximation of it.
  const LOGO_MARK = '<img src="/web/assets/brand/axim-icon-white-on-blue.png" alt="AXIM" width="100%" height="100%" style="display:block; width:100%; height:100%; object-fit:cover;">';

  // Shared "empty state" panel (icon + message) - was copy-pasted as a
  // ~200-char inline SVG verbatim across dashboard.html/bots.html/
  // sessions.html (2026-07-19 design consolidation). `message` should
  // already be escaped/trusted HTML, same convention as every other
  // *.innerHTML = `...` call site in this codebase.
  const EMPTY_PANEL_MARK = '<img src="/web/assets/brand/axim-icon-white-on-blue.png" alt="">';
  function emptyPanel(message) {
    return `<div class="empty-panel"><span class="empty-panel-mark">${EMPTY_PANEL_MARK}</span>${message}</div>`;
  }

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

  // IA reorganized 2026-07-25 per the AXIM Trader UI v2 Design Stack
  // (SCREEN_INVENTORY.md's approved 10-screen catalog, flat, no "More"
  // menu): Dashboard, Portfolio, Signals, Money Management, Backtesting,
  // Strategies, Performance, Analytics, Bots, Settings. URLs deliberately
  // unchanged (only labels/order moved) so existing bookmarks/deep-links
  // keep working, per that same spec's own "preserve existing URLs where
  // practical" note.
  //
  // Two of these were TEMPORARY placeholders when this comment was
  // written; "Signals" no longer is (2026-08-01: confirmed live -
  // web/telegram.html was rebuilt into the approved 5-region blueprint -
  // Signal Filters, Signal Feed Table, Provider Status, Selected Signal
  // Details, Execution Rules - all present):
  //   - "Bots" -> /sessions (Trading Sessions is the closer functional
  //     match to the approved bot/session-health+controls+logs screen;
  //     the current /bots route is a DIFFERENT feature - interactive
  //     bot-command signal channels - kept below, not renamed, to avoid
  //     conflating the two).
  //   - "Analytics" is intentionally OMITTED, not stubbed: no real,
  //     separately-justified content exists yet for it (this project's
  //     own standing "no placeholder screens" rule) - added back once
  //     real diagnostics/cohort content is scoped.
  const PRIMARY_NAV_ITEMS = [
    { key: "dashboard", label: "Dashboard", href: "/dashboard", icon: ICONS.dashboard },
    { key: "funds", label: "Portfolio", href: "/funds", icon: ICONS.funds },
    { key: "telegram", label: "Signals", href: "/telegram", icon: ICONS.telegram },
    { key: "money", label: "Money Management", href: "/risk", icon: ICONS.money },
    { key: "lab", label: "Backtesting", href: "/strategy-lab", icon: ICONS.lab },
    { key: "automation", label: "Strategies", href: "/automation", icon: ICONS.rules },
    { key: "stats", label: "Performance", href: "/performance", icon: ICONS.stats },
    { key: "sessions", label: "Bots", href: "/sessions", icon: ICONS.sessions },
    // Broker Accounts (2026-07-25 Integration Review): deliberately kept
    // as its own primary destination, not merged into Portfolio or made
    // Fund-contextual - a broker account is a many-to-one shared
    // resource (one account routinely serves several Funds at once) and
    // carries substantial account-scoped safety controls (Emergency
    // Stop, per-account Safety Settings, connection lifecycle) used
    // independently of any single Fund. This is an intentional, reasoned
    // departure from the original Design Stack's exact 10-screen count,
    // not an oversight - see the Integration Review note for the full
    // reasoning.
    { key: "pocketoption", label: "Broker Accounts", href: "/broker", icon: ICONS.pocketoption },
    { key: "settings", label: "Settings", href: "/settings", icon: ICONS.settings },
  ];
  // Not part of the approved 10-screen catalog at all (Logs/Users/Help).
  // Signal Inspector, Live Signal Pipeline, and Bot Control Center are
  // REMOVED from nav entirely (2026-07-25): their content now lives on
  // the Signals and Bots pages themselves - the old routes still work
  // as redirects (see api/main.py) so nothing is stranded, they're just
  // no longer separate destinations.
  const MORE_NAV_ITEMS = [
    { key: "logs", label: "Logs", href: "/logs", icon: ICONS.logs, adminOnly: true },
    { key: "users", label: "Users", href: "/users", icon: ICONS.users, adminOnly: true },
    { key: "guide", label: "Help", href: "/guide", icon: ICONS.guide },
  ];
  const NAV_ITEMS = [...PRIMARY_NAV_ITEMS, ...MORE_NAV_ITEMS];
  // 4 primary + a "More" tab covering the rest - a 9-across mobile bar
  // doesn't fit comfortably, matching the same constraint already
  // resolved in the UI Vision branch's mobile nav.
  const MOBILE_NAV_KEYS = ["dashboard", "funds", "telegram", "money"];

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

  // Visual Fidelity Pass Round 3 - the Design System Board's persistent
  // top status bar (balance, account-mode pill, notifications, avatar),
  // present on every screen in both boards but absent from the app.
  // Surfaces data already computed elsewhere (Dashboard's own balance
  // total, the real account mode, the existing notification bell) -
  // relocated/duplicated presentation, not a new feature.
  function renderTopbar(root, user) {
    root.innerHTML = `
      <span class="topbar-balance" id="topbar-balance">&nbsp;</span>
      <span class="topbar-mode" id="topbar-mode" style="display:none;"><span class="dot"></span><span id="topbar-mode-label"></span></span>
      <div class="notif-bell-wrap">
        <button class="notif-bell" id="axim-notif-bell" onclick="AximShell._toggleNotifDropdown()" title="Notifications" aria-label="Notifications">
          <svg width="17" height="17" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 6.5a4 4 0 0 1 8 0c0 3.5 1.2 4.5 1.2 4.5H2.8S4 10 4 6.5Z"/><path d="M6.3 13a1.8 1.8 0 0 0 3.4 0"/></svg>
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
      <div class="topbar-avatar" title="${escapeHtml(user.email)}">${escapeHtml(initials(user.email))}</div>
    `;
    loadTopbarBalance();
    loadTopbarMode();
  }

  async function loadTopbarBalance() {
    const el = document.getElementById("topbar-balance");
    if (!el) return;
    try {
      const funds = await fetchJSON("/api/funds?status=active");
      const total = funds.reduce((sum, f) => sum + (f.balances?.total_account_value ?? 0), 0);
      el.textContent = "$" + total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    } catch (e) {
      el.textContent = "-";
    }
  }

  async function loadTopbarMode() {
    const el = document.getElementById("topbar-mode");
    const label = document.getElementById("topbar-mode-label");
    if (!el) return;
    try {
      const po = await fetchJSON("/api/pocket-option/status");
      const isLive = po.account_mode === "LIVE";
      el.className = "topbar-mode " + (isLive ? "live" : "demo");
      label.textContent = po.account_mode || "DEMO";
      el.style.display = "inline-flex";
    } catch (e) {
      el.style.display = "none";
    }
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
    const topbar = document.createElement("div");
    topbar.className = "topbar";
    topbar.id = "topbar";
    shellRoot.insertBefore(topbar, shellRoot.firstChild);
    renderTopbar(topbar, user);
    const sidebar = document.createElement("nav");
    sidebar.className = "sidebar";
    sidebar.id = "sidebar";
    shellRoot.insertBefore(sidebar, topbar.nextSibling);
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

  // ==================== Shared Component Layer (UI v2, 2026-07-25) ====================
  // Per COMPONENT_SPEC.md - vanilla JS/CSS, no build step, matching this
  // stack's existing pattern (see docs/ui-v2-audit.md's own "Real stack"
  // note). Every function returns an HTML string for `.innerHTML =`,
  // the same convention every page already uses. These consolidate
  // patterns that were being reimplemented ad hoc per-page (state
  // badges, empty/loading/error table rows, metric tiles) - purely
  // presentational, no functional change to any page that adopts them.

  function _shellEscapeHtml(s) { return escapeHtml(s); }

  // ---- StatusBadge: live, win, loss, pending, upcoming, inactive ----
  // "Never rely on color alone" (COMPONENT_SPEC.md) - every state also
  // gets its own glyph, not just a color, so it still reads correctly
  // for a color-blind user or in a screenshot converted to grayscale.
  const STATUS_BADGE_META = {
    live:     { cls: "on",     glyph: "●", label: "Live" },
    win:      { cls: "on",     glyph: "✓", label: "Win" },
    loss:     { cls: "danger", glyph: "✕", label: "Loss" },
    pending:  { cls: "warn",   glyph: "⏳", label: "Pending" },
    upcoming: { cls: "info",   glyph: "○", label: "Upcoming" },
    inactive: { cls: "off",    glyph: "–", label: "Inactive" },
  };
  function statusBadge(status, label) {
    const meta = STATUS_BADGE_META[status] || { cls: "off", glyph: "?", label: status };
    return `<span class="badge ${meta.cls}"><span aria-hidden="true">${meta.glyph}</span> ${escapeHtml(label || meta.label)}</span>`;
  }

  // ---- SignalRow state badge: received, parsed, rejected, queued,
  // executed, won, lost - always pairs the state with its exact detail/
  // reason text (never a bare state with no explanation), matching the
  // Signals page's Signal Feed Table. ----
  const SIGNAL_ROW_STATE_META = {
    received: { color: "var(--brand)" }, parsed: { color: "var(--brand)" },
    queued: { color: "var(--brand)" }, executed: { color: "var(--brand)" },
    won: { color: "var(--green)" }, lost: { color: "var(--red)" },
    rejected: { color: "var(--red)" }, skipped: { color: "var(--text-faint)" },
    failed: { color: "var(--red)" },
  };
  function signalRowState(state, detail) {
    const key = (state || "").toLowerCase();
    const meta = SIGNAL_ROW_STATE_META[key] || { color: "var(--text-dim)" };
    const label = state ? state.charAt(0).toUpperCase() + state.slice(1) : "Unknown";
    return `<span style="color:${meta.color}; font-weight:600; font-size:12.5px;">${escapeHtml(label)}</span>` +
      (detail ? `<div class="muted" style="font-size:11px; margin-top:1px;">${escapeHtml(detail)}</div>` : "");
  }

  // ---- MetricCard: default, loading, error, positive, negative ----
  // label/value plus an optional delta ({value, positive}). `state`
  // overrides value rendering for loading/error so a page never has to
  // hand-roll "Loading..." text inside a value slot. Matches the
  // existing .mm-tile convention exactly (a grid cell meant to sit
  // inside an already-wrapping .mm-grid container, e.g. Money
  // Management's Risk Control Center) - deliberately NOT its own
  // outer card, so it's a true drop-in for the pattern already used
  // throughout this codebase rather than a new, differently-nested one.
  // `value` is trusted, already-safe HTML (matching every existing
  // *.html call site's own convention of escaping raw text at the point
  // it's read, before it ever reaches a template slot) - metricCard()
  // does NOT re-escape it, since callers that already ran it through
  // escapeHtml() would otherwise get visibly double-escaped output
  // (e.g. an apostrophe rendering as the literal text "&#39;").
  function metricCard(label, value, opts) {
    opts = opts || {};
    let valueHtml;
    if (opts.state === "loading") valueHtml = `<span class="muted">Loading&hellip;</span>`;
    else if (opts.state === "error") valueHtml = `<span class="muted" style="color:var(--red);">&mdash;</span>`;
    else valueHtml = value;
    const deltaHtml = opts.delta
      ? `<div style="font-size:12px; margin-top:2px; color:${opts.delta.positive ? "var(--green)" : "var(--red)"};">${opts.delta.positive ? "+" : ""}${escapeHtml(opts.delta.value)}</div>`
      : "";
    const onClass = opts.on !== undefined ? (opts.on ? "on" : "") : (opts.state === "loading" || opts.state === "error" ? "" : "on");
    return `
      <div class="mm-tile ${onClass}">
        <div class="mm-tile-label">${escapeHtml(label)}</div>
        <div class="mm-tile-value">${valueHtml}</div>
        ${deltaHtml}
      </div>
    `;
  }

  // ---- Shared table states: loading, empty, error, populated ----
  // Replaces the near-identical <tr><td colspan="N" class="empty">...
  // pattern that was hand-written slightly differently on every page's
  // own table (sessions history, funds list, journeys, etc.).
  function tableLoadingRow(colspan) { return `<tr><td colspan="${colspan}" class="empty">Loading&hellip;</td></tr>`; }
  function tableEmptyRow(colspan, message) { return `<tr><td colspan="${colspan}" class="empty">${escapeHtml(message || "Nothing here yet.")}</td></tr>`; }
  function tableErrorRow(colspan, message) { return `<tr><td colspan="${colspan}" class="empty">Failed to load: ${escapeHtml(message)}</td></tr>`; }

  // ---- ToggleRow: on/off, disabled, inherited, overridden ----
  // `inherited` shows the source value it would use if left blank/off
  // (e.g. a per-account override falling back to a global default) -
  // COMPONENT_SPEC.md's explicit requirement to "show source of
  // inherited value" rather than just displaying a blank field.
  function toggleRowInheritedNote(inheritedFrom, inheritedValue) {
    if (inheritedFrom === undefined) return "";
    return `<span class="muted" style="font-size:11px;">inherits ${escapeHtml(String(inheritedValue))} from ${escapeHtml(inheritedFrom)}</span>`;
  }

  return {
    init, logout, fetchJSON, isDeveloperMode, _confirmPendingTrade, _rejectPendingTrade,
    _toggleNotifDropdown, _markAllNotifsRead, subscribeEvents, toggleTheme, confirm: confirmDialog,
    emptyPanel,
    statusBadge, signalRowState, metricCard,
    tableLoadingRow, tableEmptyRow, tableErrorRow,
    toggleRowInheritedNote,
  };
})();
