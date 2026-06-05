/**
 * Python sidecar management.
 *
 * In v0.1 the sidecar is started as a plain `uv run` process. We don't depend
 * on tauri-plugin-shell's binary bundling — that requires a real packaged
 * Python binary, which we'll wire up in v0.2 (PyInstaller / pyoxidizer).
 *
 * Lifecycle:
 *   - On Tauri startup, spawn `uv run prism-sidecar` from the python/ dir
 *   - Stream stdout/stderr to the Tauri log
 *   - The React app talks to http://127.0.0.1:8765 directly (loopback only)
 *
 * Failure mode: if uv / python isn't installed, log a friendly message but
 * keep the Tauri app running. The user can start the sidecar manually.
 */

use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::thread;

use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime};

pub const SIDECAR_HOST: &str = "127.0.0.1";
pub const SIDECAR_PORT: u16 = 8765;
pub const SIDECAR_URL: &str = "http://127.0.0.1:8765";

/// Optional child handle — kept around so we can kill it on shutdown later.
#[allow(dead_code)]
pub struct SidecarState(pub Mutex<Option<std::process::Child>>);

#[derive(Serialize)]
pub struct SidecarInfo {
    pub url: String,
    pub port: u16,
    pub host: String,
}

#[tauri::command]
pub fn get_sidecar_url() -> SidecarInfo {
    SidecarInfo {
        url: SIDECAR_URL.to_string(),
        port: SIDECAR_PORT,
        host: SIDECAR_HOST.to_string(),
    }
}

pub fn spawn<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    // Resolve python/ path relative to the Tauri working dir.
    // In dev (cargo tauri dev) cwd is src-tauri/, so python/ is at ../python.
    let python_dir = std::env::current_dir()
        .map(|p| p.join("..").join("python"))
        .unwrap_or_else(|_| std::path::PathBuf::from("../python"));

    let mut cmd = Command::new("uv");
    cmd.args(["run", "--directory", python_dir.to_str().unwrap_or("../python"), "prism-sidecar"]);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    cmd.stdin(Stdio::null());

    // On Windows, hide the console window of the child process.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let mut child = cmd.spawn().map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::Other,
            format!("could not spawn `uv run prism-sidecar`: {e}"),
        )
    })?;

    // Stream stdout to the Tauri log.
    if let Some(stdout) = child.stdout.take() {
        thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                println!("[sidecar] {line}");
            }
        });
    }

    // Stream stderr to the Tauri log.
    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                eprintln!("[sidecar] {line}");
            }
        });
    }

    // Stash the child handle so we can clean it up later (Drop, signal, etc.).
    app.manage(SidecarState(Mutex::new(Some(child))));

    Ok(())
}
