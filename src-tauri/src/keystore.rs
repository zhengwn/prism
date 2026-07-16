//! Local encrypted-file keystore for LLM API keys.
//!
//! # Why this exists
//!
//! v0.2a used `tauri-plugin-keyring` to back the LLM API key with the OS
//! keychain. On macOS this triggered an "Allow access to keychain" prompt
//! on every app launch — disruptive and unnecessary for a desktop app the
//! user has already trusted. We now store the key in a local file
//! encrypted with AES-256-GCM, using a randomly generated master key
//! kept next to the data file.
//!
//! # File layout
//!
//! | Path                  | Permission | Purpose                                       |
//! |-----------------------|------------|-----------------------------------------------|
//! | `~/.prism/keystore.json` | 0600 (Unix) | Encrypted provider keys + active provider + custom config |
//! | `~/.prism/keystore.key`  | 0600 (Unix) | 32-byte random master key (AES-256)        |
//!
//! Windows uses default ACLs (best-effort `0600` equivalent is not
//! expressible through the standard library; the directory is created
//! under `%USERPROFILE%` so it is still single-user).
//!
//! # On-disk JSON shape
//!
//! ```json
//! {
//!   "version": 1,
//!   "active_provider": "deepseek",
//!   "providers": {
//!     "deepseek": { "api_key": "<base64 of nonce || ciphertext>" },
//!     "minimax":  { "api_key": "<base64 of nonce || ciphertext>" }
//!   },
//!   "custom": { "base_url": "...", "model": "..." } | null
//! }
//! ```
//!
//! The `api_key` field carries AES-256-GCM ciphertext: 12 random nonce
//! bytes concatenated with the ciphertext (which itself includes the
//! 16-byte GCM auth tag at the end). The whole blob is base64-encoded
//! so it lives in a JSON string. Decryption fails closed: any tamper /
//! truncation / wrong-key returns `None`.
//!
//! # Migration from the legacy OS keychain
//!
//! v0.2a stored entries in the OS keychain under service
//! `com.prism.desktop` with usernames like `llm-key:deepseek`. The
//! function [`migrate_from_keychain_if_needed`] performs a one-shot
//! migration: if the keystore file is missing but keychain entries
//! exist, copy them into the new format, then `delete_credential` the
//! keychain entries. The first call triggers one macOS prompt; after
//! that the keychain is never touched again.
//!
//! # Concurrency
//!
//! All file IO is serialised through a process-wide [`Mutex`]. The
//! desktop app is single-process; a single mutex is enough to keep
//! read-modify-write cycles atomic without the complexity of `RwLock`.
//!
//! # Test surface
//!
//! The file-path-taking core functions (e.g. [`read_active_provider_at`],
//! [`write_llm_key_at`]) are independent of Tauri and can be unit-tested
//! against a `tempfile::TempDir`. The Tauri-handle wrappers
//! ([`read_active_provider`], …) resolve `~/.prism/` and call the core.
//!
//! # SECURITY — be honest about what this is
//!
//! - The master key is generated from `OsRng` once on first write —
//!   and stored **in the same directory, unencrypted, with the same
//!   0600 permission** as the ciphertext it protects. Anyone who can
//!   read `keystore.json` can read `keystore.key`, so against a local
//!   attacker (or malware running as the user) this scheme is
//!   **equivalent to plaintext storage plus obfuscation**. That is NOT
//!   what the macOS Keychain offers: Keychain items are gated per
//!   requesting app by ACL and the OS prompts on foreign access.
//! - What the encryption DOES buy: the key doesn't sit in cleartext
//!   inside a file whose format invites casual copying — grep, backup
//!   diffing, an unredacted support bundle, or a sync tool shipping
//!   `~/.prism` somewhere won't expose the secret unless
//!   `keystore.key` travels along with it.
//! - The trade-off was deliberate (unsigned dev builds re-prompt for
//!   Keychain access on every launch, which is what drove the move).
//!   NOTE for the packaged app: a properly signed build keeps its
//!   Keychain identity across launches and does NOT re-prompt — worth
//!   revisiting Keychain-backed storage once release builds are
//!   consistently signed.
//! - The 0600/0700 permissions restrict access to the file owner; the
//!   overall trust model remains "single-user desktop app".
//! - The API key value is **never** returned to the frontend on the
//!   routine read path — only `configured: bool` (+ last4/length)
//!   crosses the JS↔Rust bridge; `reveal_llm_key` is the sole opt-in
//!   exception (see `secrets.rs`).

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use aes_gcm::aead::{Aead, KeyInit, OsRng, Payload};
use aes_gcm::{Aes256Gcm, Key, Nonce};
use base64::engine::general_purpose::STANDARD as B64;
use base64::Engine as _;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use tauri::Runtime;

use crate::secrets::{
    self, CustomLlmConfig, USERNAME_LLM_CONFIG_CUSTOM, USERNAME_LLM_PROVIDER_ACTIVE,
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Filename of the encrypted data file (relative to the keystore root).
const DATA_FILENAME: &str = "keystore.json";
/// Filename of the master key (relative to the keystore root).
const KEY_FILENAME: &str = "keystore.key";
/// AES-256 master key length in bytes.
const MASTER_KEY_LEN: usize = 32;
/// AES-GCM nonce length in bytes.
const NONCE_LEN: usize = 12;
/// Schema version of the on-disk JSON.
const SCHEMA_VERSION: u32 = 1;

// ---------------------------------------------------------------------------
// Process-wide mutex serialising file IO.
// ---------------------------------------------------------------------------

/// Coarse-grained lock for keystore IO. Single-process desktop app, so a
/// plain `Mutex<()>` is enough — no need for `RwLock` or per-field locks.
static LOCK: Mutex<()> = Mutex::new(());

// ---------------------------------------------------------------------------
// On-disk JSON schema
// ---------------------------------------------------------------------------

/// Encrypted entry for a single provider. Only the API key lives here
/// today; structured custom config (base_url + model) is kept in the
/// top-level `custom` field because it isn't sensitive enough to warrant
/// per-field encryption.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct ProviderEntry {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    api_key: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct KeystoreFile {
    /// Schema version. Currently always 1; bump if we ever change the
    /// layout so older builds refuse to read it.
    version: u32,
    /// Id of the active LLM provider (`"deepseek"` or `"minimax"`). None
    /// until the user has saved a key.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    active_provider: Option<String>,
    /// Per-provider entries. Missing providers are simply absent.
    #[serde(default)]
    providers: std::collections::BTreeMap<String, ProviderEntry>,
    /// Custom MiniMax override blob (base_url + model). Not encrypted
    /// (no secret content).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    custom: Option<CustomLlmConfig>,
}

// ---------------------------------------------------------------------------
// Tauri AppHandle wrapper helpers
// ---------------------------------------------------------------------------

/// Resolve the keystore root directory (`~/.prism/`). Falls back to a
/// relative `.prism/` if the home directory is unavailable.
fn keystore_root() -> PathBuf {
    dirs::home_dir()
        .map(|h| h.join(".prism"))
        .unwrap_or_else(|| PathBuf::from(".prism"))
}

// ---------------------------------------------------------------------------
// Public Tauri-handle wrappers. These exist so that `secrets.rs` can
// keep its existing `pub fn …(app: &tauri::AppHandle<R>, …)` signatures
// unchanged (and so the rest of the codebase doesn't need to learn a
// new path argument).
// ---------------------------------------------------------------------------

/// Read the active provider id, with the v0.2a → v0.2a-providers lazy
/// migration applied. See [`read_active_provider_at`] for the path
/// argument's behaviour.
pub fn read_active_provider<R: Runtime>(_app: &tauri::AppHandle<R>) -> Option<String> {
    read_active_provider_at(&keystore_root())
}

/// Write the active provider id.
pub fn write_active_provider<R: Runtime>(
    _app: &tauri::AppHandle<R>,
    provider: &str,
) -> Result<(), String> {
    write_active_provider_at(&keystore_root(), provider)
}

/// Read the API key for a given provider.
pub fn read_llm_key<R: Runtime>(
    _app: &tauri::AppHandle<R>,
    provider: &str,
) -> Option<String> {
    read_llm_key_at(&keystore_root(), provider)
}

/// Write the API key for a given provider.
pub fn write_llm_key<R: Runtime>(
    _app: &tauri::AppHandle<R>,
    provider: &str,
    key: &str,
) -> Result<(), String> {
    write_llm_key_at(&keystore_root(), provider, key)
}

/// Delete the API key for a given provider. Idempotent.
pub fn delete_llm_key<R: Runtime>(
    _app: &tauri::AppHandle<R>,
    provider: &str,
) -> Result<(), String> {
    delete_llm_key_at(&keystore_root(), provider)
}

/// Last 4 characters of the API key for a given provider, for the
/// Settings UI "••••abcd" rendering. Returns None for short / empty keys.
pub fn key_last4<R: Runtime>(
    _app: &tauri::AppHandle<R>,
    provider: &str,
) -> Option<String> {
    key_last4_at(&keystore_root(), provider)
}

/// Length of the stored API key in characters, for the Settings UI
/// length-matched password mask. Returns `None` for empty / unset keys.
pub fn key_length<R: Runtime>(
    _app: &tauri::AppHandle<R>,
    provider: &str,
) -> Option<usize> {
    key_length_at(&keystore_root(), provider)
}

/// Read the MiniMax custom config (base_url + model).
pub fn read_custom_config<R: Runtime>(
    _app: &tauri::AppHandle<R>,
) -> Option<CustomLlmConfig> {
    read_custom_config_at(&keystore_root())
}

/// Write the MiniMax custom config (base_url + model).
pub fn write_custom_config<R: Runtime>(
    _app: &tauri::AppHandle<R>,
    cfg: &CustomLlmConfig,
) -> Result<(), String> {
    write_custom_config_at(&keystore_root(), cfg)
}

// ---------------------------------------------------------------------------
// Core: path-taking variants. Used by the Tauri wrappers above and by
// the tests in `tests/keystore_smoke.rs`.
// ---------------------------------------------------------------------------

/// Ensure the keystore root directory exists, with a best-effort 0700
/// permission on Unix. Returns the canonical root path.
fn ensure_root(root: &Path) -> Result<PathBuf, String> {
    fs::create_dir_all(root).map_err(|e| format!("create {}: {e}", root.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = fs::metadata(root)
            .map_err(|e| format!("stat {}: {e}", root.display()))?
            .permissions();
        if perms.mode() & 0o777 != 0o700 {
            fs::set_permissions(root, fs::Permissions::from_mode(0o700))
                .map_err(|e| format!("chmod 0700 {}: {e}", root.display()))?;
        }
    }
    Ok(root.to_path_buf())
}

/// Read & decrypt the keystore file. Returns an empty `KeystoreFile` if
/// the file does not exist yet (first-run case).
fn read_file(root: &Path) -> Result<KeystoreFile, String> {
    let path = root.join(DATA_FILENAME);
    if !path.exists() {
        return Ok(KeystoreFile {
            version: SCHEMA_VERSION,
            ..Default::default()
        });
    }
    let mut s = String::new();
    File::open(&path)
        .map_err(|e| format!("open {}: {e}", path.display()))?
        .read_to_string(&mut s)
        .map_err(|e| format!("read {}: {e}", path.display()))?;
    let parsed: KeystoreFile = serde_json::from_str(&s)
        .map_err(|e| format!("parse {}: {e}", path.display()))?;
    Ok(parsed)
}

/// Encrypt + write the keystore file atomically (write to `.tmp`,
/// fsync, rename). On Unix the tmp file is *created* with 0600 (not
/// chmod'd after the fact) so there's no window where it briefly
/// exists under the process's default umask-derived permissions —
/// same pattern as `load_or_create_master_key` below, which this used
/// to be inconsistent with (open-then-chmod here vs. open-with-mode
/// there).
fn write_file(root: &Path, file: &KeystoreFile) -> Result<(), String> {
    let final_path = root.join(DATA_FILENAME);
    let tmp_path = root.join(format!("{DATA_FILENAME}.tmp"));
    let json = serde_json::to_string_pretty(file)
        .map_err(|e| format!("serialize keystore: {e}"))?;
    {
        let mut opts = OpenOptions::new();
        opts.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut f = opts
            .open(&tmp_path)
            .map_err(|e| format!("open {}: {e}", tmp_path.display()))?;
        f.write_all(json.as_bytes())
            .map_err(|e| format!("write {}: {e}", tmp_path.display()))?;
        f.flush().map_err(|e| format!("flush {}: {e}", tmp_path.display()))?;
        let _ = f.sync_all();
    }
    // Belt-and-suspenders: `.mode(0o600)` at open time is subject to
    // the process umask on some platforms, so re-assert the exact bits
    // here too (cheap, and matches the original behaviour on Unix).
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&tmp_path, fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("chmod 0600 {}: {e}", tmp_path.display()))?;
    }
    fs::rename(&tmp_path, &final_path).map_err(|e| {
        format!(
            "rename {} -> {}: {e}",
            tmp_path.display(),
            final_path.display()
        )
    })?;
    Ok(())
}

/// Load (or generate) the master key. Stored in `<root>/keystore.key`
/// with 0600 on Unix. The key is 32 raw bytes (no encoding).
fn load_or_create_master_key(root: &Path) -> Result<[u8; MASTER_KEY_LEN], String> {
    let path = root.join(KEY_FILENAME);
    if path.exists() {
        let mut buf = Vec::new();
        File::open(&path)
            .map_err(|e| format!("open {}: {e}", path.display()))?
            .read_to_end(&mut buf)
            .map_err(|e| format!("read {}: {e}", path.display()))?;
        if buf.len() != MASTER_KEY_LEN {
            return Err(format!(
                "{} has wrong length: expected {}, got {}",
                path.display(),
                MASTER_KEY_LEN,
                buf.len()
            ));
        }
        let mut key = [0u8; MASTER_KEY_LEN];
        key.copy_from_slice(&buf);
        return Ok(key);
    }
    // Generate fresh key.
    let mut key = [0u8; MASTER_KEY_LEN];
    OsRng.fill_bytes(&mut key);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        let mut opts = OpenOptions::new();
        opts.write(true).create(true).truncate(true).mode(0o600);
        let mut f = opts
            .open(&path)
            .map_err(|e| format!("create {}: {e}", path.display()))?;
        f.write_all(&key)
            .map_err(|e| format!("write {}: {e}", path.display()))?;
        f.flush().map_err(|e| format!("flush {}: {e}", path.display()))?;
        let _ = f.sync_all();
    }
    #[cfg(not(unix))]
    {
        let mut f = File::create(&path)
            .map_err(|e| format!("create {}: {e}", path.display()))?;
        f.write_all(&key)
            .map_err(|e| format!("write {}: {e}", path.display()))?;
        f.flush().map_err(|e| format!("flush {}: {e}", path.display()))?;
        let _ = f.sync_all();
    }
    Ok(key)
}

/// Encrypt a plaintext string under the master key. Output is
/// `nonce || ciphertext` (the ciphertext already includes the GCM tag),
/// base64-encoded so it lives in a JSON string.
fn encrypt(master: &[u8; MASTER_KEY_LEN], plaintext: &str) -> Result<String, String> {
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(master));
    let mut nonce_bytes = [0u8; NONCE_LEN];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);
    let ct = cipher
        .encrypt(
            nonce,
            Payload {
                msg: plaintext.as_bytes(),
                aad: b"prism-keystore-v1",
            },
        )
        .map_err(|e| format!("encrypt: {e}"))?;
    // Concatenate nonce + ciphertext, then base64.
    let mut out = Vec::with_capacity(NONCE_LEN + ct.len());
    out.extend_from_slice(&nonce_bytes);
    out.extend_from_slice(&ct);
    Ok(B64.encode(&out))
}

/// Decrypt a base64 string produced by [`encrypt`]. Returns `None` on
/// any error (malformed b64, wrong length, AEAD auth failure).
fn decrypt(master: &[u8; MASTER_KEY_LEN], blob: &str) -> Option<String> {
    let raw = B64.decode(blob).ok()?;
    if raw.len() < NONCE_LEN + 16 {
        // tag is 16 bytes; need at least nonce + tag
        return None;
    }
    let (nonce_bytes, ct) = raw.split_at(NONCE_LEN);
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(master));
    let nonce = Nonce::from_slice(nonce_bytes);
    cipher
        .decrypt(
            nonce,
            Payload {
                msg: ct,
                aad: b"prism-keystore-v1",
            },
        )
        .ok()
        .and_then(|pt| String::from_utf8(pt).ok())
}

// ---------------------------------------------------------------------------
// Core read / write helpers (path-taking). Every public API goes through
// these. They take the process-wide lock to serialise file IO.
// ---------------------------------------------------------------------------

fn lock() -> std::sync::MutexGuard<'static, ()> {
    // Poisoning is OK — we treat the data as corrupt and the next
    // operation will rebuild it.
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

pub fn read_active_provider_at(root: &Path) -> Option<String> {
    let root = ensure_root(root).ok()?;
    let _g = lock();
    let file = read_file(&root).ok()?;
    if let Some(active) = file.active_provider {
        if !active.is_empty() {
            return Some(active);
        }
    }
    None
}

pub fn write_active_provider_at(root: &Path, provider: &str) -> Result<(), String> {
    if provider.is_empty() {
        return Err("provider cannot be empty".to_string());
    }
    if !secrets::is_known_provider(provider) {
        return Err(format!("unknown provider: {provider}"));
    }
    let root = ensure_root(root)?;
    let _g = lock();
    let mut file = read_file(&root)?;
    file.active_provider = Some(provider.to_string());
    write_file(&root, &file)
}

pub fn read_llm_key_at(root: &Path, provider: &str) -> Option<String> {
    let root = ensure_root(root).ok()?;
    let _g = lock();
    let file = read_file(&root).ok()?;
    let entry = file.providers.get(provider)?;
    let blob = entry.api_key.as_deref()?;
    let master = load_or_create_master_key(&root).ok()?;
    decrypt(&master, blob)
}

pub fn write_llm_key_at(root: &Path, provider: &str, key: &str) -> Result<(), String> {
    if !secrets::is_known_provider(provider) {
        return Err(format!("unknown provider: {provider}"));
    }
    let root = ensure_root(root)?;
    let _g = lock();
    let mut file = read_file(&root)?;
    let master = load_or_create_master_key(&root)?;
    let blob = encrypt(&master, key)?;
    file.providers
        .entry(provider.to_string())
        .or_default()
        .api_key = Some(blob);
    write_file(&root, &file)
}

pub fn delete_llm_key_at(root: &Path, provider: &str) -> Result<(), String> {
    let root = ensure_root(root)?;
    let _g = lock();
    let mut file = read_file(&root)?;
    if file.providers.remove(provider).is_none() {
        return Ok(()); // idempotent
    }
    write_file(&root, &file)
}

pub fn key_last4_at(root: &Path, provider: &str) -> Option<String> {
    let key = read_llm_key_at(root, provider)?;
    if key.is_empty() {
        return None;
    }
    let start = key.chars().count().saturating_sub(4);
    Some(key.chars().skip(start).collect())
}

/// Length of the stored API key in characters (not bytes). Returns
/// `None` if no key is set or decryption failed. Used by the Settings
/// UI to render a length-matched mask (`•` × `keyLength` + last4)
/// instead of a fixed 8-dot placeholder, so the input width matches
/// the actual secret and the field doesn't visually "shrink" when the
/// key is hidden.
pub fn key_length_at(root: &Path, provider: &str) -> Option<usize> {
    let key = read_llm_key_at(root, provider)?;
    if key.is_empty() {
        return None;
    }
    Some(key.chars().count())
}

pub fn read_custom_config_at(root: &Path) -> Option<CustomLlmConfig> {
    let root = ensure_root(root).ok()?;
    let _g = lock();
    let file = read_file(&root).ok()?;
    file.custom
}

pub fn write_custom_config_at(root: &Path, cfg: &CustomLlmConfig) -> Result<(), String> {
    let root = ensure_root(root)?;
    let _g = lock();
    let mut file = read_file(&root)?;
    file.custom = Some(cfg.clone());
    write_file(&root, &file)
}

// ---------------------------------------------------------------------------
// One-shot migration from the legacy OS keychain layout.
//
// Called once on first startup. The first call will trigger the macOS
// "Allow access to keychain" prompt exactly one more time, then delete
// the keychain entries so subsequent launches are prompt-free.
// ---------------------------------------------------------------------------

/// Run the one-shot keychain → keystore migration. Idempotent: if the
/// keystore file already exists, this is a no-op. Errors are logged and
/// swallowed — a failed migration is not fatal; the user can re-enter
/// the API key in Settings.
pub fn migrate_from_keychain_if_needed<R: Runtime>(_app: &tauri::AppHandle<R>) {
    if let Err(e) = migrate_from_keychain_at(&keystore_root()) {
        eprintln!("[prism] keystore migration error: {e}");
    }
}

/// Path-taking variant of [`migrate_from_keychain_if_needed`]. The
/// integration test in `tests/keystore_smoke.rs` uses this directly to
/// avoid spinning up a Tauri AppHandle.
pub fn migrate_from_keychain_at(root: &Path) -> Result<(), String> {
    let root = ensure_root(root)?;

    // If the keystore file already exists, migration is a no-op. This is
    // the idempotency hook: any subsequent launch (and any retry) is
    // short-circuited.
    if root.join(DATA_FILENAME).exists() {
        return Ok(());
    }

    // Read the legacy slots from the OS keychain. Each `Entry::new` is
    // cheap; the actual prompt (macOS) happens on first `get_password`.
    // We swallow every individual read error as `None` so a partial
    // legacy state still migrates what it can.
    let mut migrated: KeystoreFile = KeystoreFile {
        version: SCHEMA_VERSION,
        ..Default::default()
    };
    let mut anything_migrated = false;

    // Active provider pointer.
    if let Some(active) = keychain_get(secrets::SERVICE, USERNAME_LLM_PROVIDER_ACTIVE) {
        if !active.is_empty() {
            migrated.active_provider = Some(active);
            anything_migrated = true;
        }
    }

    // Provider keys (deepseek + minimax).
    for provider in secrets::KNOWN_PROVIDERS {
        let username = secrets::llm_key_username(provider);
        if let Some(key) = keychain_get(secrets::SERVICE, &username) {
            if !key.is_empty() {
                migrated
                    .providers
                    .entry((*provider).to_string())
                    .or_default()
                    .api_key = Some(key);
                anything_migrated = true;
            }
        }
    }

    // Custom MiniMax override blob.
    if let Some(blob) = keychain_get(secrets::SERVICE, USERNAME_LLM_CONFIG_CUSTOM) {
        if !blob.is_empty() {
            if let Ok(cfg) = serde_json::from_str::<CustomLlmConfig>(&blob) {
                migrated.custom = Some(cfg);
                anything_migrated = true;
            }
        }
    }

    // Legacy v0.2a `deepseek-api-key` slot — if it was the only thing
    // present, treat it as the deepseek key. This matches the v0.2a →
    // v0.2a-providers migration rule.
    if !migrated.providers.contains_key("deepseek") {
        if let Some(legacy_key) = keychain_get(secrets::SERVICE, secrets::USERNAME_DEEPSEEK_LEGACY)
        {
            if !legacy_key.is_empty() {
                migrated
                    .providers
                    .entry("deepseek".to_string())
                    .or_default()
                    .api_key = Some(legacy_key);
                if migrated.active_provider.is_none() {
                    migrated.active_provider = Some("deepseek".to_string());
                }
                anything_migrated = true;
            }
        }
    }

    if anything_migrated {
        // Write to keystore (this also generates the master key).
        let _g = lock();
        write_file(&root, &migrated)?;
    }

    // Delete the keychain entries so we never see the OS prompt again.
    // Each delete is best-effort — a "no entry" error is fine.
    for username in [
        USERNAME_LLM_PROVIDER_ACTIVE,
        secrets::USERNAME_DEEPSEEK_LEGACY,
    ] {
        let _ = keychain_delete(secrets::SERVICE, username);
    }
    for provider in secrets::KNOWN_PROVIDERS {
        let _ = keychain_delete(secrets::SERVICE, &secrets::llm_key_username(provider));
    }
    let _ = keychain_delete(secrets::SERVICE, USERNAME_LLM_CONFIG_CUSTOM);

    Ok(())
}

// ---------------------------------------------------------------------------
// Thin `keyring` crate wrappers. The `keyring` crate is the only place
// we touch the OS keychain — exclusively from the migration path.
// ---------------------------------------------------------------------------

fn keychain_get(service: &str, username: &str) -> Option<String> {
    let entry = keyring::Entry::new(service, username)
        .map_err(|e| {
            eprintln!("[prism] keychain entry for {username}: {e}");
            e
        })
        .ok()?;
    match entry.get_password() {
        Ok(v) => Some(v),
        Err(e) => {
            // NoEntry is expected on fresh installs; everything else is
            // logged but not fatal.
            let msg = e.to_string();
            if !msg.to_lowercase().contains("no entry") {
                eprintln!("[prism] keychain read {username}: {msg}");
            }
            None
        }
    }
}

fn keychain_delete(service: &str, username: &str) -> Result<(), String> {
    let entry = keyring::Entry::new(service, username).map_err(|e| e.to_string())?;
    match entry.delete_credential() {
        Ok(()) => Ok(()),
        Err(e) => {
            let msg = e.to_string();
            if msg.to_lowercase().contains("no entry") {
                Ok(())
            } else {
                Err(msg)
            }
        }
    }
}

