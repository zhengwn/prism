mod secrets;
mod sidecar;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // OS-keychain plugin — exposes `app.keyring()` to Rust code and (if
        // we ever want it) the `tauri-plugin-keyring-api` JS package to the
        // webview. For v0.2a we call the Rust API directly from our own
        // `secrets` module; the webview only sees our three custom commands
        // (`get_api_key_status` / `set_api_key` / `clear_api_key`) and never
        // touches the keychain directly.
        .plugin(tauri_plugin_keyring::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
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
            sidecar::get_sidecar_url,
            secrets::get_api_key_status,
            secrets::set_api_key,
            secrets::clear_api_key,
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
