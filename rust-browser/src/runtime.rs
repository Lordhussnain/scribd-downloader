//! Locate browser components shipped with Windows packages.
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BundledRuntime {
    /// None in the driver-only package: Chrome must be installed separately.
    pub chrome: Option<PathBuf>,
    pub driver: PathBuf,
}

/// Resolve runtime next to the executable. A mode marker distinguishes an
/// intentional driver-only bundle from a broken full bundle. Older full
/// bundles without a marker remain compatible.
pub fn bundled_runtime_at(app_dir: &Path) -> Result<Option<BundledRuntime>, String> {
    let root = app_dir.join("runtime");
    if !root.exists() { return Ok(None); }
    let invalid = || format!("The bundled browser runtime is incomplete in {}. Reinstall the app or copy the entire portable folder.", root.display());
    let mode_file = root.join("package-mode.txt");
    let driver_only = if mode_file.exists() {
        let mode = std::fs::read_to_string(&mode_file).map_err(|_| invalid())?;
        match mode.trim() { "driver-only" => true, "full" => false, _ => return Err(invalid()) }
    } else { false };
    let chrome = root.join("chrome-win64").join("chrome.exe");
    let driver = root.join("chromedriver-win64").join("chromedriver.exe");
    if !driver.is_file() || (!driver_only && !chrome.is_file()) { return Err(invalid()); }
    Ok(Some(BundledRuntime { chrome: if driver_only { None } else { Some(chrome) }, driver }))
}

pub fn bundled_runtime() -> Result<Option<BundledRuntime>, String> {
    let exe = std::env::current_exe().map_err(|e| format!("Cannot locate the application executable: {e}"))?;
    let app_dir = exe.parent().ok_or("Cannot locate the application directory")?;
    bundled_runtime_at(app_dir)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn detects_full_and_driver_only_and_rejects_corrupt_packages() {
        let temp = tempfile::tempdir().unwrap();
        let app = temp.path();
        assert_eq!(bundled_runtime_at(app).unwrap(), None);
        let root = app.join("runtime");
        std::fs::create_dir_all(root.join("chrome-win64")).unwrap();
        std::fs::create_dir_all(root.join("chromedriver-win64")).unwrap();
        std::fs::write(root.join("chrome-win64/chrome.exe"), b"fake").unwrap();
        assert!(bundled_runtime_at(app).unwrap_err().contains("incomplete"));
        std::fs::write(root.join("chromedriver-win64/chromedriver.exe"), b"fake").unwrap();
        let pair = bundled_runtime_at(app).unwrap().unwrap();
        assert_eq!(pair.chrome, Some(root.join("chrome-win64/chrome.exe")));
        assert_eq!(pair.driver, root.join("chromedriver-win64/chromedriver.exe"));
        std::fs::write(root.join("package-mode.txt"), "driver-only\n").unwrap();
        std::fs::remove_file(root.join("chrome-win64/chrome.exe")).unwrap();
        assert!(bundled_runtime_at(app).unwrap().unwrap().chrome.is_none());
        std::fs::write(root.join("package-mode.txt"), "full\n").unwrap();
        assert!(bundled_runtime_at(app).is_err());
        std::fs::write(root.join("package-mode.txt"), "invalid\n").unwrap();
        assert!(bundled_runtime_at(app).is_err());
        std::fs::write(root.join("package-mode.txt"), "driver-only\n").unwrap();
        std::fs::remove_file(root.join("chromedriver-win64/chromedriver.exe")).unwrap();
        assert!(bundled_runtime_at(app).is_err());
    }
}
