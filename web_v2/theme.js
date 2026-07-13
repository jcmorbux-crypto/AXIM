// Theme toggle - Light / Dark / System, persisted in localStorage.
// data-theme="dark"|"light" on <html> forces a theme; no attribute
// (or the value "system") means design-system.css's prefers-color-scheme
// media query decides. Applied before paint via the inline snippet each
// page includes in <head> (see THEME_INIT_SNIPPET below) to avoid a
// flash of the wrong theme on load.

const THEME_KEY = "axim-theme"; // "light" | "dark" | "system"

function getStoredTheme() {
  return localStorage.getItem(THEME_KEY) || "system";
}

function applyTheme(pref) {
  const root = document.documentElement;
  if (pref === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", pref);
}

function setTheme(pref) {
  localStorage.setItem(THEME_KEY, pref);
  applyTheme(pref);
  document.querySelectorAll("[data-theme-option]").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.themeOption === pref);
  });
}

function initThemeToggle(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const current = getStoredTheme();
  el.innerHTML = `
    <button data-theme-option="light" class="${current === "light" ? "active" : ""}" title="Light mode">&#9728;</button>
    <button data-theme-option="dark" class="${current === "dark" ? "active" : ""}" title="Dark mode">&#9789;</button>
    <button data-theme-option="system" class="${current === "system" ? "active" : ""}" title="Match system">&#9881;</button>
  `;
  el.querySelectorAll("[data-theme-option]").forEach(btn => {
    btn.addEventListener("click", () => setTheme(btn.dataset.themeOption));
  });
}

// Inline, synchronous snippet each page's <head> runs before design-system.css
// paints anything - prevents a flash of the wrong theme on load. Pages
// embed this literally (can't be an external <script src> and still run
// before first paint reliably), theme.js just documents/owns the logic.
const THEME_INIT_SNIPPET = `
  (function(){
    var t = localStorage.getItem('axim-theme') || 'system';
    if (t !== 'system') document.documentElement.setAttribute('data-theme', t);
  })();
`;
