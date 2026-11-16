//! Packaging contract checks; real Windows builds still need an end-to-end test.
use std::path::Path;

#[test]
fn windows_build_and_installer_reference_portable_runtime() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let launcher = std::fs::read_to_string(root.join("build_windows.bat")).unwrap();
    let script = std::fs::read_to_string(root.join("build_portable.ps1")).unwrap();
    let installer = std::fs::read_to_string(root.join("installer/ScribdDownloader.iss")).unwrap();
    assert!(launcher.contains("build_portable.ps1"));
    assert!(script.contains("x86_64-pc-windows-msvc"));
    assert!(script.contains("target-feature=+crt-static"));
    assert!(script.contains("chrome-win64\\chrome.exe"));
    assert!(script.contains("chromedriver-win64\\chromedriver.exe"));
    assert!(script.contains("$metadata.channels.Stable"));
    assert!(script.contains("$release.downloads.chrome"));
    assert!(script.contains("$release.downloads.chromedriver"));
    assert!(installer.contains("dist\\ScribdDownloader-Portable\\*"));
    assert!(installer.contains("recursesubdirs"));
}

#[test]
fn automated_release_is_weekly_and_excludes_pull_requests() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let workflow = std::fs::read_to_string(root.join(".github/workflows/windows-build.yml")).unwrap();
    assert!(workflow.contains("cron: '0 2 * * 0'"));
    assert!(workflow.contains("contents: write"));
    assert!(workflow.contains("github.event_name == 'schedule'"));
    assert!(workflow.contains("github.event_name == 'workflow_dispatch'"));
    assert!(workflow.contains("github.event.repository.default_branch"));
    assert!(workflow.contains("gh release create"));
    assert!(workflow.contains("--prerelease"));
    assert!(workflow.contains("ScribdDownloader-Windows-x64.zip"));
    assert!(workflow.contains("compression-level: 6"));
    assert!(workflow.contains("-CompressionLevel Optimal"));
}

#[test]
fn lightweight_variants_have_separate_weekly_release_workflows() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let driver = std::fs::read_to_string(root.join(".github/workflows/windows-driver-only.yml")).unwrap();
    let system = std::fs::read_to_string(root.join(".github/workflows/windows-system.yml")).unwrap();
    let common = std::fs::read_to_string(root.join(".github/workflows/windows-lightweight-reusable.yml")).unwrap();
    let script = std::fs::read_to_string(root.join("build_portable.ps1")).unwrap();
    assert!(driver.contains("cron: '15 2 * * 0'"));
    assert!(driver.contains("mode: DriverOnly"));
    assert!(system.contains("cron: '30 2 * * 0'"));
    assert!(system.contains("mode: System"));
    for workflow in [&driver, &system] {
        assert!(workflow.contains("uses: ./.github/workflows/windows-lightweight-reusable.yml"));
        assert!(workflow.contains("contents: write"));
    }
    assert!(common.contains("gh release create"));
    assert!(common.contains("github.event.repository.default_branch"));
    assert!(common.contains("github.event_name == 'schedule'"));
    assert!(common.contains("github.event_name == 'workflow_dispatch'"));
    assert!(common.contains("--prerelease"));
    assert!(script.contains("[ValidateSet('Full', 'DriverOnly', 'System')]"));
    assert!(script.contains("if ($Mode -eq 'System')"));
    assert!(script.contains("if ($Mode -eq 'Full')"));
    assert!(script.contains("'driver-only' | Set-Content"));
}
