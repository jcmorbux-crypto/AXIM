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
  };

  const NAV_ITEMS = [
    { key: "dashboard", label: "Mission Control", href: "/dashboard", icon: ICONS.dashboard },
    { key: "sessions", label: "Trading Sessions", href: "/sessions", icon: ICONS.sessions },
    { key: "telegram", label: "Signal Sources", href: "/telegram", icon: ICONS.telegram },
    { key: "inspector", label: "Signal Inspector", href: "/inspector", icon: ICONS.inspector },
    { key: "money", label: "Risk Engine", href: "/legacy#money", icon: ICONS.money },
    { key: "trades", label: "Trade Center", href: "/legacy#trades", icon: ICONS.trades },
    { key: "stats", label: "Performance", href: "/legacy#trades", icon: ICONS.stats },
    { key: "pocketoption", label: "Broker", href: "/legacy#po", icon: ICONS.pocketoption },
    { key: "users", label: "Users", href: "/users", icon: ICONS.users, adminOnly: true },
    { key: "logs", label: "Logs", href: "/legacy#logs", icon: ICONS.logs },
    { key: "settings", label: "Settings", href: "/legacy#money", icon: ICONS.settings },
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

  async function init(opts) {
    let user;
    try {
      user = await fetchJSON("/api/auth/me");
    } catch (e) {
      window.location.href = "/login";
      return null;
    }
    const shellRoot = document.getElementById("app-shell");
    shellRoot.classList.add("app-shell");
    const sidebar = document.createElement("nav");
    sidebar.className = "sidebar";
    sidebar.id = "sidebar";
    shellRoot.insertBefore(sidebar, shellRoot.firstChild);
    renderSidebar(sidebar, user, opts.active);
    return user;
  }

  return { init, logout, fetchJSON };
})();
