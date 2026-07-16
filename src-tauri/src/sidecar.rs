/**
 * Python sidecar management.
 *
 * Lifecycle:
 *   - On Tauri startup, spawn the sidecar. In a packaged app this is the
 *     frozen, self-contained binary bundled beside the app (Tauri
 *     `externalBin`); in a dev tree it falls back to `uv run prism-sidecar`
 *     from python/. See `resolve_bundled_sidecar`.
 *   - Read the active LLM provider from the OS keychain.
 *   - Inject the appropriate API key (and base URL for MiniMax) into
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
 * v0.2a+ (post provider pruning): env injection is provider-aware across
 * two providers — `DEEPSEEK_API_KEY` for deepseek, `MINIMAX_API_KEY` +
 * `MINIMAX_API_BASE` for MiniMax (the OpenAI-compatible endpoint at
 * api.minimaxi.com). The sidecar reads whichever env vars it needs to
 * know the active provider.
 */

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::thread;

use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime};

use crate::secrets;
use crate::secrets::{LlmConfigInput, LlmConfigResponse};

pub const SIDECAR_HOST: &str = "127.0.0.1";
pub const SIDECAR_PORT: u16 = 8765;
pub const SIDECAR_URL: &str = "http://127.0.0.1:8765";

/// Env var carrying the loopback API token to the sidecar (see
/// `api_token`). The Python side's auth middleware reads it at request
/// time; without it (dev `uv run`, pytest) the check is off.
pub const ENV_PRISM_API_TOKEN: &str = "PRISM_API_TOKEN";

/// Per-app-run random token gating the sidecar's HTTP API.
///
/// CORS only protects against browsers — before this token, ANY local
/// process could hit 127.0.0.1:8765 and create/delete sources or
/// register a data-exfiltrating webhook. The token is generated once
/// per Tauri process, injected into every sidecar spawn's env, and
/// handed to the webview via `get_sidecar_url` so the frontend can
/// send it as the `X-Prism-Token` header (or `?token=` for SSE).
/// Restarting the sidecar reuses the same token, so the frontend never
/// needs to refresh it mid-session.
static API_TOKEN: OnceLock<String> = OnceLock::new();

pub fn api_token() -> &'static str {
    API_TOKEN.get_or_init(|| {
        use base64::Engine as _;
        use rand::RngCore;
        let mut bytes = [0u8; 32];
        rand::thread_rng().fill_bytes(&mut bytes);
        // URL_SAFE_NO_PAD keeps it header- and query-string-safe.
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
    })
}

/// File the Tauri shell writes next to the sidecar's data dir. The sidecar
/// reads this on startup to know which provider is active without having
/// to call back into Tauri. The file **never** contains API keys.
pub const ACTIVE_PROVIDER_FILENAME: &str = "active_provider.json";

/// Optional child handle — kept around so we can kill it on shutdown or
/// restart it on provider change.
pub struct SidecarState(pub Mutex<Option<std::process::Child>>);

/// Serialises the whole kill→spawn→store sequence. `set_llm_config`
/// fires restarts as background tasks; without this lock two rapid
/// "save" clicks could interleave (A kills, B kills, A spawns+stores,
/// B spawns and OVERWRITES the stored child) leaking a process that
/// stays bound to port 8765. The per-field `SidecarState` mutex only
/// protects the handle slot, not the multi-step sequence.
static RESTART_LOCK: Mutex<()> = Mutex::new(());

#[derive(Serialize)]
pub struct SidecarInfo {
    pub url: String,
    pub port: u16,
    pub host: String,
    /// Loopback API token — the frontend sends this on every sidecar
    /// request (`X-Prism-Token`). See `api_token` for the threat model.
    pub token: String,
}

#[tauri::command]
pub fn get_sidecar_url() -> SidecarInfo {
    SidecarInfo {
        url: SIDECAR_URL.to_string(),
        port: SIDECAR_PORT,
        host: SIDECAR_HOST.to_string(),
        token: api_token().to_string(),
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
    let tmp_path = dir.join(format!("{ACTIVE_PROVIDER_FILENAME}.tmp"));
    let mut payload = serde_json::json!({ "provider": provider });
    if let Some(m) = model {
        payload["model"] = serde_json::Value::String(m.to_string());
    }
    // tmp + rename so the sidecar (which reads this file on its own
    // startup, possibly concurrently) never sees a half-written JSON.
    match serde_json::to_string_pretty(&payload)
        .map_err(|e| e.to_string())
        .and_then(|s| std::fs::write(&tmp_path, s).map_err(|e| e.to_string()))
        .and_then(|()| std::fs::rename(&tmp_path, &path).map_err(|e| e.to_string()))
    {
        Ok(()) => eprintln!("[prism] wrote active provider marker: {} (provider={})", path.display(), provider),
        Err(e) => eprintln!("[prism] failed to write active provider marker {path:?}: {e}"),
    }
}

/// Build the `Command` for spawning the sidecar, with the correct working
/// directory + env injection for the given provider. The caller still owns
/// spawn/kill/wait.
/// Locate the frozen, self-contained sidecar binary bundled next to the app
/// (Tauri's `externalBin` places it beside the main executable, named plainly
/// `prism-sidecar` with the target-triple stripped). Returns None in a dev
/// tree where no frozen binary exists, so the caller falls back to `uv run`.
///
/// `PRISM_SIDECAR_BIN` overrides the lookup — handy for exercising the
/// production spawn path from a dev build.
fn resolve_bundled_sidecar() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("PRISM_SIDECAR_BIN") {
        let p = PathBuf::from(explicit);
        if p.exists() {
            return Some(p);
        }
    }
    let name = if cfg!(windows) { "prism-sidecar.exe" } else { "prism-sidecar" };
    let bin = std::env::current_exe().ok()?.parent()?.join(name);
    bin.exists().then_some(bin)
}

fn build_command<R: Runtime>(
    app: &AppHandle<R>,
    provider: &str,
) -> Result<Command, String> {
    // Prod: spawn the frozen binary bundled beside the app (no uv/Python
    // needed). Dev: fall back to `uv run prism-sidecar` from ../python
    // (in `cargo tauri dev` the cwd is src-tauri/, so python/ is at ../python).
    let mut cmd = if let Some(bin) = resolve_bundled_sidecar() {
        eprintln!("[prism] spawning bundled sidecar: {}", bin.display());
        Command::new(bin)
    } else {
        let python_dir = std::env::current_dir()
            .map(|p| p.join("..").join("python"))
            .unwrap_or_else(|_| std::path::PathBuf::from("../python"));
        eprintln!("[prism] no bundled sidecar found — falling back to `uv run` (dev)");
        let mut c = Command::new("uv");
        c.args([
            "run",
            "--directory",
            python_dir.to_str().unwrap_or("../python"),
            "prism-sidecar",
        ]);
        c
    };
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    cmd.stdin(Stdio::null());

    // Always tell the sidecar which provider is active.
    cmd.env(secrets::ENV_PRISM_ACTIVE_PROVIDER, provider);
    // Loopback auth: the sidecar rejects requests without this token
    // (any local process could otherwise drive the API). Same token for
    // every respawn within this app run — see `api_token`.
    cmd.env(ENV_PRISM_API_TOKEN, api_token());

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
        "minimax" => {
            if let Some(key) = secrets::read_llm_key(app, "minimax") {
                cmd.env(secrets::ENV_MINIMAX_API_KEY, key);
                eprintln!("[prism] injected {}", secrets::ENV_MINIMAX_API_KEY);
            } else {
                eprintln!("[prism] provider=minimax but no key in keychain");
            }
            // MiniMax uses the OpenAI-compatible protocol — always set
            // the base URL to the canonical endpoint unless the legacy
            // override blob says otherwise.
            let base_url = secrets::read_custom_config(app)
                .map(|c| c.base_url)
                .unwrap_or_else(|| secrets::DEFAULT_MINIMAX_API_BASE.to_string());
            cmd.env(secrets::ENV_MINIMAX_API_BASE, &base_url);
            eprintln!(
                "[prism] injected {}={}",
                secrets::ENV_MINIMAX_API_BASE,
                base_url
            );
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
///
/// Public entry point — takes `RESTART_LOCK` so concurrent spawn /
/// restart / shutdown calls can't interleave their kill→spawn→store
/// sequences.
pub fn spawn<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let _g = RESTART_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    spawn_inner(app)
}

/// The actual spawn sequence. Caller must hold `RESTART_LOCK`.
fn spawn_inner<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let provider = resolve_provider(app);

    // Build the command (may fail if keychain lookups go sideways — but those
    // are best-effort and shouldn't block startup).
    let mut cmd = build_command(app, &provider).map_err(|e| {
        tauri::Error::Io(std::io::Error::new(std::io::ErrorKind::Other, e))
    })?;

    // Persist a marker file for the sidecar to read on its own startup.
    // For MiniMax we stash the (possibly user-overridden) model into the
    // active-provider marker so the sidecar can pick it up without a
    // dedicated env var. DeepSeek uses the canonical model hard-coded in
    // the sidecar's `default_model` so no hint is needed.
    let model_hint = if provider == "minimax" {
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

/// Best-effort kill of a process **tree** rooted at `pid`.
///
/// Why this exists: the child we spawn and hold in `SidecarState` is
/// `uv`, not the Python interpreter — `build_command` runs
/// `uv run --directory ../python prism-sidecar`. `uv` forks the actual
/// `prism-sidecar` (uvicorn) process as its own child. Plain
/// `Child::kill()` (SIGKILL to `uv`'s pid / `TerminateProcess` on
/// Windows) only guarantees `uv` itself dies — whether the grandchild
/// Python process goes down with it depends on `uv` forwarding the
/// signal, which we shouldn't rely on. Left alone, that Python process
/// keeps the loopback port (8765) bound, so the next `spawn()` (e.g.
/// after a provider change) can fail to bind or — worse — the old and
/// new sidecar both answer requests.
///
/// We kill the tree first (so the grandchild dies even if `uv` would
/// have ignored the signal), then still call `Child::kill()` on the
/// direct child as the existing, already-tested fallback. Both steps
/// are best-effort: a failure here is logged, never fatal — worst case
/// is the pre-existing (already-documented) orphan-process behaviour,
/// not a regression.
fn kill_process_tree(pid: u32, context: &str) {
    #[cfg(unix)]
    {
        // `pkill -P <pid>` signals *children* of pid (i.e. the Python
        // process `uv run` forked), not pid itself — `Child::kill()`
        // below still handles `uv`. `-TERM` first gives uvicorn a
        // chance to close its listening socket cleanly; callers that
        // need a hard stop still get the SIGKILL from `Child::kill()`.
        match std::process::Command::new("pkill")
            .args(["-TERM", "-P", &pid.to_string()])
            .status()
        {
            Ok(status) if status.success() => {
                eprintln!("[prism] killed sidecar child processes of pid={pid} ({context})");
            }
            // Exit code 1 just means "no matching processes" — not an
            // error, just means there was nothing to clean up.
            Ok(_) => {}
            Err(e) => {
                eprintln!(
                    "[prism] pkill -P {pid} failed ({context}): {e} \
                     (pkill may not be installed; falling back to killing {pid} only)"
                );
            }
        }
        // ALSO SIGTERM the direct child itself. `pkill -P` signals only
        // pid's *children* — which covers `uv run` and the PyInstaller
        // onefile bootloader (the real server is their child), but when
        // the spawned process IS the server (onedir build, or
        // PRISM_SIDECAR_BIN pointing straight at a uvicorn binary) the
        // graceful path never fired: the server saw no TERM, the caller
        // burned the full 5s grace, then SIGKILL'd it — so the Python
        // side's drain-and-close-db shutdown never ran. A duplicate
        // TERM in the covered cases is harmless (uv forwards it; the
        // bootloader exits with its child).
        match std::process::Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .status()
        {
            Ok(status) if status.success() => {
                eprintln!("[prism] sent SIGTERM to sidecar pid={pid} ({context})");
            }
            Ok(_) => {}
            Err(e) => {
                eprintln!("[prism] kill -TERM {pid} failed ({context}): {e}");
            }
        }
    }
    #[cfg(windows)]
    {
        // `/T` kills the whole process tree rooted at pid, `/F` forces
        // it. This is the Windows equivalent of the pkill call above —
        // TerminateProcess (what `Child::kill()` uses) does not cascade
        // to descendants on its own.
        match std::process::Command::new("taskkill")
            .args(["/T", "/F", "/PID", &pid.to_string()])
            .status()
        {
            Ok(status) if status.success() => {
                eprintln!("[prism] killed sidecar process tree pid={pid} ({context})");
            }
            Ok(_) => {}
            Err(e) => {
                eprintln!("[prism] taskkill /PID {pid} failed ({context}): {e}");
            }
        }
    }
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
        // Step 1: SIGTERM the tree (the real Python process `uv`
        // forked) — see `kill_process_tree` doc comment. uvicorn turns
        // SIGTERM into a graceful shutdown: the sidecar's lifespan
        // hook drains in-flight sync jobs at their per-source
        // checkpoint (python `orchestrator.drain_inflight`, grace 4s)
        // and closes the db cleanly.
        kill_process_tree(child.id(), context);

        // Step 2 (v0.2c): give the graceful path a bounded window
        // before the hard kill. 5s deliberately outlives the Python
        // side's 4s drain grace; a sidecar with nothing in flight
        // exits well under a second, so the common case stays snappy —
        // we poll `try_wait` instead of sleeping the whole window.
        const GRACE_MS: u64 = 5_000;
        const POLL_MS: u64 = 100;
        let mut waited = 0u64;
        loop {
            match child.try_wait() {
                Ok(Some(status)) => {
                    eprintln!(
                        "[prism] sidecar exited gracefully ({context}, {status}, {waited}ms)"
                    );
                    return;
                }
                Ok(None) => {}
                Err(e) => {
                    eprintln!("[prism] sidecar try_wait failed ({context}): {e}");
                    break;
                }
            }
            if waited >= GRACE_MS {
                eprintln!(
                    "[prism] sidecar still running after {GRACE_MS}ms grace ({context}); hard-killing"
                );
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(POLL_MS));
            waited += POLL_MS;
        }

        // Step 3: hard fallback — same behaviour as before v0.2c.
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
    let _g = RESTART_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    kill_existing_child(app, "restart");
    spawn_inner(app)
}

/// Tauri command: manually restart the sidecar.
///
/// Exposed to the Settings page so the user can pick up a newly-saved API
/// key (or a manually-edited `~/.prism/keystore.json`) without quitting
/// and relaunching the whole app. Like `set_llm_config`, the actual
/// kill+respawn runs on a background thread so the UI stays responsive;
/// the frontend polls `health()` and watches the version/uptime reset to
/// know the new process is live.
#[tauri::command]
pub fn restart_sidecar(app: tauri::AppHandle) {
    let app_for_restart = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        if let Err(e) = restart(&app_for_restart) {
            eprintln!("[prism] manual sidecar restart failed: {e}");
        }
    });
}

/// Best-effort shutdown for the sidecar child process.
///
/// v0.2c: SIGTERM first, then up to 5s grace for the Python side to
/// drain in-flight sync jobs (per-source checkpoint) and close the db,
/// then hard kill as the fallback — see `kill_existing_child`.
pub fn shutdown<R: Runtime>(app: &AppHandle<R>) {
    let _g = RESTART_LOCK.lock().unwrap_or_else(|e| e.into_inner());
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

    // 1. API key. Three cases, keyed off how the frontend builds the
    //    payload (SettingsPage saveMut):
    //      * field absent (None)     → leave the stored key untouched
    //      * empty / whitespace-only → explicit "clear key" — delete the slot
    //      * non-empty               → write the new key
    //    The empty-string case used to fall into the same filter as
    //    "absent", which silently turned the Settings page's "清除 Key"
    //    button into a no-op (the slot was never deleted).
    if let Some(raw) = config.api_key.as_deref() {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            secrets::delete_llm_key(app, &config.provider)?;
        } else {
            secrets::write_llm_key(app, &config.provider, trimmed)?;
        }
    }

    // 2. Active provider pointer.
    secrets::write_active_provider(app, &config.provider)?;

    // 3. MiniMax override blob (base_url + model). Only persisted when
    // the user supplies at least one of them; both are optional because
    // the sidecar already knows sensible defaults.
    if config.provider == "minimax" {
        let base_url = config
            .base_url
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string());
        let model = config
            .model
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string());
        if base_url.is_some() || model.is_some() {
            let cfg = secrets::CustomLlmConfig {
                base_url: base_url.unwrap_or_else(|| secrets::DEFAULT_MINIMAX_API_BASE.to_string()),
                model: model.unwrap_or_else(|| "MiniMax-M3".to_string()),
            };
            secrets::write_custom_config(app, &cfg)?;
        }
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
