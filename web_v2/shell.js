// AXIM Trader V2 shell - navigation + fetch helper shared by every preview
// page. Deliberately tiny (no framework) - matches the production
// codebase's own "dependency-light" philosophy.
//
// IA reorganized 2026-07-13 per direct user spec: primary nav trimmed to
// daily-use destinations only; everything occasional/advanced moved under
// a "More" menu. Money Management Studio (strategy.html) is real, built
// content - it moved to More because it's a setup task, not a daily
// destination, not because it's less finished than anything else here.

const NAV_ITEMS = [
  { id: "home", label: "Home", href: "home.html" },
  { id: "sessions", label: "Sessions", href: "sessions.html" },
  { id: "funds", label: "Funds", href: "portfolio.html" },
  { id: "sources", label: "Sources", href: "signals.html" },
  { id: "strategy-lab", label: "Strategy Lab", href: "strategy_lab.html" },
  { id: "performance", label: "Performance", href: "performance.html" },
];

const MORE_ITEMS = [
  { id: "money-mgmt", label: "Money Management", href: "strategy.html" },
  { id: "automation", label: "Automation Studio", href: "automation_studio.html" },
  { id: "inspector", label: "Signal Inspector", href: "signal_inspector.html" },
  { id: "broker", label: "Broker Accounts", href: "broker_accounts.html" },
  { id: "logs", label: "Logs", href: "logs.html" },
  { id: "users", label: "Users", href: "users.html" },
  { id: "settings", label: "Settings", href: "settings.html" },
  { id: "help", label: "Help", href: "help.html" },
];

// Mobile bottom nav: 4 primary destinations + More (P5 - a 5-across bar
// is the standard mobile pattern; 6 primary items don't all fit).
const MOBILE_NAV_IDS = ["home", "sessions", "funds", "sources"];

function isMoreActive(activeId) {
  return MORE_ITEMS.some(i => i.id === activeId);
}

function renderShell(activeId, title, subtitle) {
  document.body.insertAdjacentHTML("afterbegin", `
    <div class="preview-badge">Preview Mode &middot; <b>Read-only</b> &middot; AXIM Trader V2 (UI Vision) &middot; No real trades are ever placed here</div>
  `);

  const sidebar = NAV_ITEMS.map(i => `
    <a class="nav-item ${i.id === activeId ? "active" : ""}" href="${i.href}">${i.label}</a>
  `).join("");

  const moreOpen = isMoreActive(activeId);
  const moreList = MORE_ITEMS.map(i => `
    <a class="nav-item more-item ${i.id === activeId ? "active" : ""}" href="${i.href}">${i.label}</a>
  `).join("");

  const mobileNav = NAV_ITEMS.filter(i => MOBILE_NAV_IDS.includes(i.id)).map(i => `
    <a class="item ${i.id === activeId ? "active" : ""}" href="${i.href}">${i.label}</a>
  `).join("") + `<a class="item ${moreOpen ? "active" : ""}" href="#" onclick="document.getElementById('more-sheet').classList.toggle('open'); return false;">More</a>`;

  document.body.insertAdjacentHTML("beforeend", `
    <div class="mobile-nav">${mobileNav}</div>
    <div class="more-sheet" id="more-sheet">
      <div class="more-sheet-inner">
        <div class="more-sheet-title">More</div>
        ${moreList}
      </div>
    </div>
  `);

  const shell = document.getElementById("shell-root");
  shell.innerHTML = `
    <div class="sidebar">
      <div class="brand"><span class="mark"></span> AXIM Trader</div>
      ${sidebar}
      <div class="nav-more ${moreOpen ? "open" : ""}">
        <div class="nav-more-toggle" onclick="this.parentElement.classList.toggle('open')">
          More <span class="chev">&#9662;</span>
        </div>
        <div class="nav-more-list">${moreList}</div>
      </div>
      <div class="theme-toggle-wrap">
        <div class="theme-toggle" id="theme-toggle-sidebar"></div>
      </div>
    </div>
    <div class="main">
      <h1>${title}</h1>
      <div class="subtitle">${subtitle}</div>
      <div id="page-content"></div>
    </div>
  `;

  if (typeof initThemeToggle === "function") initThemeToggle("theme-toggle-sidebar");
}

async function fetchPreview(path) {
  const res = await fetch(`/api/preview${path}`);
  if (!res.ok) throw new Error(`preview API ${path} -> HTTP ${res.status}`);
  return res.json();
}

function fmtMoney(n) {
  if (n === null || n === undefined) return "&mdash;";
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

function moneyClass(n) {
  if (n === null || n === undefined) return "";
  return n > 0 ? "up" : n < 0 ? "down" : "";
}

function fmtPct(n) {
  if (n === null || n === undefined) return "&mdash;";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

// Shared preview-safe control-click feedback (Start/Pause/Stop buttons
// across preview pages) - confirms the click registered without ever
// pretending a real session started/stopped. preview_server.py exposes
// no write endpoints at all, so this is structurally, not just visually,
// incapable of touching real trading state.
let _toastTimer = null;
function showToast(message) {
  let el = document.getElementById("preview-toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "preview-toast";
    el.className = "preview-toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove("show"), 2400);
}
