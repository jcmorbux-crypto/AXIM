const { invoke } = window.__TAURI__.core;

// Launcher screen (Client/Server Step 6 - docs/AXIM_REMOTE_ACCESS.md).
// On every start: if a mode is already configured, show a brief
// "Connecting..." banner with a "Change" escape hatch, then navigate this
// same window to the resolved target (local spawn, or a remote server -
// resolve_and_launch() on the Rust side never spawns local processes in
// remote mode). If nothing is configured yet (first run), skip straight
// to the picker form.
const AUTO_CONNECT_DELAY_MS = 1500;

let autoConnectTimer = null;

function showForm(config) {
  document.getElementById("auto-connect").style.display = "none";
  const form = document.getElementById("config-form");
  form.style.display = "";

  const mode = (config && config.mode) || "local";
  const radios = form.querySelectorAll('input[name="mode"]');
  radios.forEach(r => { r.checked = r.value === mode; });
  document.getElementById("server-address").value = (config && config.server_address) || "";
  document.getElementById("server-address-field").style.display = mode === "remote" ? "" : "none";
}

function showAutoConnect(config) {
  const label = config.mode === "remote"
    ? `Connecting to ${config.server_address}...`
    : "Starting AXIM on this PC...";
  document.getElementById("auto-connect-message").textContent = label;
  document.getElementById("auto-connect").style.display = "";
  document.getElementById("config-form").style.display = "none";
}

async function launch() {
  document.getElementById("config-error").style.display = "none";
  try {
    const target = await invoke("resolve_and_launch");
    window.location.href = target.url;
  } catch (err) {
    const errorEl = document.getElementById("config-error");
    errorEl.textContent = `Failed to start: ${err}`;
    errorEl.style.display = "";
    showForm(await invoke("get_remote_config"));
  }
}

async function init() {
  let config;
  try {
    config = await invoke("get_remote_config");
  } catch (err) {
    // Both #auto-connect and #config-form start display:none in
    // index.html - if this throws before either gets shown, the window
    // is otherwise just a blank title with no error and no way to
    // proceed. Fall back to the picker form (a safe, always-actionable
    // default) with an error message, same pattern as web/login.html's
    // boot() falling back to the login view.
    showForm({});
    const errorEl = document.getElementById("config-error");
    errorEl.textContent = `Failed to load configuration: ${err}`;
    errorEl.style.display = "";
    return;
  }
  const forceConfigure = new URLSearchParams(window.location.search).has("configure");

  if (!config.configured || forceConfigure) {
    showForm(config);
    return;
  }

  showAutoConnect(config);
  document.getElementById("change-link").addEventListener("click", () => {
    clearTimeout(autoConnectTimer);
    showForm(config);
  });
  autoConnectTimer = setTimeout(launch, AUTO_CONNECT_DELAY_MS);
}

window.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("config-form");
  form.querySelectorAll('input[name="mode"]').forEach(radio => {
    radio.addEventListener("change", () => {
      const mode = form.querySelector('input[name="mode"]:checked').value;
      document.getElementById("server-address-field").style.display = mode === "remote" ? "" : "none";
    });
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const mode = form.querySelector('input[name="mode"]:checked').value;
    const serverAddress = document.getElementById("server-address").value.trim();
    const errorEl = document.getElementById("config-error");
    errorEl.style.display = "none";

    if (mode === "remote" && !serverAddress) {
      errorEl.textContent = "Enter the AXIM Server's address first.";
      errorEl.style.display = "";
      return;
    }

    try {
      await invoke("set_remote_config", { mode, serverAddress: mode === "remote" ? serverAddress : null });
      showAutoConnect({ mode, server_address: serverAddress });
      launch();
    } catch (err) {
      errorEl.textContent = String(err);
      errorEl.style.display = "";
    }
  });

  init();
});
