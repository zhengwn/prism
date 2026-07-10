//! Smoke test for the v0.2a+ secrets helpers + JSON serialization layout.
//!
//! Run with `cargo test --test llm_config_smoke` or just `cargo test`.
//!
//! Scope: the public, side-effect-free helpers in `secrets.rs`.
//! The actual encrypted-file roundtrip is exercised by
//! `keystore_smoke.rs`, which uses a tempdir + the `keystore::write_*_at`
//! path-taking API so it doesn't need a Tauri AppHandle.
//!
//! What's covered here:
//!
//! - `llm_key_username` formatter output (the wire contract with the
//!   keystore layout).
//! - `is_known_provider` / `default_model_for` (the canonical id set).
//! - `CustomLlmConfig` JSON shape (snake_case on disk; consumed by
//!   the sidecar's `active_provider.json` reader).
//! - Tauri IPC serde contract: `LlmConfigResponse` / `LlmConfigInput` /
//!   `ProviderSchema` must use camelCase on the JS boundary.
//! - v0.2a legacy constant pin: the migration key (`deepseek-api-key`)
//!   is distinct from the new `llm-key:deepseek` slot.

use prism_lib::secrets;

// ---------------------------------------------------------------------------
// Pure helper tests — no IO, always run.
// ---------------------------------------------------------------------------

#[test]
fn llm_key_username_format() {
    assert_eq!(secrets::llm_key_username("minimax"), "llm-key:minimax");
    assert_eq!(secrets::llm_key_username("deepseek"), "llm-key:deepseek");
    // Unknown providers are still stringified the same way — the helper is
    // a pure formatter, validation lives in `is_known_provider`.
    assert_eq!(secrets::llm_key_username("nope"), "llm-key:nope");
}

#[test]
fn is_known_provider_recognises_canonical_ids() {
    for id in ["deepseek", "minimax"] {
        assert!(
            secrets::is_known_provider(id),
            "expected {id} to be a known provider"
        );
    }
    assert!(!secrets::is_known_provider(""));
    assert!(!secrets::is_known_provider("gpt-5"));
    assert!(!secrets::is_known_provider("DEEPSEEK"));
    // v0.2a- removed providers must NOT be accepted.
    assert!(!secrets::is_known_provider("openai"));
    assert!(!secrets::is_known_provider("anthropic"));
    assert!(!secrets::is_known_provider("ollama"));
    assert!(!secrets::is_known_provider("custom"));
}

#[test]
fn default_model_per_provider() {
    assert_eq!(secrets::default_model_for("deepseek"), Some("deepseek-v4-pro"));
    assert_eq!(secrets::default_model_for("minimax"), Some("MiniMax-M3"));
    assert_eq!(secrets::default_model_for("nope"), None);
}

#[test]
fn custom_llm_config_json_roundtrip() {
    let cfg = secrets::CustomLlmConfig {
        base_url: "https://api.example.com/v1".to_string(),
        model: "my-finetune-v3".to_string(),
    };

    let json = serde_json::to_string(&cfg).expect("serialize");
    // The on-disk shape is part of the public contract with the sidecar
    // (which reads ~/.prism/active_provider.json) and the keystore
    // (which stores the literal JSON string). Pin the field names.
    assert!(
        json.contains("\"base_url\""),
        "expected base_url field, got {json}"
    );
    assert!(
        json.contains("\"model\""),
        "expected model field, got {json}"
    );
    assert!(json.contains("api.example.com"));
    assert!(json.contains("my-finetune-v3"));

    let parsed: secrets::CustomLlmConfig =
        serde_json::from_str(&json).expect("deserialize");
    assert_eq!(parsed, cfg);
}

#[test]
fn custom_llm_config_tolerates_extra_fields() {
    // Forward compatibility: a future build might add fields like
    // `temperature` or `timeout`. Older builds reading new JSON should
    // not panic; they just ignore the extras (serde default behavior).
    let raw = r#"{"base_url":"https://x","model":"y","temperature":0.7}"#;
    let cfg: secrets::CustomLlmConfig =
        serde_json::from_str(raw).expect("deserialize with extras");
    assert_eq!(cfg.base_url, "https://x");
    assert_eq!(cfg.model, "y");
}

#[test]
fn legacy_v0_2a_username_does_not_collide_with_v0_2a_providers() {
    // The legacy `deepseek-api-key` slot is the source for the v0.2a →
    // v0.2a-providers migration. The migration runs at startup in
    // `keystore::migrate_from_keychain_if_needed`, copying the value to
    // the new `llm-key:deepseek` slot. This test just guards against
    // accidental renames of the legacy constant.
    assert_eq!(secrets::USERNAME_DEEPSEEK_LEGACY, "deepseek-api-key");
    assert_ne!(secrets::USERNAME_DEEPSEEK_LEGACY, secrets::llm_key_username("deepseek"));
}

// ---------------------------------------------------------------------------
// Tauri IPC serde contract — camelCase on the JS boundary.
//
// v0.2a removed a hard runtime failure: the frontend's `LlmConfigUpdate` TS
// type uses camelCase (`apiKey`, `baseUrl`), but the Rust struct fields are
// snake_case. Without `#[serde(rename_all = "camelCase")]` the
// `set_llm_config` command would silently drop `apiKey` / `baseUrl` at
// deserialisation time and then either error out or write garbage to the
// keystore. These tests pin the public IPC shape so a future refactor that
// drops the attribute fails CI instead of breaking prod.
// ---------------------------------------------------------------------------

#[test]
fn llm_config_response_serialises_camel_case() {
    // Frontend (TS) reads `baseUrl` from the response. If a future refactor
    // drops `rename_all = "camelCase"`, the JS side would see `base_url` and
    // break (LlmConfig.baseUrl would be undefined).
    let resp = secrets::LlmConfigResponse {
        provider: "minimax".to_string(),
        configured: true,
        key_last4: Some("cdef".to_string()),
        key_length: Some(32),
        model: Some("MiniMax-M3".to_string()),
        base_url: Some("https://api.minimaxi.com/v1".to_string()),
    };
    let json = serde_json::to_string(&resp).expect("serialize");
    assert!(
        json.contains("\"baseUrl\""),
        "expected camelCase baseUrl, got {json}"
    );
    assert!(
        !json.contains("\"base_url\""),
        "snake_case base_url leaked into IPC payload: {json}"
    );
    // SettingsPage renders the length-matched key mask from `keyLast4` +
    // `keyLength`. Both are two-word fields, so they break the same way
    // `baseUrl` would if the rename attribute is ever dropped.
    assert!(
        json.contains("\"keyLast4\"") && json.contains("\"keyLength\""),
        "expected camelCase keyLast4/keyLength, got {json}"
    );
    assert!(
        !json.contains("\"key_last4\"") && !json.contains("\"key_length\""),
        "snake_case key_last4/key_length leaked into IPC payload: {json}"
    );
    // `provider`, `configured`, `model` are already single-word — they
    // should serialise as-is. Guard against any future global rename.
    assert!(json.contains("\"provider\":\"minimax\""));
    assert!(json.contains("\"configured\":true"));
    assert!(json.contains("\"model\":\"MiniMax-M3\""));
}

#[test]
fn llm_config_input_deserialises_camel_case() {
    // The JS side sends `{ provider, apiKey, model, baseUrl }`. Verify
    // serde accepts that exact shape — the deserialised struct should
    // carry the values under their snake_case field names.
    let raw = r#"{
        "provider": "minimax",
        "apiKey": "ey-test-camelcase",
        "model": "MiniMax-M3",
        "baseUrl": "https://api.minimaxi.com/v1"
    }"#;
    let input: secrets::LlmConfigInput =
        serde_json::from_str(raw).expect("camelCase input must deserialise");
    assert_eq!(input.provider, "minimax");
    assert_eq!(input.api_key.as_deref(), Some("ey-test-camelcase"));
    assert_eq!(input.model.as_deref(), Some("MiniMax-M3"));
    assert_eq!(input.base_url.as_deref(), Some("https://api.minimaxi.com/v1"));

    // Sanity: a snake_case payload DOES technically deserialise (because
    // `#[serde(default)]` lets unknown fields be silently dropped) — but
    // the camelCase-mapped fields stay None. This is a real footgun and
    // the test pins the "data loss" behaviour so any future tweak (e.g.
    // adding `#[serde(deny_unknown_fields)]`) shows up as an explicit
    // failure rather than a silent regression. The positive case above is
    // what actually matters in production: callers must send camelCase.
    let snake = r#"{
        "provider": "minimax",
        "api_key": "sk-test",
        "model": "MiniMax-M3",
        "base_url": "https://api.minimaxi.com/v1"
    }"#;
    let parsed: secrets::LlmConfigInput =
        serde_json::from_str(snake).expect("snake_case deserialises but drops data");
    // `api_key` (camelCase form) stays None because the JSON key
    // "api_key" doesn't match the renamed field. Document the footgun.
    assert!(
        parsed.api_key.is_none(),
        "snake_case `api_key` must NOT populate api_key field when rename_all = camelCase"
    );
    assert!(
        parsed.base_url.is_none(),
        "snake_case `base_url` must NOT populate base_url field when rename_all = camelCase"
    );
}

#[test]
fn provider_schema_serialises_camel_case() {
    // `get_provider_schema` returns a Vec<ProviderSchema> to the JS side.
    // Pin that `requiresKey` / `defaultModel` are camelCase so the TS
    // `ProviderSchema` type matches.
    let schemas = secrets::get_provider_schema();
    assert_eq!(schemas.len(), 2, "v0.2a+ supports exactly 2 providers");
    let json = serde_json::to_string(&schemas.first().unwrap()).expect("serialize");
    assert!(
        json.contains("\"requiresKey\""),
        "expected camelCase requiresKey, got {json}"
    );
    assert!(
        json.contains("\"defaultModel\""),
        "expected camelCase defaultModel, got {json}"
    );
    assert!(
        !json.contains("\"requires_key\""),
        "snake_case requires_key leaked into IPC payload: {json}"
    );
    assert!(
        !json.contains("\"default_model\""),
        "snake_case default_model leaked into IPC payload: {json}"
    );
}
