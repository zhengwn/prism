/**
 * API-key storage backed by the OS keychain.
 *
 * We use the `tauri-plugin-keyring` plugin (which wraps the `keyring` crate) so
 * we get the same capability-gated API surface as the rest of the Tauri plugin
 * ecosystem. On macOS this lands in Keychain Access, on Windows in the
 * Credential Manager, and on Linux in the Secret Service (libsecret).
 *
 * # v0.2a multi-provider keychain layout
 *
 * | Username                       | Value                              | Purpose                          |
 * |--------------------------------|------------------------------------|----------------------------------|
 * | `llm-provider:active`          | provider id string (`"deepseek"`)  | which provider is active         |
 * | `llm-key:deepseek`             | api key                            | DeepSeek key                     |
 * | `llm-key:openai`               | api key                            | OpenAI key                       |
 * | `llm-key:anthropic`            | api key                            | Anthropic key                    |
 * | `llm-key:custom`               | api key                            | Custom OpenAI-compatible key     |
 * | `llm-config:custom`            | JSON `{base_url, model}`           | Custom mode base_url + model     |
 *
 * All entries live under the same service name (`com.prism.desktop`) so they
 * show up grouped in the OS keychain UI.
 *
 * # v0.2a → v0.2a-providers migration
 *
 * The pre-providers v0.2a build stored a single key under the username
 * `deepseek-api-key`. The first time the new code reads
 * `read_active_provider()` and finds no active provider set, it will:
 *
 * 1. Read `deepseek-api-key` if present
 * 2. Write it to `llm-key:deepseek`
 * 3. Set `llm-provider:active = "deepseek"`
 * 4. Delete the legacy `deepseek-api-key` slot
 *
 * Migration is idempotent: once `llm-provider:active` exists, the old slot is
 * never read again. A user who already set OpenAI in v0.2b+ before this
 * refactor would not be affected.
 *
 * # SECURITY
 *
 * - The key value is **never** returned to the frontend. Only `configured: bool`
 *   crosses the JS↔Rust bridge on the read direction. A compromised renderer
 *   cannot exfiltrate keys.
 * - The legacy `set_api_key` / `get_api_key_status` / `clear_api_key` Tauri
 *   commands remain as thin wrappers around the deepseek slot for backwards
 *   compatibility with the v0.2a Settings page.
 */

use serde::{Deserialize, Serialize};
use tauri::Runtime;
use tauri_plugin_keyring::KeyringExt;

/// Service name registered in the OS keychain. Bundled with the bundle
/// identifier (`com.prism.desktop`) for namespace safety.
pub const SERVICE: &str = "com.prism.desktop";

// ---------------------------------------------------------------------------
// Username constants for the multi-slot keychain layout.
// ---------------------------------------------------------------------------

/// Username that stores the id of the currently active LLM provider
/// (e.g. `"deepseek"`, `"openai"`, `"anthropic"`, `"ollama"`, `"custom"`).
pub const USERNAME_LLM_PROVIDER_ACTIVE: &str = "llm-provider:active";

/// Username that stores the JSON `{"base_url": "...", "model": "..."}` blob
/// for the `custom` provider.
pub const USERNAME_LLM_CONFIG_CUSTOM: &str = "llm-config:custom";

/// Legacy v0.2a username. Read once at first launch, then deleted.
pub const USERNAME_DEEPSEEK_LEGACY: &str = "deepseek-api-key";

/// Build the username used to store the API key for a given provider id.
/// The provider id is interpolated directly into the username; callers
/// must validate the id against the known set before calling.
pub fn llm_key_username(provider: &str) -> String {
    format!("llm-key:{provider}")
}

// ---------------------------------------------------------------------------
// Env-var names the Python sidecar reads at startup. Kept here so the
// Rust env-injection code and any future docs reference the same strings.
// ---------------------------------------------------------------------------

pub const ENV_DEEPSEEK_API_KEY: &str = "DEEPSEEK_API_KEY";
pub const ENV_OPENAI_API_KEY: &str = "OPENAI_API_KEY";
pub const ENV_ANTHROPIC_API_KEY: &str = "ANTHROPIC_API_KEY";
pub const ENV_OLLAMA_API_BASE: &str = "OLLAMA_API_BASE";
pub const ENV_OPENAI_API_BASE: &str = "OPENAI_API_BASE";
pub const ENV_PRISM_ACTIVE_PROVIDER: &str = "PRISM_ACTIVE_PROVIDER";

/// Default Ollama host. Used when no `llm-config:ollama.base_url` is set.
pub const DEFAULT_OLLAMA_BASE_URL: &str = "http://127.0.0.1:11434";

// ---------------------------------------------------------------------------
// Known provider ids.
// ---------------------------------------------------------------------------

/// Canonical list of provider ids. Order matches the Settings UI dropdown.
pub const KNOWN_PROVIDERS: &[&str] = &["deepseek", "openai", "anthropic", "ollama", "custom"];

/// Returns `true` if the given string is one of the known provider ids.
pub fn is_known_provider(p: &str) -> bool {
    KNOWN_PROVIDERS.contains(&p)
}

// ---------------------------------------------------------------------------
// Tauri command payload types — these are the public contracts the frontend
// invokes. They live here (not in `lib.rs`) so the keychain ↔ command
// surface stays in one file.
// ---------------------------------------------------------------------------

/// Response shape for `get_llm_config` and `set_llm_config`. Never carries a
/// key value — only the booleans/strings the Settings UI needs to render.
///
/// `rename_all = "camelCase"` so the JS side can consume `baseUrl` instead of
/// `base_url` — Tauri's IPC layer deserialises the request and re-serialises
/// the response using serde, and the frontend payload convention is camelCase
/// (matches the `LlmConfig` TS type in `src/types/index.ts`).
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmConfigResponse {
    pub provider: String,
    pub configured: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub base_url: Option<String>,
}

/// Input shape for `set_llm_config`. All fields except `provider` are
/// optional — only the ones relevant to the chosen provider need to be sent.
///
/// `rename_all = "camelCase"` so the JS side can send `{ apiKey, baseUrl }`
/// instead of `{ api_key, base_url }`. Without this serde would reject every
/// save call at runtime (no field matches the JS payload).
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmConfigInput {
    pub provider: String,
    #[serde(default)]
    pub api_key: Option<String>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub base_url: Option<String>,
}

/// One entry of the provider schema. Mirrors the JSON returned by
/// `GET /api/settings/providers` on the sidecar, with a hard-coded fallback
/// so the frontend can render the picker even before the sidecar is up.
///
/// `rename_all = "camelCase"` for consistency with the response structs — the
/// `get_provider_schema` Tauri command (when called from JS) returns
/// `requiresKey` / `defaultModel` to match the TS `ProviderSchema` type.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderSchema {
    pub id: String,
    pub label: String,
    pub requires_key: bool,
    pub default_model: String,
    pub fields: Vec<String>,
}

// ---------------------------------------------------------------------------
// Low-level keychain helpers — generic over the Tauri Runtime so the same
// code paths work in production, in tests (via `tauri::test::mock_app()`),
// and in unit-test-style direct callers.
// ---------------------------------------------------------------------------

fn read_slot<R: Runtime>(app: &tauri::AppHandle<R>, username: &str) -> Option<String> {
    match app.keyring().get_password(SERVICE, username) {
        Ok(maybe_value) => maybe_value,
        Err(e) => {
            eprintln!("[prism] keychain read error for {username}: {e}");
            None
        }
    }
}

fn write_slot<R: Runtime>(
    app: &tauri::AppHandle<R>,
    username: &str,
    value: &str,
) -> Result<(), String> {
    app.keyring()
        .set_password(SERVICE, username, value)
        .map_err(|e| format!("keychain set failed for {username}: {e}"))
}

fn delete_slot<R: Runtime>(app: &tauri::AppHandle<R>, username: &str) -> Result<(), String> {
    match app.keyring().delete_password(SERVICE, username) {
        Ok(()) => Ok(()),
        Err(e) => {
            let msg = e.to_string();
            if msg.to_lowercase().contains("no entry") || msg.contains("NoEntry") {
                Ok(())
            } else {
                Err(format!("keychain delete failed for {username}: {msg}"))
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Public helpers for the multi-slot keychain layout.
// ---------------------------------------------------------------------------

/// Read the API key for a given provider. Returns `None` if absent or on
/// keychain backend error. Never returns the legacy `deepseek-api-key` slot —
/// callers wanting the active provider's key should go through
/// `read_active_provider` first.
pub fn read_llm_key<R: Runtime>(app: &tauri::AppHandle<R>, provider: &str) -> Option<String> {
    read_slot(app, &llm_key_username(provider))
}

/// Write the API key for a given provider. The caller is expected to
/// trim and basic-validate the key before calling.
pub fn write_llm_key<R: Runtime>(
    app: &tauri::AppHandle<R>,
    provider: &str,
    key: &str,
) -> Result<(), String> {
    if !is_known_provider(provider) {
        return Err(format!("unknown provider: {provider}"));
    }
    write_slot(app, &llm_key_username(provider), key)
}

/// Delete the API key for a given provider. Idempotent — deleting a
/// missing entry is not an error.
pub fn delete_llm_key<R: Runtime>(
    app: &tauri::AppHandle<R>,
    provider: &str,
) -> Result<(), String> {
    delete_slot(app, &llm_key_username(provider))
}

/// Custom-mode configuration: base_url + model. Serialized to JSON when
/// stored in the keychain.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CustomLlmConfig {
    pub base_url: String,
    pub model: String,
}

/// Read the stored `custom` provider config (base_url + model).
/// Returns `None` if absent, malformed, or on keychain error.
pub fn read_custom_config<R: Runtime>(app: &tauri::AppHandle<R>) -> Option<CustomLlmConfig> {
    let raw = read_slot(app, USERNAME_LLM_CONFIG_CUSTOM)?;
    match serde_json::from_str::<CustomLlmConfig>(&raw) {
        Ok(cfg) => Some(cfg),
        Err(e) => {
            eprintln!("[prism] llm-config:custom is not valid JSON: {e}");
            None
        }
    }
}

/// Write the `custom` provider config (base_url + model). The blob is
/// stored as JSON in the keychain.
pub fn write_custom_config<R: Runtime>(
    app: &tauri::AppHandle<R>,
    cfg: &CustomLlmConfig,
) -> Result<(), String> {
    let json = serde_json::to_string(cfg).map_err(|e| format!("serialize CustomLlmConfig: {e}"))?;
    write_slot(app, USERNAME_LLM_CONFIG_CUSTOM, &json)
}

/// Read the active provider id, performing the lazy v0.2a → v0.2a-providers
/// migration on first call.
///
/// Migration rules (idempotent):
/// 1. If `llm-provider:active` is set → return it directly.
/// 2. Else if the legacy `deepseek-api-key` slot is present → migrate it to
///    `llm-key:deepseek` + `llm-provider:active = "deepseek"`, delete the
///    legacy slot, return `Some("deepseek")`.
/// 3. Else → return `None`. Callers (e.g. `sidecar::spawn`) should fall back
///    to a sensible default such as `"deepseek"`.
pub fn read_active_provider<R: Runtime>(app: &tauri::AppHandle<R>) -> Option<String> {
    if let Some(active) = read_slot(app, USERNAME_LLM_PROVIDER_ACTIVE) {
        if !active.is_empty() {
            return Some(active);
        }
    }

    // No active provider — check for the legacy v0.2a slot.
    if let Some(legacy_key) = read_slot(app, USERNAME_DEEPSEEK_LEGACY) {
        eprintln!("[prism] migrating v0.2a API key to multi-provider slot");
        if let Err(e) = write_slot(app, &llm_key_username("deepseek"), &legacy_key) {
            eprintln!("[prism] migration write to llm-key:deepseek failed: {e}");
            return None;
        }
        if let Err(e) = write_slot(app, USERNAME_LLM_PROVIDER_ACTIVE, "deepseek") {
            eprintln!("[prism] migration write to llm-provider:active failed: {e}");
            return None;
        }
        if let Err(e) = delete_slot(app, USERNAME_DEEPSEEK_LEGACY) {
            // Non-fatal — the legacy entry is now duplicated at worst.
            eprintln!("[prism] migration delete of {USERNAME_DEEPSEEK_LEGACY} failed: {e}");
        }
        return Some("deepseek".to_string());
    }

    None
}

/// Write the active provider id. Empty strings are rejected.
pub fn write_active_provider<R: Runtime>(
    app: &tauri::AppHandle<R>,
    provider: &str,
) -> Result<(), String> {
    if provider.is_empty() {
        return Err("provider cannot be empty".to_string());
    }
    write_slot(app, USERNAME_LLM_PROVIDER_ACTIVE, provider)
}

// ---------------------------------------------------------------------------
// v0.2a backward-compat thin wrappers. The frontend still invokes these from
// the v0.2a Settings page. They route through the new multi-slot helpers so
// the keychain layout is consistent regardless of which API the UI uses.
// ---------------------------------------------------------------------------

/// Read the DeepSeek API key. Equivalent to `read_llm_key(app, "deepseek")`.
/// Returns `None` if absent or on keychain backend error.
pub fn read_deepseek_key<R: Runtime>(app: &tauri::AppHandle<R>) -> Option<String> {
    read_llm_key(app, "deepseek")
}

/// Write the DeepSeek API key. Equivalent to `write_llm_key(app, "deepseek", key)`.
pub fn write_deepseek_key<R: Runtime>(
    app: &tauri::AppHandle<R>,
    key: &str,
) -> Result<(), String> {
    write_llm_key(app, "deepseek", key)
}

/// Delete the DeepSeek API key. Idempotent.
pub fn delete_deepseek_key<R: Runtime>(app: &tauri::AppHandle<R>) -> Result<(), String> {
    delete_llm_key(app, "deepseek")
}

// ---------------------------------------------------------------------------
// Tauri commands (frontend invokes these via `invoke()`).
//
// SECURITY: We deliberately do NOT return the key value to the webview — only
// a `configured: bool` flag. The frontend's job is to display "API key set"
// or "no API key", and to accept the user's paste. The actual key never
// crosses the JS↔Rust bridge in the read direction, so a compromised
// renderer (XSS in a future feed item, say) can't exfiltrate it.
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// New v0.2a-providers Tauri commands.
//
// `get_llm_config` and `get_provider_schema` are read-only and live here.
// `set_llm_config` lives in `sidecar.rs` because it has to restart the
// sidecar after writing the keychain.
// ---------------------------------------------------------------------------

/// Default models for the preset providers. The `custom` provider's model
/// is user-supplied and lives in the keychain as `llm-config:custom`.
pub fn default_model_for(provider: &str) -> Option<&'static str> {
    match provider {
        "deepseek" => Some("deepseek-chat"),
        "openai" => Some("gpt-4o-mini"),
        "anthropic" => Some("claude-3-5-sonnet-20241022"),
        "ollama" => Some("qwen2.5:7b"),
        _ => None,
    }
}

/// Build the `LlmConfigResponse` for the current keychain state. Used by
/// both `get_llm_config` and `set_llm_config` (which returns the
/// post-write state).
pub fn build_llm_config_response<R: Runtime>(app: &tauri::AppHandle<R>) -> LlmConfigResponse {
    let provider = read_active_provider(app).unwrap_or_else(|| "deepseek".to_string());
    let configured = match provider.as_str() {
        // Ollama needs no key — it always counts as "configured" (it can
        // still fail at request time if the local server isn't running,
        // but that's a runtime concern, not a keychain one).
        "ollama" => true,
        "custom" => read_llm_key(app, "custom").is_some()
            && read_custom_config(app).is_some(),
        other => read_llm_key(app, other).is_some(),
    };

    let model = match provider.as_str() {
        "custom" => read_custom_config(app).map(|c| c.model),
        other => default_model_for(other).map(|s| s.to_string()),
    };

    let base_url = match provider.as_str() {
        "ollama" => Some(DEFAULT_OLLAMA_BASE_URL.to_string()),
        "custom" => read_custom_config(app).map(|c| c.base_url),
        _ => None,
    };

    LlmConfigResponse {
        provider,
        configured,
        model,
        base_url,
    }
}

/// Read-only: return the current LLM config. Never includes the API key.
#[tauri::command]
pub fn get_llm_config(app: tauri::AppHandle) -> LlmConfigResponse {
    build_llm_config_response(&app)
}

/// Return the provider schema. This is the static contract — the Python
/// sidecar has its own copy in `GET /api/settings/providers` and the two
/// should stay in sync. We hard-code it here so the Settings UI can render
/// the picker even before the sidecar is up.
#[tauri::command]
pub fn get_provider_schema() -> Vec<ProviderSchema> {
    vec![
        ProviderSchema {
            id: "deepseek".to_string(),
            label: "DeepSeek".to_string(),
            requires_key: true,
            default_model: "deepseek-chat".to_string(),
            fields: vec!["api_key".to_string()],
        },
        ProviderSchema {
            id: "openai".to_string(),
            label: "OpenAI".to_string(),
            requires_key: true,
            default_model: "gpt-4o-mini".to_string(),
            fields: vec!["api_key".to_string()],
        },
        ProviderSchema {
            id: "anthropic".to_string(),
            label: "Anthropic".to_string(),
            requires_key: true,
            default_model: "claude-3-5-sonnet-20241022".to_string(),
            fields: vec!["api_key".to_string()],
        },
        ProviderSchema {
            id: "ollama".to_string(),
            label: "Ollama (本地)".to_string(),
            requires_key: false,
            default_model: "qwen2.5:7b".to_string(),
            fields: vec!["base_url".to_string(), "model".to_string()],
        },
        ProviderSchema {
            id: "custom".to_string(),
            label: "Custom (OpenAI-compatible)".to_string(),
            requires_key: true,
            default_model: String::new(),
            fields: vec![
                "api_key".to_string(),
                "base_url".to_string(),
                "model".to_string(),
            ],
        },
    ]
}
