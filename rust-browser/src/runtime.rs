//! Locate the paired browser and driver shipped with the Windows package.
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BundledRuntime {
    pub chrome: PathBuf,
    pub driver: PathBuf,
}

/// Locate the runtime relative to the application executable, not the
/// working directory. A partial package is an error, not a silent fallback to
/// a potentially incompatible system browser/driver pair.
pub fn bundled_runtime_at(app_dir: &Path) -> Result<Option<BundledRuntime>, String> {
    let root = app_dir.join("runtime");
    if !root.exists() { return Ok(None); }
    let chrome = root.join("chrome-win64").join("chrome.exe");
    let driver = root.join("chromedriver-win64").join("chromedriver.exe");
    if !chrome.is_file() || !driver.is_file() {
        return Err(format!("The bundled browser runtime is incomplete in {}. Reinstall the app or copy the entire portable folder.", root.display()));
    }
    Ok(Some(BundledRuntime { chrome, driver }))
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
    fn locates_complete_pair_and_rejects_partial_package() {
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
        assert_eq!(pair.chrome, root.join("chrome-win64/chrome.exe"));
        assert_eq!(pair.driver, root.join("chromedriver-win64/chromedriver.exe"));
    }
}
