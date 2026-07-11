// AXIM desktop shell (docs/AXIM_APP_PLAN.md Phase 6, extended for
// docs/AXIM_REMOTE_ACCESS.md's Remote Client) - a thin Tauri window that
// either (a) spawns the EXISTING local FastAPI control UI + Telegram
// listener processes and points itself at 127.0.0.1, exactly as before,
// or (b) points itself at a configured remote AXIM Server's address and
// spawns nothing locally at all. Local mode does not bundle a Python
// runtime: it launches the same venv\Scripts\python.exe the manual
// `python -m uvicorn ...` / `python core/telegram_listener.py` commands
// already use (see DEPLOYMENT.md), so local mode only works on a machine
// with the AXIM project checkout and its venv already set up - a known,
// documented limitation, not a fully standalone installer yet. Remote
// mode has no such requirement - it's just a browser-like window.
use std::env;
use std::fs;
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::Manager;

struct ManagedChildren(Mutex<Vec<Child>>);

#[derive(Serialize, Deserialize, Clone)]
struct RemoteConfig {
    // False until the user has gone through the launcher once (or the
    // config file doesn't exist yet) - distinguishes "brand new install,
    // show the picker" from "already chose local mode explicitly".
    #[serde(default)]
    configured: bool,
    #[serde(default = "default_mode")]
    mode: String, // "local" | "remote"
    #[serde(default)]
    server_address: Option<String>,
}

fn default_mode() -> String {
    "local".to_string()
}

// Deliberately NOT #[derive(Default)] - that would default `mode` to
// String::default() ("") rather than "local", since serde's
// `default = "default_mode"` attribute only governs missing-field
// deserialization, not the Default trait derive. An empty mode string
// would leave both radio buttons unchecked in the launcher UI.
impl Default for RemoteConfig {
    fn default() -> Self {
        RemoteConfig {
            configured: false,
            mode: default_mode(),
            server_address: None,
        }
    }
}

#[derive(Serialize)]
struct TargetInfo {
    url: String,
}

// AXIM_PROJECT_ROOT lets this be pointed at any checkout; falls back to
// this project's own known location for local dev/testing.
fn project_root() -> PathBuf {
    if let Ok(p) = env::var("AXIM_PROJECT_ROOT") {
        return PathBuf::from(p);
    }
    PathBuf::from(r"C:\AXIM")
}

// Local mode's API host/port mirror config/settings.py's API_BIND_HOST /
// API_BIND_PORT (read from the same .env file) so this app still finds
// the API if an operator has changed the bind address - the same values
// scripts/install_api_scheduled_task.ps1 resolves for the Scheduled Task.
// Mirrors python-dotenv's quote handling closely enough for these two
// keys - strips a single matching pair of surrounding quotes, same as
// what config/settings.py effectively sees via load_dotenv().
fn strip_env_quotes(value: &str) -> String {
    let value = value.trim();
    let bytes = value.as_bytes();
    if bytes.len() >= 2
        && ((bytes[0] == b'"' && bytes[bytes.len() - 1] == b'"')
            || (bytes[0] == b'\'' && bytes[bytes.len() - 1] == b'\''))
    {
        return value[1..value.len() - 1].to_string();
    }
    value.to_string()
}

fn local_api_bind(root: &PathBuf) -> (String, u16) {
    let mut host = "127.0.0.1".to_string();
    let mut port: u16 = 8090;
    if let Ok(contents) = fs::read_to_string(root.join(".env")) {
        for line in contents.lines() {
            let line = line.trim();
            if let Some(value) = line.strip_prefix("API_BIND_HOST=") {
                host = strip_env_quotes(value);
            } else if let Some(value) = line.strip_prefix("API_BIND_PORT=") {
                if let Ok(p) = strip_env_quotes(value).parse::<u16>() {
                    port = p;
                }
            }
        }
    }
    (host, port)
}

fn spawn_axim_processes(root: &PathBuf, api_host: &str, api_port: u16) -> Vec<Child> {
    let python = root.join("venv").join("Scripts").join("python.exe");
    let mut children = Vec::new();
    let port_str = api_port.to_string();

    match Command::new(&python)
        .args([
            "-m", "uvicorn", "api.main:app",
            "--host", api_host, "--port", port_str.as_str(),
        ])
        .current_dir(root)
        .spawn()
    {
        Ok(child) => children.push(child),
        Err(e) => eprintln!("axim-desktop: failed to start API process: {e}"),
    }

    match Command::new(&python)
        .arg("core/telegram_listener.py")
        .current_dir(root)
        .spawn()
    {
        Ok(child) => children.push(child),
        Err(e) => eprintln!("axim-desktop: failed to start listener process: {e}"),
    }

    children
}

// Force-killing core/telegram_listener.py (which is what closing this
// window does to a locally-spawned listener - there is no reliable way
// to deliver a specific child process a Ctrl+C-equivalent on Windows)
// skips its own clean-shutdown path that closes every browser tab it
// opened, exactly the scenario USER_GUIDE.md's "Stopping AXIM correctly"
// warns about and tells an operator to run cleanup_axim_chrome.ps1 -Kill
// for manually. Since this window is the actual "simple launch" surface
// for AXIM Core (not a terminal an operator is watching), that manual
// step would silently never happen - so run the same script automatically
// whenever local-mode processes were actually spawned and are being torn
// down. No-op (and safe to call) when nothing was spawned, e.g. remote
// mode, since `children` is empty there.
fn kill_all(root: &PathBuf, children: &mut Vec<Child>) {
    if children.is_empty() {
        return;
    }
    for child in children.iter_mut() {
        let _ = child.kill();
        let _ = child.wait();
    }
    let cleanup_script = root.join("scripts").join("cleanup_axim_chrome.ps1");
    let _ = Command::new("powershell")
        .arg("-NoProfile")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-File")
        .arg(&cleanup_script)
        .arg("-Kill")
        .current_dir(root)
        .spawn();
}

// A fixed sleep isn't reliable here - uvicorn's cold-start time varies with
// system load (module imports, DB init), so this polls until the port
// actually accepts a connection instead of guessing a delay. Without this,
// the window's navigation to the local API can race a uvicorn that isn't
// listening yet, WebView2 shows a blank/connection-refused page, and it
// never retries.
fn wait_for_api_ready(host: &str, port: u16, timeout: Duration) {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if TcpStream::connect((host, port)).is_ok() {
            return;
        }
        thread::sleep(Duration::from_millis(150));
    }
    eprintln!("axim-desktop: {host}:{port} did not become ready within {timeout:?}, loading window anyway");
}

fn config_file_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_config_dir()
        .map_err(|e| format!("could not resolve app config dir: {e}"))?;
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir.join("remote_client_config.json"))
}

fn load_remote_config(app: &tauri::AppHandle) -> RemoteConfig {
    let Ok(path) = config_file_path(app) else {
        return RemoteConfig::default();
    };
    match fs::read_to_string(&path) {
        Ok(contents) => serde_json::from_str(&contents).unwrap_or_default(),
        Err(_) => RemoteConfig::default(),
    }
}

fn save_remote_config(app: &tauri::AppHandle, config: &RemoteConfig) -> Result<(), String> {
    let path = config_file_path(app)?;
    let json = serde_json::to_string_pretty(config).map_err(|e| e.to_string())?;
    fs::write(&path, json).map_err(|e| e.to_string())
}

#[tauri::command]
fn get_remote_config(app: tauri::AppHandle) -> RemoteConfig {
    load_remote_config(&app)
}

#[tauri::command]
fn set_remote_config(
    app: tauri::AppHandle,
    mode: String,
    server_address: Option<String>,
) -> Result<(), String> {
    if mode != "local" && mode != "remote" {
        return Err(format!("invalid mode: {mode}"));
    }
    let server_address = server_address.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    if mode == "remote" && server_address.is_none() {
        return Err("a server address is required for Remote mode".to_string());
    }
    let config = RemoteConfig {
        configured: true,
        mode,
        server_address,
    };
    save_remote_config(&app, &config)
}

// Called once by the launcher screen after it resolves which mode to use.
// Local-mode process spawning is guarded so a launcher reload (e.g. after
// clicking Save) never spawns a second uvicorn/listener pair on top of an
// already-running one for this app instance.
#[tauri::command]
fn resolve_and_launch(app: tauri::AppHandle) -> Result<TargetInfo, String> {
    let config = load_remote_config(&app);

    if config.mode == "remote" {
        let addr = config
            .server_address
            .clone()
            .ok_or_else(|| "Remote mode is selected but no server address is configured".to_string())?;
        let url = if addr.starts_with("http://") || addr.starts_with("https://") {
            addr
        } else {
            format!("http://{addr}")
        };
        return Ok(TargetInfo { url });
    }

    let root = project_root();
    let (api_host, api_port) = local_api_bind(&root);

    let state = app.state::<ManagedChildren>();
    {
        let mut children = state.0.lock().unwrap();
        if children.is_empty() {
            *children = spawn_axim_processes(&root, &api_host, api_port);
        }
    }
    wait_for_api_ready(&api_host, api_port, Duration::from_secs(30));
    Ok(TargetInfo {
        url: format!("http://{api_host}:{api_port}"),
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(ManagedChildren(Mutex::new(Vec::new())))
        .invoke_handler(tauri::generate_handler![
            get_remote_config,
            set_remote_config,
            resolve_and_launch
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if let Some(state) = window.app_handle().try_state::<ManagedChildren>() {
                    let mut children = state.0.lock().unwrap();
                    kill_all(&project_root(), &mut children);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
