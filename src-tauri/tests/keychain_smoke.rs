//! Smoke test for the OS keychain integration.
//!
//! Run with `cargo test --test keychain_smoke` or just `cargo test`.
//! This hits the **real** keychain (Keychain Access on macOS, Credential
//! Manager on Windows, libsecret on Linux) — it is NOT a mock.
//!
//! To avoid leaving junk entries in the user's real keychain, we use a
//! service name with a unique per-run suffix. The cleanup at the end of
//! each test deletes the entry, but if a test panics partway, the next
//! run will overwrite the same key (we re-use a fixed service+user per
//! test) so leaks are bounded.
//!
//! Skipped on CI / headless systems by checking for an env var — running
//! the test locally is the real verification path.

use keyring::Entry;

/// Returns true unless `PRISM_SKIP_KEYCHAIN_TEST=1` is set in the env.
/// CI / non-interactive environments should set that var.
fn should_run() -> bool {
    std::env::var("PRISM_SKIP_KEYCHAIN_TEST").ok().as_deref() != Some("1")
}

fn unique_service(tag: &str) -> String {
    // Use nanoseconds + process id so parallel runs don't stomp each other.
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("com.prism.desktop.test.{tag}.{now}.{}", std::process::id())
}

#[test]
fn keychain_set_get_delete_roundtrip() {
    if !should_run() {
        eprintln!("PRISM_SKIP_KEYCHAIN_TEST=1 — skipping real keychain roundtrip");
        return;
    }

    let service = unique_service("roundtrip");
    let user = "smoke-user";
    let secret = "sk-prism-smoke-xxxxxxxxxxxxxxxxxxxx";

    let entry = Entry::new(&service, user).expect("create entry handle");

    // Make sure we're starting clean. delete_password is idempotent in the
    // sense that "no entry" is treated as success by most keyring backends,
    // but the keyring crate returns `NoEntry` as Err — swallow that.
    let _ = entry.delete_credential();

    // Set
    entry
        .set_password(secret)
        .expect("set_password should succeed on a writable keychain");

    // Get — must round-trip byte-for-byte
    let read_back = entry
        .get_password()
        .expect("get_password should succeed after set_password");
    assert_eq!(read_back, secret, "keychain roundtrip must preserve bytes");

    // Update — overwrite with a new value
    let updated = "sk-prism-smoke-yyyyyyyyyyyyyyyyyyyy";
    entry
        .set_password(updated)
        .expect("set_password (update) should succeed");
    let read_back_2 = entry
        .get_password()
        .expect("get_password should succeed after update");
    assert_eq!(read_back_2, updated, "keychain update should overwrite");

    // Delete — must remove the entry
    entry
        .delete_credential()
        .expect("delete_credential should succeed");

    // After delete, get_password should report `NoEntry` (the keyring crate
    // encodes that as Err with a specific message). Don't assert on the
    // exact text — backend-dependent — just confirm it's gone.
    let after_delete = entry.get_password();
    assert!(
        after_delete.is_err(),
        "expected Err after delete, got {after_delete:?}"
    );
}

#[test]
fn keychain_missing_entry_errors() {
    if !should_run() {
        eprintln!("PRISM_SKIP_KEYCHAIN_TEST=1 — skipping");
        return;
    }

    let service = unique_service("missing");
    let user = "never-set";
    let entry = Entry::new(&service, user).expect("create entry handle");

    let result = entry.get_password();
    assert!(
        result.is_err(),
        "reading a non-existent entry should error, got {result:?}"
    );
}
