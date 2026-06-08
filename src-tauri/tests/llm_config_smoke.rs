//! Smoke test for the v0.2a-providers multi-slot keychain + custom-config
//! JSON serialization.
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
    assert_eq!(secrets::llm_key_username("openai"), "llm-key:openai");
    assert_eq!(secrets::llm_key_username("deepseek"), "llm-key:deepseek");
    assert_eq!(secrets::llm_key_username("custom"), "llm-key:custom");
    // Unknown providers are still stringified the same way — the helper is
    // a pure formatter, validation lives in `is_known_provider`.
    assert_eq!(secrets::llm_key_username("nope"), "llm-key:nope");
}

#[test]
fn is_known_provider_recognises_canonical_ids() {
    for id in ["deepseek", "openai", "anthropic", "ollama", "custom"] {
        assert!(
            secrets::is_known_provider(id),
            "expected {id} to be a known provider"
        );
    }
    assert!(!secrets::is_known_provider(""));
    assert!(!secrets::is_known_provider("gpt-5"));
    assert!(!secrets::is_known_provider("DEEPSEEK"));
}

#[test]
fn default_model_per_provider() {
    assert_eq!(secrets::default_model_for("deepseek"), Some("deepseek-chat"));
    assert_eq!(secrets::default_model_for("openai"), Some("gpt-4o-mini"));
    assert_eq!(
        secrets::default_model_for("anthropic"),
        Some("claude-3-5-sonnet-20241022")
    );
    assert_eq!(secrets::default_model_for("ollama"), Some("qwen2.5:7b"));
    assert_eq!(secrets::default_model_for("custom"), None);
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
    let u_openai = secrets::llm_key_username("openai");
    let u_anthropic = secrets::llm_key_username("anthropic");
    let u_active = secrets::USERNAME_LLM_PROVIDER_ACTIVE.to_string();
    let u_custom = secrets::USERNAME_LLM_CONFIG_CUSTOM.to_string();
    let names: Vec<&str> = vec![&u_openai, &u_anthropic, &u_active, &u_custom];
    for (i, a) in names.iter().enumerate() {
        for b in names.iter().skip(i + 1) {
            assert_ne!(a, b, "slot names must be unique: {a} vs {b}");
        }
    }

    // 1. Set active = openai
    raw_set(&service, &u_active, "openai");
    // 2. Write openai key
    raw_set(&service, &u_openai, "sk-test-openai-xxxxxxxxxxxxxxx");

    // 3. Read back
    assert_eq!(raw_get(&service, &u_active).as_deref(), Some("openai"));
    // 4. Key roundtrips
    assert_eq!(
        raw_get(&service, &u_openai).as_deref(),
        Some("sk-test-openai-xxxxxxxxxxxxxxx")
    );

    // 5. Switch to anthropic
    raw_set(&service, &u_active, "anthropic");
    raw_set(&service, &u_anthropic, "sk-ant-test-yyyyyyyyyyyyyyy");

    // 6. Active pointer updated
    assert_eq!(raw_get(&service, &u_active).as_deref(), Some("anthropic"));
    // 7. Anthropic key readable
    assert_eq!(
        raw_get(&service, &u_anthropic).as_deref(),
        Some("sk-ant-test-yyyyyyyyyyyyyyy")
    );
    // 8. OpenAI key still there (we don't delete on provider switch)
    assert_eq!(
        raw_get(&service, &u_openai).as_deref(),
        Some("sk-test-openai-xxxxxxxxxxxxxxx")
    );

    // 9. Custom config slot — JSON blob
    let cfg = secrets::CustomLlmConfig {
        base_url: "https://api.deepseek.com/v1".to_string(),
        model: "deepseek-reasoner".to_string(),
    };
    let blob = serde_json::to_string(&cfg).expect("serialize");
    raw_set(&service, &u_custom, &blob);
    let read_back = raw_get(&service, &u_custom).expect("custom config present");
    let parsed: secrets::CustomLlmConfig =
        serde_json::from_str(&read_back).expect("parse back");
    assert_eq!(parsed, cfg);

    // Cleanup
    raw_delete(&service, &u_active);
    raw_delete(&service, &u_openai);
    raw_delete(&service, &u_anthropic);
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
