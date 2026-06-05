mod sidecar;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
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
        .invoke_handler(tauri::generate_handler![sidecar::get_sidecar_url])
        .run(tauri::generate_context!())
        .expect("error while running Prism");
}
