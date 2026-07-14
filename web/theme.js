// Theme toggle - Light / Dark / System, persisted in localStorage.
// data-theme="dark"|"light" on <html> forces a theme; no attribute (or
// the value "system") means theme.css's prefers-color-scheme media
// query decides. Applied before paint via an inline snippet each
// page's <head> carries directly (THEME_INIT_SNIPPET below, for
// reference - the actual copy lives inline in each page since an
// external <script src> can't run early enough to avoid a flash of
// the wrong theme on load).

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
    <button data-theme-option="light" class="${current === "light" ? "active" : ""}" title="Light mode" aria-label="Light mode">&#9728;</button>
    <button data-theme-option="dark" class="${current === "dark" ? "active" : ""}" title="Dark mode" aria-label="Dark mode">&#9789;</button>
    <button data-theme-option="system" class="${current === "system" ? "active" : ""}" title="Match system" aria-label="Match system">&#9881;</button>
  `;
  el.querySelectorAll("[data-theme-option]").forEach(btn => {
    btn.addEventListener("click", () => setTheme(btn.dataset.themeOption));
  });
}

// Reference copy - each page's own inline <head> snippet (see
// web/shell.js's THEME_INIT_SNIPPET export) must match this exactly.
const THEME_INIT_SNIPPET = `
  (function(){
    var t = localStorage.getItem('axim-theme') || 'system';
    if (t !== 'system') document.documentElement.setAttribute('data-theme', t);
  })();
`;
