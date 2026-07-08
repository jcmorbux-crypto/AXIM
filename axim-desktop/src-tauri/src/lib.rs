// AXIM desktop shell (docs/AXIM_APP_PLAN.md Phase 6) - a thin Tauri
// window around the EXISTING FastAPI control UI + Telegram listener.
// This does not bundle a Python runtime: it launches the same
// venv\Scripts\python.exe the manual `python -m uvicorn ...` / `python
// core/telegram_listener.py` commands already use (see DEPLOYMENT.md),
// so it only works on a machine with the AXIM project checkout and its
// venv already set up - a known, documented limitation, not a fully
// standalone installer yet.
use std::env;
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::Manager;

struct ManagedChildren(Mutex<Vec<Child>>);

// AXIM_PROJECT_ROOT lets this be pointed at any checkout; falls back to
// this project's own known location for local dev/testing.
fn project_root() -> PathBuf {
    if let Ok(p) = env::var("AXIM_PROJECT_ROOT") {
        return PathBuf::from(p);
    }
    PathBuf::from(r"C:\AXIM")
}

fn spawn_axim_processes(root: &PathBuf) -> Vec<Child> {
    let python = root.join("venv").join("Scripts").join("python.exe");
    let mut children = Vec::new();

    match Command::new(&python)
        .args(["-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8090"])
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

fn kill_all(children: &mut Vec<Child>) {
    for child in children.iter_mut() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

// A fixed sleep isn't reliable here - uvicorn's cold-start time varies with
// system load (module imports, DB init), so this polls until the port
// actually accepts a connection instead of guessing a delay. Without this,
// the window's initial navigation can race a uvicorn that isn't listening
// yet, WebView2 shows a blank/connection-refused page, and it never retries.
fn wait_for_api_ready(timeout: Duration) {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if TcpStream::connect(("127.0.0.1", 8090)).is_ok() {
            return;
        }
        thread::sleep(Duration::from_millis(150));
    }
    eprintln!("axim-desktop: API did not become ready within {timeout:?}, loading window anyway");
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let root = project_root();
            let children = spawn_axim_processes(&root);
            app.manage(ManagedChildren(Mutex::new(children)));
            // Block until uvicorn actually accepts connections before the
            // main window (its URL is declared in tauri.conf.json) tries
            // to load it - see wait_for_api_ready's doc comment.
            wait_for_api_ready(Duration::from_secs(30));
            Ok(())
        })
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
