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

// Matches api/main.py's own HEARTBEAT_STALE_THRESHOLD_SECONDS (3x the
// listener's 30s heartbeat interval) - the same self-reported "is the
// listener alive right now" freshness check that endpoint already trusts,
// read directly from data/axim.db here since this runs before (or
// instead of) spawning anything that could talk to the API.
const HEARTBEAT_STALE_THRESHOLD_SECONDS: i64 = 45;

// Found live (2026-07-10): a project checkout can have telegram_listener.py
// running (a Scheduled Task, a soak test, a manually-started terminal)
// WITHOUT api/main.py also running - they're independent processes, not a
// package deal, even though local mode has always spawned both together as
// one unit. Checking only the API port (as an earlier version of this fix
// did) missed exactly that case: the port looked free, so it spawned a
// second telegram_listener.py anyway, which then fought the real one over
// the single persistent Chrome profile lock
// (`sessions/pocket_browser`) and error-looped every 30-60s until killed
// by hand. Each process now gets its own liveness check instead.
fn listener_heartbeat_is_fresh(root: &PathBuf) -> bool {
    let db_path = root.join("data").join("axim.db");
    if !db_path.exists() {
        return false;
    }
    let conn = match rusqlite::Connection::open_with_flags(
        &db_path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
    ) {
        Ok(c) => c,
        Err(_) => return false,
    };
    let updated_at: Result<String, _> = conn.query_row(
        "SELECT updated_at FROM ui_listener_heartbeat WHERE id = 1",
        [],
        |row| row.get(0),
    );
    let Ok(updated_at) = updated_at else { return false };
    let Ok(updated_at) = chrono::NaiveDateTime::parse_from_str(&updated_at, "%Y-%m-%dT%H:%M:%S%.f")
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(&updated_at, "%Y-%m-%dT%H:%M:%S"))
    else {
        return false;
    };
    let age = chrono::Local::now().naive_local() - updated_at;
    age.num_seconds() >= 0 && age.num_seconds() <= HEARTBEAT_STALE_THRESHOLD_SECONDS
}

fn spawn_axim_processes(root: &PathBuf, api_host: &str, api_port: u16) -> Vec<Child> {
    let python = root.join("venv").join("Scripts").join("python.exe");
    let mut children = Vec::new();
    let port_str = api_port.to_string();

    if is_port_open(api_host, api_port) {
        eprintln!(
            "axim-desktop: {api_host}:{api_port} already has a server running - not starting a second one"
        );
    } else {
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
    }

    if listener_heartbeat_is_fresh(root) {
        eprintln!(
            "axim-desktop: a telegram_listener.py is already reporting a fresh heartbeat - not starting a second one"
        );
    } else {
        match Command::new(&python)
            .arg("core/telegram_listener.py")
            .current_dir(root)
            .spawn()
        {
            Ok(child) => children.push(child),
            Err(e) => eprintln!("axim-desktop: failed to start listener process: {e}"),
        }
    }

    children
}

fn kill_all(children: &mut Vec<Child>) {
    for child in children.iter_mut() {
        let _ = child.kill();
        let _ = child.wait();
    }
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

// Found live (2026-07-10): launching local mode against a project
// checkout that ALREADY has a listener running (a Scheduled Task, a
// soak test, or just a manually-started terminal) had no detection for
// this at all - it always spawned a second uvicorn/telegram_listener.py
// pair, which then fought the first over the same persistent Chrome
// profile lock (`sessions/pocket_browser`) and error-looped
// ("Opening in existing browser session") until manually killed. The
// existing ManagedChildren guard only prevents THIS app instance from
// double-spawning across a launcher reload - it has no idea about a
// process outside this app's own lifetime. A single already-listening
// check before spawning covers both cases with one primitive.
fn is_port_open(host: &str, port: u16) -> bool {
    TcpStream::connect((host, port)).is_ok()
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

    // spawn_axim_processes independently checks liveness for the API port
    // and the listener's own heartbeat before starting each one - either,
    // both, or neither may already be running (they're independent
    // processes, not a package deal - see its own comment). Whatever it
    // decides not to spawn is simply never added to `children`, so this
    // app's Stop-on-exit cleanup correctly never touches a process it
    // didn't start.
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
                    kill_all(&mut children);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
