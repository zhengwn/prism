/**
 * Python sidecar management.
 *
 * Lifecycle:
 *   - On Tauri startup, spawn `uv run prism-sidecar` from the python/ dir.
 *   - Read the active LLM provider from the OS keychain.
 *   - Inject the appropriate API key (and base URL for ollama/custom) into
 *     the child process's environment.
 *   - Persist a small JSON marker (`~/.prism/active_provider.json`) so the
 *     sidecar can pick up the active provider on its own at startup.
 *   - Stream stdout/stderr to the Tauri log.
 *   - On `set_llm_config` (provider change), kill the child + respawn so the
 *     new env vars take effect (~2 s).
 *
 * Failure mode: if uv / python isn't installed, log a friendly message but
 * keep the Tauri app running. The user can start the sidecar manually.
 *
 * v0.2a → v0.2a-providers: env injection is now provider-aware. The contract
 * with the Python sidecar is unchanged for the deepseek case (still
 * `DEEPSEEK_API_KEY=…`); the new providers add `OPENAI_API_KEY` /
 * `ANTHROPIC_API_KEY` / `OLLAMA_API_BASE` / `OPENAI_API_BASE` /
 * `PRISM_ACTIVE_PROVIDER` as needed. The sidecar reads whichever env vars it
 * needs to know the active provider.
 */

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::thread;

use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime};

use crate::secrets;
use crate::secrets::{LlmConfigInput, LlmConfigResponse};

pub const SIDECAR_HOST: &str = "127.0.0.1";
pub const SIDECAR_PORT: u16 = 8765;
pub const SIDECAR_URL: &str = "http://127.0.0.1:8765";

/// File the Tauri shell writes next to the sidecar's data dir. The sidecar
/// reads this on startup to know which provider is active without having
/// to call back into Tauri. The file **never** contains API keys.
pub const ACTIVE_PROVIDER_FILENAME: &str = "active_provider.json";

/// Optional child handle — kept around so we can kill it on shutdown or
/// restart it on provider change.
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

/// Compute `~/.prism/...` once. Falls back to the current working dir if
/// the home dir is not available — better than panicking.
fn prism_home_dir() -> PathBuf {
    dirs::home_dir()
        .map(|h| h.join(".prism"))
        .unwrap_or_else(|| PathBuf::from(".prism"))
}

/// Write the active-provider marker file. Best-effort — a failure here is
/// logged but not fatal, because the sidecar can still infer the active
/// provider from `PRISM_ACTIVE_PROVIDER` env var.
fn write_active_provider_marker(provider: &str, model: Option<&str>) {
    let dir = prism_home_dir();
    if let Err(e) = std::fs::create_dir_all(&dir) {
        eprintln!(
            "[prism] could not create {}: {e} (sidecar will rely on env only)",
            dir.display()
        );
        return;
    }
    let path = dir.join(ACTIVE_PROVIDER_FILENAME);
    let mut payload = serde_json::json!({ "provider": provider });
    if let Some(m) = model {
        payload["model"] = serde_json::Value::String(m.to_string());
    }
    match serde_json::to_string_pretty(&payload)
        .map_err(|e| e.to_string())
        .and_then(|s| std::fs::write(&path, s).map_err(|e| e.to_string()))
    {
        Ok(()) => eprintln!("[prism] wrote active provider marker: {} (provider={})", path.display(), provider),
        Err(e) => eprintln!("[prism] failed to write active provider marker {path:?}: {e}"),
    }
}

/// Build the `Command` for spawning the sidecar, with the correct working
/// directory + env injection for the given provider. The caller still owns
/// spawn/kill/wait.
fn build_command<R: Runtime>(
    app: &AppHandle<R>,
    provider: &str,
) -> Result<Command, String> {
    // Resolve python/ path relative to the Tauri working dir.
    // In dev (cargo tauri dev) cwd is src-tauri/, so python/ is at ../python.
    let python_dir = std::env::current_dir()
        .map(|p| p.join("..").join("python"))
        .unwrap_or_else(|_| std::path::PathBuf::from("../python"));

    let mut cmd = Command::new("uv");
    cmd.args([
        "run",
        "--directory",
        python_dir.to_str().unwrap_or("../python"),
        "prism-sidecar",
    ]);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    cmd.stdin(Stdio::null());

    // Always tell the sidecar which provider is active.
    cmd.env(secrets::ENV_PRISM_ACTIVE_PROVIDER, provider);

    // Per-provider env injection.
    match provider {
        "deepseek" => {
            if let Some(key) = secrets::read_llm_key(app, "deepseek") {
                cmd.env(secrets::ENV_DEEPSEEK_API_KEY, key);
                eprintln!("[prism] injected {}", secrets::ENV_DEEPSEEK_API_KEY);
            } else {
                eprintln!(
                    "[prism] provider=deepseek but no key in keychain — distiller will be disabled"
                );
            }
        }
        "openai" => {
            if let Some(key) = secrets::read_llm_key(app, "openai") {
                cmd.env(secrets::ENV_OPENAI_API_KEY, key);
                eprintln!("[prism] injected {}", secrets::ENV_OPENAI_API_KEY);
            } else {
                eprintln!("[prism] provider=openai but no key in keychain");
            }
        }
        "anthropic" => {
            if let Some(key) = secrets::read_llm_key(app, "anthropic") {
                cmd.env(secrets::ENV_ANTHROPIC_API_KEY, key);
                eprintln!("[prism] injected {}", secrets::ENV_ANTHROPIC_API_KEY);
            } else {
                eprintln!("[prism] provider=anthropic but no key in keychain");
            }
        }
        "ollama" => {
            // No key — only a base URL. Default to localhost.
            let base_url = secrets::DEFAULT_OLLAMA_BASE_URL.to_string();
            cmd.env(secrets::ENV_OLLAMA_API_BASE, &base_url);
            eprintln!(
                "[prism] injected {}={}",
                secrets::ENV_OLLAMA_API_BASE,
                base_url
            );
        }
        "custom" => {
            if let Some(key) = secrets::read_llm_key(app, "custom") {
                cmd.env(secrets::ENV_OPENAI_API_KEY, key);
            } else {
                eprintln!("[prism] provider=custom but no key in keychain");
            }
            if let Some(cfg) = secrets::read_custom_config(app) {
                cmd.env(secrets::ENV_OPENAI_API_BASE, &cfg.base_url);
                eprintln!(
                    "[prism] injected {}={}",
                    secrets::ENV_OPENAI_API_BASE,
                    cfg.base_url
                );
                // The model goes into the active-provider marker so the sidecar
                // can pick it up. We don't have a dedicated PRISM_CUSTOM_MODEL
                // env var yet — keeping the contract minimal.
            } else {
                eprintln!("[prism] provider=custom but no base_url/model in keychain");
            }
        }
        other => {
            eprintln!(
                "[prism] unknown provider id {other:?} — spawning sidecar with PRISM_ACTIVE_PROVIDER only"
            );
        }
    }

    // On Windows, hide the console window of the child process.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    Ok(cmd)
}

/// Resolve the active provider id, falling back to a default. Logs a warning
/// when falling back so a fresh install isn't silent.
fn resolve_provider<R: Runtime>(app: &AppHandle<R>) -> String {
    match secrets::read_active_provider(app) {
        Some(p) if !p.is_empty() => p,
        _ => {
            eprintln!(
                "[prism] no active LLM provider in keychain — falling back to \"deepseek\""
            );
            "deepseek".to_string()
        }
    }
}

/// Spawn the sidecar child. Idempotent: if a child is already running, kill
/// it first so the new env takes effect. The state is stored in Tauri's
/// app-state map under `SidecarState`; the caller is expected to have
/// `manage()`d an empty `SidecarState` once at startup.
pub fn spawn<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let provider = resolve_provider(app);

    // Build the command (may fail if keychain lookups go sideways — but those
    // are best-effort and shouldn't block startup).
    let mut cmd = build_command(app, &provider).map_err(|e| {
        tauri::Error::Io(std::io::Error::new(std::io::ErrorKind::Other, e))
    })?;

    // Persist a marker file for the sidecar to read on its own startup.
    let model_hint = if provider == "custom" {
        secrets::read_custom_config(app).map(|c| c.model)
    } else {
        None
    };
    write_active_provider_marker(&provider, model_hint.as_deref());

    // Replace any existing child.
    kill_existing_child(app, "pre-spawn");

    let mut child = cmd.spawn().map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::Other,
            format!("could not spawn `uv run prism-sidecar`: {e}"),
        )
    })?;

    eprintln!("[prism] sidecar spawned (provider={provider})");

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

    // Stash the child handle in the existing SidecarState.
    let state = app
        .state::<SidecarState>();
    let mut guard = state
        .0
        .lock()
        .map_err(|e| tauri::Error::Io(std::io::Error::new(std::io::ErrorKind::Other, format!("sidecar mutex poisoned: {e}"))))?;
    *guard = Some(child);

    Ok(())
}

/// Helper: take the existing child out of the state, kill + wait for it.
/// Used by both `restart()` and `spawn()` (when re-spawning in place).
fn kill_existing_child<R: Runtime>(app: &AppHandle<R>, context: &str) {
    let state = match app.try_state::<SidecarState>() {
        Some(s) => s,
        None => return, // state never managed — nothing to do
    };
    let mut guard = match state.0.lock() {
        Ok(g) => g,
        Err(e) => {
            eprintln!("[prism] sidecar mutex poisoned during {context}: {e}");
            return;
        }
    };
    if let Some(mut child) = guard.take() {
        match child.kill() {
            Ok(()) => eprintln!("[prism] sidecar killed ({context})"),
            Err(e) => eprintln!("[prism] sidecar kill failed ({context}): {e}"),
        }
        // Reap so we don't leak a zombie.
        let _ = child.wait();
    }
}

/// Restart the sidecar: kill the current child and re-spawn with the
/// current keychain state. Used after `set_llm_config` to pick up new
/// env vars. Blocks for the duration of the kill+wait+respawn (~2 s).
pub fn restart<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    eprintln!("[prism] restarting sidecar (provider change)");
    kill_existing_child(app, "restart");
    spawn(app)
}

/// Best-effort shutdown for the sidecar child process.
///
/// v0.2a: we just `kill()` — the Python process exits and APScheduler's loop
/// is torn down by the OS. v0.2b will send a SIGTERM first (or hit a
/// `/shutdown` HTTP endpoint) and give the scheduler a moment to drain.
pub fn shutdown<R: Runtime>(app: &AppHandle<R>) {
    kill_existing_child(app, "shutdown");
}

// ---------------------------------------------------------------------------
// Tauri command: set_llm_config
//
// Writes the relevant keychain slots for the chosen provider, then kicks
// off a background restart so the sidecar picks up the new env vars. The
// restart is async-fire-and-forget to keep the Tauri UI thread responsive;
// the user sees a "restarting…" toast on the frontend while the respawn
// happens.
// ---------------------------------------------------------------------------

/// Apply the keychain writes for a new LLM config (without touching the
/// sidecar). Used by `set_llm_config` and (in tests) the keychain smoke
/// suite.
pub fn apply_llm_config<R: Runtime>(
    app: &tauri::AppHandle<R>,
    config: &LlmConfigInput,
) -> Result<(), String> {
    if !secrets::is_known_provider(&config.provider) {
        return Err(format!("unknown provider: {}", config.provider));
    }

    // 1. API key (if provided and provider needs one).
    if let Some(key) = config.api_key.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        secrets::write_llm_key(app, &config.provider, key)?;
    }

    // 2. Active provider pointer.
    secrets::write_active_provider(app, &config.provider)?;

    // 3. Custom-mode base_url + model.
    if config.provider == "custom" {
        let base_url = config
            .base_url
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| "custom provider requires base_url".to_string())?
            .to_string();
        let model = config
            .model
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| "custom provider requires model".to_string())?
            .to_string();
        let cfg = secrets::CustomLlmConfig { base_url, model };
        secrets::write_custom_config(app, &cfg)?;
    }

    Ok(())
}

#[tauri::command]
pub fn set_llm_config(
    app: tauri::AppHandle,
    config: LlmConfigInput,
) -> Result<LlmConfigResponse, String> {
    // Synchronous keychain writes — fast, blocks the command briefly.
    apply_llm_config(&app, &config)?;

    // Kick off a background restart so we don't block the UI thread for
    // ~2 s waiting for the sidecar to die and respawn. The frontend polls
    // health() and will see the new provider come online.
    let app_for_restart = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        if let Err(e) = restart(&app_for_restart) {
            eprintln!("[prism] background sidecar restart failed: {e}");
        }
    });

    // Return the post-write state immediately. The actual sidecar
    // restart is in flight; health() will report the new provider once
    // it comes back up.
    Ok(secrets::build_llm_config_response(&app))
}
