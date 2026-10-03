use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};

fn config_path() -> PathBuf {
    option_env!("EMAKI_DATADIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(option_env!("EMAKI_PREFIX").unwrap_or("/usr")).join("share/emaki")
        })
        .join("fetch/config.jsonc")
}

#[test]
fn bare_emaki_execs_fastfetch_with_the_packaged_config() {
    let directory = std::env::temp_dir().join(format!("emaki-fetch-test-{}", std::process::id()));
    fs::create_dir(&directory).unwrap();
    let fake = directory.join("fastfetch");
    fs::write(
        &fake,
        "#!/bin/sh\nprintf '%s\\n' \"$$\" \"$#\" \"$@\"\nexit 37\n",
    )
    .unwrap();
    fs::set_permissions(&fake, fs::Permissions::from_mode(0o755)).unwrap();
    let child = Command::new(env!("CARGO_BIN_EXE_emaki"))
        .env_clear()
        .env("PATH", &directory)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let pid = child.id();
    let output = child.wait_with_output().unwrap();
    fs::remove_dir_all(directory).unwrap();
    assert_eq!(output.status.code(), Some(37));
    assert!(output.stderr.is_empty());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap(),
        format!("{pid}\n2\n--config\n{}\n", config_path().display())
    );
}

#[test]
fn missing_fastfetch_prints_one_diagnostic_and_the_unchanged_help() {
    let output = Command::new(env!("CARGO_BIN_EXE_emaki"))
        .env_clear()
        .env("PATH", "/nonexistent-emaki-fetch-test")
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stdout.is_empty());
    assert_eq!(
        String::from_utf8(output.stderr).unwrap(),
        format!(
            "emaki: fastfetch is not installed or is not on PATH.\n\n{}",
            include_str!("fixtures/help.txt")
        )
    );
    for arg in ["help", "--help", "-h"] {
        let output = Command::new(env!("CARGO_BIN_EXE_emaki"))
            .arg(arg)
            .env_clear()
            .env("PATH", "/nonexistent-emaki-fetch-test")
            .output()
            .unwrap();
        assert!(output.status.success());
        assert!(output.stderr.is_empty());
        assert_eq!(output.stdout, include_bytes!("fixtures/help.txt"));
    }
}
