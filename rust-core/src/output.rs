//! Destination conflict handling for sequential queue downloads.
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConflictPolicy { Copy, Skip, Overwrite }

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OutputDecision {
    Download { path: PathBuf, existed_before: bool },
    Skip { path: PathBuf },
}

/// Choose a destination, without opening or modifying any files.
/// This is not an atomic reservation: another process could create the
/// selected path before it is opened. The eventual writer must handle that.
pub fn choose_output(path: &Path, policy: ConflictPolicy) -> OutputDecision {
    let exists = path.exists();
    if exists && policy == ConflictPolicy::Skip {
        return OutputDecision::Skip { path: path.to_owned() };
    }
    let selected = if exists && policy == ConflictPolicy::Copy { unique_path(path) } else { path.to_owned() };
    OutputDecision::Download { path: selected, existed_before: exists }
}

/// Return the original path if available, otherwise append " (1)", etc.
pub fn unique_path(path: &Path) -> PathBuf {
    if !path.exists() { return path.to_owned(); }
    let stem = path.file_stem().unwrap_or_default();
    let extension = path.extension();
    for number in 1u64.. {
        let mut name = stem.to_os_string();
        name.push(format!(" ({number})"));
        if let Some(ext) = extension { name.push("."); name.push(ext); }
        let candidate = path.with_file_name(name);
        if !candidate.exists() { return candidate; }
    }
    unreachable!("no available numbered filename")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn applies_all_policies_and_finds_next_copy() {
        let dir = std::env::temp_dir().join(format!("scribd-output-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("my.file.pdf");
        let copy = dir.join("my.file (1).pdf");
        let next = dir.join("my.file (2).pdf");
        assert_eq!(choose_output(&path, ConflictPolicy::Skip), OutputDecision::Download { path: path.clone(), existed_before: false });
        std::fs::write(&path, b"original").unwrap();
        std::fs::write(&copy, b"copy").unwrap();
        assert_eq!(choose_output(&path, ConflictPolicy::Skip), OutputDecision::Skip { path: path.clone() });
        assert_eq!(choose_output(&path, ConflictPolicy::Overwrite), OutputDecision::Download { path: path.clone(), existed_before: true });
        assert_eq!(choose_output(&path, ConflictPolicy::Copy), OutputDecision::Download { path: next, existed_before: true });
        assert_eq!(std::fs::read(&path).unwrap(), b"original");
        std::fs::remove_dir_all(dir).unwrap();
    }
}
