/**
 * API-key storage backed by a local encrypted-file keystore.
 *
 * The actual storage lives in `keystore.rs`. This module is a thin
 * compatibility shim that preserves the v0.2a public API
 * (`read_llm_key`, `write_llm_key`, `read_active_provider`, …, plus the
 * `get_api_key_status` / `set_api_key` / `clear_api_key` Tauri commands)
 * so the rest of the Tauri shell — and the frontend's IPC contract — is
 * unchanged.
 *
 * Verified-dead note (v0.2c review): as of this writing, the frontend's
 * only `invoke()` calls are `get_llm_config`, `set_llm_config`, and
 * `reveal_llm_key` (grep `src/` for `invoke(` to confirm). `get_api_key_status`,
 * `set_api_key`, `clear_api_key`, and `get_provider_schema` are registered
 * in `lib.rs`'s `invoke_handler!` and still compile/work, but nothing in
 * the current React app calls them — they're inert back-compat surface,
 * not an active fallback path. Don't trust doc comments below (including
 * ones in this file) that describe them as "what the Settings page uses";
 * re-grep the frontend before relying on that.
 *
 * # v0.2a → v0.2a-providers layout (unchanged contract)
 *
 * | Slot                | Type            | Purpose                          |
 * |---------------------|-----------------|----------------------------------|
 * | `llm-provider:active` | provider id   | which provider is active         |
 * | `llm-key:deepseek`    | api key       | DeepSeek key                     |
 * | `llm-key:minimax`     | api key       | MiniMax M3 key                   |
 * | `llm-config:custom`   | JSON blob     | MiniMax override (base_url + model) |
 *
 * All slots used to live in the OS keychain under service
 * `com.prism.desktop`. v0.2a+ moves them into a single encrypted file at
 * `~/.prism/keystore.json` (master key at `~/.prism/keystore.key`).
 * See `keystore.rs` for the on-disk format and encryption details.
 *
 * # Migration from the v0.2a OS-keychain layout
 *
 * `keystore::migrate_from_keychain_if_needed` runs once on first
 * startup. If the new keystore file is missing but the OS keychain has
 * entries, it copies them across and deletes the keychain entries. The
 * first call triggers one macOS prompt; after that the keychain is
 * never touched again. Idempotent — once the keystore file exists, the
 * migration is a no-op.
 *
 * # SECURITY
 *
 * - The *default* read path (`get_api_key_status`, `get_llm_config`) never
 *   returns the key value — only `configured: bool` (+ `key_last4` /
 *   `key_length`) crosses the JS↔Rust bridge. The one deliberate
 *   exception is `reveal_llm_key`, called only when the user explicitly
 *   clicks the Settings page's "show key" eye toggle; see its own
 *   SECURITY doc comment below for the threat-model argument. Anything
 *   that describes this boundary as "the frontend can never get the
 *   key" is describing the default path only, not `reveal_llm_key`.
 * - The keystore file lives at `~/.prism/keystore.json` with permission
 *   0600 (Unix). The trust model is a single-user desktop app — anyone
 *   with read access to the user's home directory can read the file.
 *   This matches the trust model of the macOS Keychain in practice
 *   (an attacker who can read the macOS Keychain database can
 *   also read the keys).
 * - The legacy `set_api_key` / `get_api_key_status` / `clear_api_key` Tauri
 *   commands remain as thin wrappers around the deepseek slot for backwards
 *   compatibility with the v0.2a Settings page.
 */

use serde::{Deserialize, Serialize};
use tauri::Runtime;

/// Re-export of the encrypted-file storage backend. All keychain-style
/// helpers below are thin forwarders to this module. The actual IO /
/// encryption / migration logic lives in `keystore.rs`.
use crate::keystore;

/// Service name registered in the OS keychain. Bundled with the bundle
/// identifier (`com.prism.desktop`) for namespace safety.
pub const SERVICE: &str = "com.prism.desktop";

// ---------------------------------------------------------------------------
// Username constants for the multi-slot keychain layout.
// ---------------------------------------------------------------------------

/// Username that stores the id of the currently active LLM provider
/// (e.g. `"deepseek"`, `"minimax"`).
pub const USERNAME_LLM_PROVIDER_ACTIVE: &str = "llm-provider:active";

/// Username that stores the JSON `{"base_url": "...", "model": "..."}` blob
/// for the `minimax` provider (kept for parity with the old custom slot).
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
pub const ENV_MINIMAX_API_KEY: &str = "MINIMAX_API_KEY";
pub const ENV_MINIMAX_API_BASE: &str = "MINIMAX_API_BASE";
pub const ENV_PRISM_ACTIVE_PROVIDER: &str = "PRISM_ACTIVE_PROVIDER";

/// Default MiniMax API base URL. Used when no `llm-config:custom.base_url`
/// is set. MiniMax exposes an OpenAI-compatible endpoint here.
pub const DEFAULT_MINIMAX_API_BASE: &str = "https://api.minimaxi.com/v1";

// ---------------------------------------------------------------------------
// Known provider ids.
// ---------------------------------------------------------------------------

/// Canonical list of provider ids. Order matches the Settings UI dropdown.
pub const KNOWN_PROVIDERS: &[&str] = &["deepseek", "minimax"];

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
///
/// `key_last4` is the trailing 4 chars of the active key (when present)
/// — the Settings UI renders it as `••••xxxx` inside the password field
/// so the user can see which key is on disk without exposing the value
/// itself. The eye toggle still flips to `type="text"` which would
/// surface the full masked string; the field stays `readOnly` in that
/// mode so the user has to explicitly click "edit" (or focus the
/// input) before they can type a replacement.
///
/// `key_length` is the total character count of the stored key (when
/// present) — the Settings UI uses it to render a length-matched
/// password mask (`•` × `keyLength` + `keyLast4`) so the field width
/// tracks the real secret. Exposing the length is safe: it carries no
/// information about the key's contents, and the field is already
/// `type="password"` so the mask is rendered by the browser as dots
/// regardless. Returns `None` when no key is set.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LlmConfigResponse {
    pub provider: String,
    pub configured: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub key_last4: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub key_length: Option<usize>,
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
// Low-level helpers — all forwarded to `keystore`. The Tauri-handle
// wrappers in `keystore.rs` resolve `~/.prism/` and call the
// path-taking core. Anything that needs a multi-step migration lives in
// `keystore::migrate_from_keychain_if_needed` (called once on startup
// from `lib.rs`).
// ---------------------------------------------------------------------------

/// Custom-mode configuration: base_url + model. Serialized to JSON in
/// the keystore.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CustomLlmConfig {
    pub base_url: String,
    pub model: String,
}

// ---------------------------------------------------------------------------
// Public helpers for the multi-slot layout — all thin forwarders to
// `keystore::*`. Signatures preserved verbatim from the keychain era so
// `sidecar.rs` doesn't need to change.
// ---------------------------------------------------------------------------

/// Read the API key for a given provider. Returns `None` if absent or on
/// keystore IO / decrypt error.
pub fn read_llm_key<R: Runtime>(app: &tauri::AppHandle<R>, provider: &str) -> Option<String> {
    keystore::read_llm_key(app, provider)
}

/// Write the API key for a given provider. The caller is expected to
/// trim and basic-validate the key before calling.
pub fn write_llm_key<R: Runtime>(
    app: &tauri::AppHandle<R>,
    provider: &str,
    key: &str,
) -> Result<(), String> {
    keystore::write_llm_key(app, provider, key)
}

/// Delete the API key for a given provider. Idempotent — deleting a
/// missing entry is not an error.
pub fn delete_llm_key<R: Runtime>(
    app: &tauri::AppHandle<R>,
    provider: &str,
) -> Result<(), String> {
    keystore::delete_llm_key(app, provider)
}

/// Last 4 characters of the stored API key — useful for the Settings UI
/// "••••abcd" rendering. Returns `None` if no key is set.
pub fn key_last4<R: Runtime>(app: &tauri::AppHandle<R>, provider: &str) -> Option<String> {
    keystore::key_last4(app, provider)
}

/// Length of the stored API key (in characters). Used by the Settings UI
/// to render a length-matched password mask so the field width tracks the
/// real secret. Returns `None` if no key is set or decryption failed.
pub fn key_length<R: Runtime>(app: &tauri::AppHandle<R>, provider: &str) -> Option<usize> {
    keystore::key_length(app, provider)
}

/// Read the stored `custom` provider config (base_url + model).
/// Returns `None` if absent or on IO error.
pub fn read_custom_config<R: Runtime>(app: &tauri::AppHandle<R>) -> Option<CustomLlmConfig> {
    keystore::read_custom_config(app)
}

/// Write the `custom` provider config (base_url + model).
pub fn write_custom_config<R: Runtime>(
    app: &tauri::AppHandle<R>,
    cfg: &CustomLlmConfig,
) -> Result<(), String> {
    keystore::write_custom_config(app, cfg)
}

/// Read the active provider id. The v0.2a → v0.2a-providers lazy
/// migration is now handled by `keystore::migrate_from_keychain_if_needed`
/// at startup (one-shot, before the sidecar is spawned), so this
/// function just returns the persisted value or `None`.
///
/// Callers (e.g. `sidecar::spawn`) should fall back to a sensible
/// default such as `"deepseek"` when this returns `None`.
pub fn read_active_provider<R: Runtime>(app: &tauri::AppHandle<R>) -> Option<String> {
    keystore::read_active_provider(app)
}

/// Write the active provider id. Empty strings and unknown ids are
/// rejected.
pub fn write_active_provider<R: Runtime>(
    app: &tauri::AppHandle<R>,
    provider: &str,
) -> Result<(), String> {
    keystore::write_active_provider(app, provider)
}

// ---------------------------------------------------------------------------
// v0.2a backward-compat thin wrappers. The v0.2a Settings page used to
// invoke the `get_api_key_status` / `set_api_key` / `clear_api_key` Tauri
// commands built on these; the current Settings page does not (it only
// calls `get_llm_config` / `set_llm_config` / `reveal_llm_key` — see the
// module doc comment's "Verified-dead note"). Kept for back-compat in case
// something still links against them; route through the new multi-slot
// helpers so the keychain layout stays consistent regardless of which API
// a caller uses.
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
// SECURITY: the commands in this block deliberately do NOT return the key
// value to the webview — only a `configured: bool` flag (+ `key_last4` /
// `key_length` further down). The frontend's job is to display "API key
// set" or "no API key", and to accept the user's paste. `reveal_llm_key`
// (below) is the sole, opt-in exception to this rule — see its own doc
// comment.
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

/// Default models for the preset providers. The `minimax` provider's
/// model is hard-coded (M3 with OpenAI-compatible prefix) so the
/// Python sidecar's `default_model` and the Tauri schema agree.
pub fn default_model_for(provider: &str) -> Option<&'static str> {
    match provider {
        "deepseek" => Some("deepseek-v4-pro"),
        "minimax" => Some("MiniMax-M3"),
        _ => None,
    }
}

/// Build the `LlmConfigResponse` for the current keychain state. Used by
/// both `get_llm_config` and `set_llm_config` (which returns the
/// post-write state).
pub fn build_llm_config_response<R: Runtime>(app: &tauri::AppHandle<R>) -> LlmConfigResponse {
    let provider = read_active_provider(app).unwrap_or_else(|| "deepseek".to_string());
    // v0.2a+: both supported providers (DeepSeek, MiniMax) require a
    // key — `configured` is just "is the keychain slot populated?".
    let configured = read_llm_key(app, &provider).is_some();

    // Surface the trailing 4 chars of the key so the Settings UI can
    // render `••••xxxx` and the user knows which key is on disk
    // without ever exposing the secret value itself. `None` when the
    // slot is empty (or decryption failed) — UI falls back to an
    // empty / placeholder state.
    let key_last4 = key_last4(app, &provider);

    // Also surface the key length so the password input can render
    // a length-matched mask (one dot per character of the real key)
    // instead of a fixed placeholder. Same secret-safety story as
    // `keyLast4`: a length is non-sensitive, and the field is
    // `type="password"` so the mask is rendered as bullets either way.
    let key_length = key_length(app, &provider);

    let model = default_model_for(&provider).map(|s| s.to_string());

    // Only MiniMax exposes a user-overridable base_url (its OpenAI-
    // compatible endpoint). DeepSeek uses the canonical API directly.
    let base_url = if provider == "minimax" {
        Some(DEFAULT_MINIMAX_API_BASE.to_string())
    } else {
        None
    };

    LlmConfigResponse {
        provider,
        configured,
        key_last4,
        key_length,
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
/// should stay in sync. The intent was for the Settings UI to render the
/// picker from this command before the sidecar is up; in the current
/// frontend, `SettingsPage.tsx` unconditionally calls
/// `api.listProviders()` (the sidecar HTTP endpoint) instead and has no
/// fallback to this command, so that "renders before the sidecar is up"
/// behavior does not currently exist — the picker just won't have data
/// until the sidecar responds. This command is otherwise unused; kept
/// as a hard-coded reference copy and in case a future revision wires
/// the fallback up for real.
#[tauri::command]
pub fn get_provider_schema() -> Vec<ProviderSchema> {
    vec![
        ProviderSchema {
            id: "deepseek".to_string(),
            label: "DeepSeek".to_string(),
            requires_key: true,
            default_model: "deepseek-v4-pro".to_string(),
            fields: vec!["api_key".to_string()],
        },
        ProviderSchema {
            id: "minimax".to_string(),
            label: "MiniMax".to_string(),
            requires_key: true,
            default_model: "MiniMax-M3".to_string(),
            fields: vec!["api_key".to_string()],
        },
    ]
}

/// Return the active provider's full API key (decrypted from the
/// keystore). The frontend only calls this when the user explicitly
/// clicks the "show" eye toggle — the key never crosses the IPC
/// boundary otherwise.
///
/// SECURITY: this command hands a plaintext secret to the renderer.
/// That's acceptable in this threat model because:
///   * The Settings window is a Tauri-controlled webview, not a
///     browser tab; no third-party JS can run there.
///   * The renderer is expected to drop the value as soon as the user
///     hides the field again (it doesn't have to, but anything else
///     would leak through the React DevTools / a console paste).
///   * `get_llm_config` (the routine read path) still returns only
///     `configured: boolean` + `keyLast4` — the eye toggle is opt-in.
#[tauri::command]
pub fn reveal_llm_key(app: tauri::AppHandle, provider: String) -> Option<String> {
    read_llm_key(&app, &provider)
}
