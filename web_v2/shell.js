// AXIM Trader V2 shell - navigation + fetch helper shared by every preview
// page. Deliberately tiny (no framework) - matches the production
// codebase's own "dependency-light" philosophy, just for a fresh IA.

const NAV_ITEMS = [
  { id: "home", label: "Home", href: "home.html" },
  { id: "portfolio", label: "Portfolio", href: "portfolio.html" },
  { id: "signals", label: "Signals", href: "signals.html" },
  { id: "sessions", label: "Sessions", href: "sessions.html" },
  { id: "strategy", label: "Strategy", href: "strategy.html" },
  { id: "settings", label: "Settings", href: "settings.html" },
];

const MOBILE_NAV_ITEMS = ["home", "sessions", "signals"]; // P5: 3 + "More"

function renderShell(activeId, title, subtitle) {
  document.body.insertAdjacentHTML("afterbegin", `
    <div class="preview-badge">Preview Mode &middot; <b>Read-only</b> &middot; AXIM Trader V2 (UI Vision) &middot; No real trades are ever placed here</div>
  `);

  const sidebar = NAV_ITEMS.map(i => `
    <a class="nav-item ${i.id === activeId ? "active" : ""}" href="${i.href}">${i.label}</a>
  `).join("");

  const mobileNav = NAV_ITEMS.filter(i => MOBILE_NAV_ITEMS.includes(i.id)).map(i => `
    <a class="item ${i.id === activeId ? "active" : ""}" href="${i.href}">${i.label}</a>
  `).join("") + `<a class="item">More</a>`;

  document.body.insertAdjacentHTML("beforeend", `
    <div class="mobile-nav">${mobileNav}</div>
  `);

  const shell = document.getElementById("shell-root");
  shell.innerHTML = `
    <div class="sidebar">
      <div class="brand"><span class="mark"></span> AXIM Trader</div>
      ${sidebar}
    </div>
    <div class="main">
      <h1>${title}</h1>
      <div class="subtitle">${subtitle}</div>
      <div id="page-content"></div>
    </div>
  `;
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
