// The `keystore` / `secrets` / `sidecar` modules are exposed for
// integration tests under `tests/` (the smoke tests roundtrip the
// encrypted-file store and check the provider schema).
pub mod keystore;
pub mod secrets;
mod sidecar;

use tauri::Manager as _;
use sidecar::SidecarState;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // One-shot migration from the v0.2a OS-keychain layout to the
            // new local encrypted-file keystore. Idempotent — once the
            // keystore file exists, this is a no-op. The first call on
            // a v0.2a install triggers one macOS prompt; after that the
            // keychain is never touched again.
            keystore::migrate_from_keychain_if_needed(app.handle());

            // Register the sidecar child-handle state up front so `spawn()`
            // and `restart()` can both update the same slot without
            // double-`manage()` panicking.
            app.manage(SidecarState(std::sync::Mutex::new(None)));

            // Spawn the Python sidecar so the React app can talk to it on
            // http://127.0.0.1:8765. Failure here is non-fatal — the user can
            // start the sidecar manually via `npm run sidecar:dev`.
            match sidecar::spawn(app.handle()) {
                Ok(()) => {}
                Err(e) => {
                    eprintln!("[prism] failed to spawn sidecar: {e}");
                    eprintln!("[prism] run `npm run sidecar:dev` in another terminal to start it manually");
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // Sidecar info / lifecycle
            sidecar::get_sidecar_url,
            sidecar::restart_sidecar,
            // Legacy v0.2a single-provider API (back-compat)
            secrets::get_api_key_status,
            secrets::set_api_key,
            secrets::clear_api_key,
            // v0.2a-providers multi-provider config
            secrets::get_provider_schema,
            secrets::get_llm_config,
            secrets::reveal_llm_key,
            sidecar::set_llm_config,
        ])
        .build(tauri::generate_context!())
        .expect("error while building Prism")
        .run(|app_handle, event| {
            // Best-effort sidecar shutdown on app exit. We don't try to be
            // clever about ExitRequested vs WindowEvent::CloseRequested —
            // this fires for both, and we just want the child process gone.
            if let tauri::RunEvent::ExitRequested { .. } = event {
                sidecar::shutdown(app_handle);
            }
        });
}
