//! Manual smoke for the keychain flow.
//!
//! Run with: `cargo run --example dev_keychain_check`
//!
//! This bypasses Tauri entirely and exercises the same `keyring` crate the
//! `tauri-plugin-keyring` plugin is built on. Useful when you want to verify
//! the OS keychain is working without launching the full Tauri shell.
//!
//! On macOS, open Keychain Access and search for `com.prism.desktop.dev` to
//! see the entry this leaves behind.

use keyring::Entry;

fn main() {
    let service = "com.prism.desktop.dev";
    let user = "dev-keychain-check";
    let secret = format!("prism-smoke-{}", std::process::id());

    let entry = match Entry::new(service, user) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("could not create entry handle: {e}");
            std::process::exit(1);
        }
    };

    println!("[1/3] writing secret to keychain...");
    if let Err(e) = entry.set_password(&secret) {
        eprintln!("set_password failed: {e}");
        std::process::exit(1);
    }
    println!("    wrote secret (length {} chars)", secret.len());

    println!("[2/3] reading back...");
    let read_back = match entry.get_password() {
        Ok(s) => s,
        Err(e) => {
            eprintln!("get_password failed: {e}");
            std::process::exit(1);
        }
    };
    if read_back != secret {
        eprintln!("roundtrip MISMATCH! wrote {secret:?}, read {read_back:?}");
        std::process::exit(1);
    }
    println!("    read back matches");

    println!("[3/3] deleting...");
    if let Err(e) = entry.delete_credential() {
        eprintln!("delete_credential failed: {e}");
        std::process::exit(1);
    }
    println!("    deleted");

    println!("\nOK -- keychain roundtrip succeeded.");
    println!(
        "On macOS, Keychain Access should now show no entry for service={service}, user={user}."
    );
}
