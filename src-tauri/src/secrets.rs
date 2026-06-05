/**
 * API-key storage backed by the OS keychain.
 *
 * We use the `tauri-plugin-keyring` plugin (which wraps the `keyring` crate) so
 * we get the same capability-gated API surface as the rest of the Tauri plugin
 * ecosystem. On macOS this lands in Keychain Access, on Windows in the
 * Credential Manager, and on Linux in the Secret Service (libsecret).
 *
 * v0.2a: only one secret — the DeepSeek API key. The constant `SERVICE` and
 * `USERNAME` are intentionally kept in one place so v0.2b+ (when we add
 * OpenAI / Anthropic / RSS tokens) can extend without scattering string
 * literals across the codebase.
 *
 * IMPORTANT: never log the key value, never serialize it into a Tauri event,
 * and never return it to the frontend. The frontend only ever sees
 * `{ configured: bool }` so a compromised renderer cannot exfiltrate the key.
 */

use tauri::Runtime;
use tauri_plugin_keyring::KeyringExt;

/// Service name registered in the OS keychain. Bundled with the bundle
/// identifier (`com.prism.desktop`) for namespace safety.
pub const SERVICE: &str = "com.prism.desktop";

/// Username / account label for the DeepSeek key. v0.2b will add sibling
/// constants like `USERNAME_OPENAI`, `USERNAME_ANTHROPIC`.
pub const USERNAME_DEEPSEEK: &str = "deepseek-api-key";

/// Env-var name the Python sidecar reads at startup.
pub const ENV_DEEPSEEK_API_KEY: &str = "DEEPSEEK_API_KEY";

/// Read the DeepSeek API key from the keychain. Returns `None` if it is
/// missing OR if the keyring backend errored (we don't want a keychain
/// failure to brick sidecar startup — log + carry on).
pub fn read_deepseek_key<R: Runtime>(app: &tauri::AppHandle<R>) -> Option<String> {
    match app.keyring().get_password(SERVICE, USERNAME_DEEPSEEK) {
        // tauri-plugin-keyring returns Ok(None) when the entry is absent, and
        // Ok(Some(key)) when present. The Err branch is reserved for actual
        // backend failures (dbus down, keychain locked, etc.).
        Ok(maybe_key) => {
            if maybe_key.is_none() {
                eprintln!("[prism] no DeepSeek key in keychain");
            }
            maybe_key
        }
        Err(e) => {
            eprintln!("[prism] keychain read error: {e}");
            None
        }
    }
}

/// Write the DeepSeek API key to the keychain. The caller is expected to
/// trim and basic-validate the key before calling.
pub fn write_deepseek_key<R: Runtime>(
    app: &tauri::AppHandle<R>,
    key: &str,
) -> Result<(), String> {
    app.keyring()
        .set_password(SERVICE, USERNAME_DEEPSEEK, key)
        .map_err(|e| format!("keychain set failed: {e}"))
}

/// Delete the DeepSeek API key from the keychain. Idempotent — deleting a
/// missing entry is not an error.
pub fn delete_deepseek_key<R: Runtime>(app: &tauri::AppHandle<R>) -> Result<(), String> {
    match app
        .keyring()
        .delete_password(SERVICE, USERNAME_DEEPSEEK)
    {
        Ok(()) => Ok(()),
        Err(e) => {
            let msg = e.to_string();
            if msg.to_lowercase().contains("no entry") || msg.contains("NoEntry") {
                // Already gone — that's fine.
                Ok(())
            } else {
                Err(format!("keychain delete failed: {msg}"))
            }
        }
    }
}

// ----- Tauri commands (frontend invokes these via `invoke()`) -----
//
// We deliberately do NOT return the key value to the webview — only a
// `configured: bool` flag. The frontend's job is to display "API key set"
// or "no API key", and to accept the user's paste. The actual key never
// crosses the JS↔Rust bridge in the read direction, so a compromised
// renderer (XSS in a future feed item, say) can't exfiltrate it.

/// Returns `{ "configured": <bool> }`. No key value is ever returned.
#[tauri::command]
pub fn get_api_key_status(app: tauri::AppHandle) -> serde_json::Value {
    serde_json::json!({ "configured": read_deepseek_key(&app).is_some() })
}

/// Stores the provided key in the OS keychain. Empty / whitespace-only
/// keys are rejected early so we don't litter the keychain with junk.
#[tauri::command]
pub fn set_api_key(app: tauri::AppHandle, key: String) -> Result<serde_json::Value, String> {
    let trimmed = key.trim();
    if trimmed.is_empty() {
        return Err("API key cannot be empty".to_string());
    }
    write_deepseek_key(&app, trimmed)?;
    Ok(serde_json::json!({ "ok": true }))
}

/// Removes the key from the keychain. Idempotent.
#[tauri::command]
pub fn clear_api_key(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    delete_deepseek_key(&app)?;
    Ok(serde_json::json!({ "ok": true }))
}
