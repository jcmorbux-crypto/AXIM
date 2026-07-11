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
  document.getElementById("splash-mark").classList.remove("connecting");

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
  document.getElementById("splash-mark").classList.add("connecting");
}

// Gap found: navigating straight to target.url with no reachability check
// meant a wrong/unreachable remote address (Tailscale down, typo'd
// hostname, server not running) dropped the user into WebView2's own
// native browser error page - completely outside AXIM's UI, with no
// "Change server settings" link to get back to the picker. This probes
// first so that failure surfaces as a normal, recoverable error on the
// launcher screen instead. `no-cors` mode is enough - the response body
// is opaque either way (we only care that ANY response came back, not
// what it says), and fetch only rejects on a genuine network-level
// failure (DNS, connection refused, timeout), which is exactly the
// failure mode this exists to catch. Local mode's own resolve_and_launch
// already waits up to 30s for the API port on the Rust side, but that
// wait can still time out and return anyway (its own comment: "loading
// window anyway") - this check catches that case too, not just remote.
async function probeReachable(url, timeoutMs) {
  try {
    await fetch(url, { mode: "no-cors", signal: AbortSignal.timeout(timeoutMs) });
    return true;
  } catch (err) {
    return false;
  }
}

async function launch() {
  document.getElementById("config-error").style.display = "none";
  try {
    const target = await invoke("resolve_and_launch");
    const reachable = await probeReachable(target.url, 6000);
    if (!reachable) {
      throw new Error(`could not reach ${target.url} - check the address, that the AXIM Server is running, and (for a remote server) that Tailscale is connected`);
    }
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
