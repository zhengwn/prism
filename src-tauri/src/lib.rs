// The `secrets` and `sidecar` modules are exposed for integration tests
// under `tests/` (the smoke tests roundtrip the multi-slot keychain
// layout and check the provider schema).
pub mod secrets;
mod sidecar;

use tauri::Manager as _;
use sidecar::SidecarState;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // OS-keychain plugin — exposes `app.keyring()` to Rust code and (if
        // we ever want it) the `tauri-plugin-keyring-api` JS package to the
        // webview. For v0.2a we call the Rust API directly from our own
        // `secrets` module; the webview only sees our custom commands and
        // never touches the keychain directly.
        .plugin(tauri_plugin_keyring::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
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
            // Legacy v0.2a single-provider API (back-compat)
            secrets::get_api_key_status,
            secrets::set_api_key,
            secrets::clear_api_key,
            // v0.2a-providers multi-provider config
            secrets::get_provider_schema,
            secrets::get_llm_config,
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
