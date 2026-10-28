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
