//! Smoke test for the v0.2a+ keychain + JSON serialization layout.
//!
//! Run with `cargo test --test llm_config_smoke` or just `cargo test`.
//!
//! The pure helpers (username construction, provider id validation,
//! default-model lookup, CustomLlmConfig JSON shape) are unit-tested
//! directly. The keychain roundtrip is exercised against the **real**
//! OS keychain using a unique per-run service name so we don't pollute
//! the user's real `com.prism.desktop` entry set.
//!
//! Set `PRISM_SKIP_KEYCHAIN_TEST=1` to skip the real-keychain portions
//! (CI / headless systems).

use keyring::Entry;
use prism_lib::secrets;

// ---------------------------------------------------------------------------
// Pure helper tests — no keychain access, always run.
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
    // (which reads ~/.prism/active_provider.json) and the keychain
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

// ---------------------------------------------------------------------------
// Real keychain roundtrip — gated on env, uses a unique per-run service
// name. Mirrors the pattern in `keychain_smoke.rs`.
// ---------------------------------------------------------------------------

fn should_run() -> bool {
    std::env::var("PRISM_SKIP_KEYCHAIN_TEST").ok().as_deref() != Some("1")
}

fn unique_service(tag: &str) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("com.prism.desktop.test.llmcfg.{tag}.{now}.{}", std::process::id())
}

/// Drive the multi-slot layout through the raw `keyring` crate, mirroring
/// what the Tauri-keyring plugin does internally. This validates the
/// username construction + slot layout without needing a full AppHandle.
fn raw_set(service: &str, username: &str, value: &str) {
    let entry = Entry::new(service, username).expect("create entry");
    let _ = entry.delete_credential(); // idempotent clean start
    entry.set_password(value).expect("set_password");
}

fn raw_get(service: &str, username: &str) -> Option<String> {
    let entry = Entry::new(service, username).expect("create entry");
    entry.get_password().ok()
}

fn raw_delete(service: &str, username: &str) {
    let entry = Entry::new(service, username).expect("create entry");
    let _ = entry.delete_credential(); // ignore "no entry"
}

#[test]
fn multi_slot_keychain_roundtrip() {
    if !should_run() {
        eprintln!("PRISM_SKIP_KEYCHAIN_TEST=1 — skipping real keychain roundtrip");
        return;
    }

    let service = unique_service("multi-slot");

    // Sanity: all usernames should be distinct so the slots don't collide.
    let u_minimax = secrets::llm_key_username("minimax");
    let u_active = secrets::USERNAME_LLM_PROVIDER_ACTIVE.to_string();
    let u_custom = secrets::USERNAME_LLM_CONFIG_CUSTOM.to_string();
    let names: Vec<&str> = vec![&u_minimax, &u_active, &u_custom];
    for (i, a) in names.iter().enumerate() {
        for b in names.iter().skip(i + 1) {
            assert_ne!(a, b, "slot names must be unique: {a} vs {b}");
        }
    }

    // 1. Set active = minimax
    raw_set(&service, &u_active, "minimax");
    // 2. Write minimax key
    raw_set(&service, &u_minimax, "ey-test-minimax-xxxxxxxxxxxxxx");

    // 3. Read back
    assert_eq!(raw_get(&service, &u_active).as_deref(), Some("minimax"));
    // 4. Key roundtrips
    assert_eq!(
        raw_get(&service, &u_minimax).as_deref(),
        Some("ey-test-minimax-xxxxxxxxxxxxxx")
    );

    // 5. Switch to deepseek
    raw_set(&service, &u_active, "deepseek");
    raw_set(&service, "llm-key:deepseek", "sk-test-deepseek-yyyyyyyyyyy");

    // 6. Active pointer updated
    assert_eq!(raw_get(&service, &u_active).as_deref(), Some("deepseek"));
    // 7. DeepSeek key readable
    assert_eq!(
        raw_get(&service, "llm-key:deepseek").as_deref(),
        Some("sk-test-deepseek-yyyyyyyyyyy")
    );
    // 8. MiniMax key still there (we don't delete on provider switch)
    assert_eq!(
        raw_get(&service, &u_minimax).as_deref(),
        Some("ey-test-minimax-xxxxxxxxxxxxxx")
    );

    // 9. Custom config slot (used for MiniMax base_url + model overrides)
    let cfg = secrets::CustomLlmConfig {
        base_url: "https://api.minimaxi.com/v1".to_string(),
        model: "MiniMax-M3-highspeed".to_string(),
    };
    let blob = serde_json::to_string(&cfg).expect("serialize");
    raw_set(&service, &u_custom, &blob);
    let read_back = raw_get(&service, &u_custom).expect("custom config present");
    let parsed: secrets::CustomLlmConfig =
        serde_json::from_str(&read_back).expect("parse back");
    assert_eq!(parsed, cfg);

    // Cleanup
    raw_delete(&service, &u_active);
    raw_delete(&service, &u_minimax);
    raw_delete(&service, "llm-key:deepseek");
    raw_delete(&service, &u_custom);
}

#[test]
fn legacy_v0_2a_username_does_not_collide_with_v0_2a_providers() {
    // The legacy `deepseek-api-key` slot must remain readable until
    // migration runs. The migration is triggered from `read_active_provider`
    // inside the Tauri runtime, not from the raw keychain — this test
    // just guards against accidental renames of the legacy constant.
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
// keychain. These tests pin the public IPC shape so a future refactor that
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
