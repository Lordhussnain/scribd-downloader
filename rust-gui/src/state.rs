//! Persistent GUI preferences and unfinished queue items.
use serde::{Deserialize, Serialize};
use std::{io::Write, path::{Path, PathBuf}};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Settings {
    pub output_dir: String,
    pub appearance: String,
    pub batch_size: u32,
    pub page_load_timeout: u32,
    pub export_timeout: u32,
    pub show_browser: bool,
    pub show_driver_console: bool,
    pub open_when_done: bool,
    pub clipboard_check: bool,
    pub conflict_policy: String,
    pub pending_queue: Vec<Pending>,
    pub recents: Vec<Recent>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Pending { pub source_url: String, pub filename: String }
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Recent { pub path: String, pub title: String }

impl Default for Settings {
    fn default() -> Self {
        let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
        let downloads = home.join("Downloads");
        Self { output_dir: if downloads.is_dir() { downloads } else { home }.to_string_lossy().into_owned(), appearance: "System".into(), batch_size: 8, page_load_timeout: 120, export_timeout: 600, show_browser: false, show_driver_console: false, open_when_done: false, clipboard_check: true, conflict_policy: "copy".into(), pending_queue: Vec::new(), recents: Vec::new() }
    }
}
impl Settings {
    pub fn normalize(&mut self) {
        if self.output_dir.is_empty() { self.output_dir = Self::default().output_dir; }
        if !["System", "Light", "Dark"].contains(&self.appearance.as_str()) { self.appearance = "System".into(); }
        if !["copy", "skip", "overwrite"].contains(&self.conflict_policy.as_str()) { self.conflict_policy = "copy".into(); }
        self.batch_size = *[4, 8, 16].iter().min_by_key(|n| (**n as i64 - self.batch_size as i64).abs()).unwrap();
        self.page_load_timeout = self.page_load_timeout.clamp(10, 600);
        self.export_timeout = self.export_timeout.clamp(30, 3600);
        self.recents.truncate(5);
    }
    pub fn load(path: &Path) -> Self {
        let mut settings = std::fs::read(path).ok().and_then(|data| serde_json::from_slice::<Self>(&data).ok()).unwrap_or_default();
        settings.normalize(); settings
    }
    /// Save via a temporary file in the same directory to avoid partial JSON.
    pub fn save(&self, path: &Path) -> Result<(), String> {
        let parent = path.parent().ok_or("Invalid settings path")?;
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
        serde_json::to_writer_pretty(file.as_file_mut(), self).map_err(|e| e.to_string())?;
        file.flush().map_err(|e| e.to_string())?;
        file.persist(path).map_err(|e| e.to_string())?;
        Ok(())
    }
}
pub fn config_dir() -> PathBuf {
    if cfg!(windows) {
        std::env::var_os("APPDATA").or_else(|| std::env::var_os("USERPROFILE")).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
    } else {
        std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).or_else(|| std::env::var_os("HOME").map(|p| PathBuf::from(p).join(".config"))).unwrap_or_else(|| PathBuf::from("."))
    }.join("ScribdDownloader")
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn roundtrip_and_normalize() {
        let dir = tempfile::tempdir().unwrap(); let path = dir.path().join("settings.json");
        let mut s = Settings::default(); s.batch_size = 12; s.page_load_timeout = 5; s.pending_queue.push(Pending { source_url: "https://www.scribd.com/document/1/A".into(), filename: "A.pdf".into() });
        s.save(&path).unwrap(); let restored = Settings::load(&path);
        assert_eq!(restored.batch_size, 8); assert_eq!(restored.page_load_timeout, 10); assert_eq!(restored.pending_queue, s.pending_queue);
    }
    #[test]
    fn corrupt_file_uses_defaults() {
        let dir = tempfile::tempdir().unwrap(); let path = dir.path().join("settings.json"); std::fs::write(&path, "{").unwrap();
        assert_eq!(Settings::load(&path), Settings::default());
    }
    #[test]
    fn invalid_values_are_normalized_and_recents_are_bounded() {
        let mut s = Settings::default();
        s.appearance = "unexpected".into();
        s.conflict_policy = "bad".into();
        s.output_dir.clear();
        s.batch_size = 99;
        s.page_load_timeout = 999;
        s.export_timeout = 1;
        s.recents = (0..8).map(|i| Recent { path: format!("{i}.pdf"), title: i.to_string() }).collect();
        s.normalize();
        assert_eq!(s.appearance, "System");
        assert_eq!(s.conflict_policy, "copy");
        assert!(!s.output_dir.is_empty());
        assert_eq!(s.batch_size, 16);
        assert_eq!(s.page_load_timeout, 600);
        assert_eq!(s.export_timeout, 30);
        assert_eq!(s.recents.len(), 5);
    }
    #[test]
    fn settings_replace_existing_file_without_losing_data() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nested").join("settings.json");
        let mut settings = Settings::default();
        settings.save(&path).unwrap();
        settings.appearance = "Dark".into();
        settings.conflict_policy = "skip".into();
        settings.open_when_done = true;
        settings.save(&path).unwrap();
        let restored = Settings::load(&path);
        assert_eq!(restored.appearance, "Dark");
        assert_eq!(restored.conflict_policy, "skip");
        assert!(restored.open_when_done);
    }
    #[test]
    fn missing_and_partial_settings_are_supported() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("settings.json");
        assert_eq!(Settings::load(&path), Settings::default());
        std::fs::write(&path, r#"{ "appearance": "Light" }"#).unwrap();
        let restored = Settings::load(&path);
        assert_eq!(restored.appearance, "Light");
        assert_eq!(restored.batch_size, 8);
        assert!(restored.pending_queue.is_empty());
    }

    #[test]
    fn driver_console_setting_defaults_off_and_persists() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("settings.json");
        // Older settings files have no console preference.
        std::fs::write(&path, r#"{ "show_browser": true }"#).unwrap();
        let mut settings = Settings::load(&path);
        assert!(settings.show_browser);
        assert!(!settings.show_driver_console);
        settings.show_driver_console = true;
        settings.save(&path).unwrap();
        let restored = Settings::load(&path);
        assert!(restored.show_browser);
        assert!(restored.show_driver_console);
    }

}
