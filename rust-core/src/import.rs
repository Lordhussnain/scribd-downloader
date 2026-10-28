//! Link-file and drag-and-drop parsing, independent of a GUI toolkit.
use crate::{extract_scribd_urls, ScribdDocument};
use std::collections::HashSet;
use std::io::Read;
use std::path::{Path, PathBuf};

pub const MAX_LINK_FILE_BYTES: u64 = 2 * 1024 * 1024;

/// Split a tkdnd fallback file list, respecting nested brace-quoted paths.
pub fn split_drop_files(data: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut depth = 0usize;
    for c in data.chars() {
        match c {
            '{' => { depth += 1; if depth > 1 { current.push(c); } }
            '}' => { depth = depth.saturating_sub(1); if depth > 0 { current.push(c); } }
            c if c.is_whitespace() && depth == 0 => {
                if !current.is_empty() { parts.push(std::mem::take(&mut current)); }
            }
            c => current.push(c),
        }
    }
    if !current.is_empty() { parts.push(current); }
    parts.into_iter().map(|s| s.trim().to_owned()).filter(|s| !s.is_empty()).collect()
}

fn supported_file(path: &Path) -> bool {
    path.extension().and_then(|s| s.to_str()).map(|ext| {
        ["txt", "text", "md", "csv", "url", "html", "htm"].iter().any(|allowed| ext.eq_ignore_ascii_case(allowed))
    }).unwrap_or(false)
}

// Like Python's UTF-8 errors="ignore": discard invalid sequences rather than
// inserting replacement characters (which could change an extracted URL).
fn utf8_ignoring_errors(bytes: &[u8]) -> String {
    let mut text = String::new();
    let mut rest = bytes;
    while !rest.is_empty() {
        match std::str::from_utf8(rest) {
            Ok(valid) => { text.push_str(valid); break; }
            Err(error) => {
                let valid = error.valid_up_to();
                text.push_str(std::str::from_utf8(&rest[..valid]).expect("validated prefix"));
                match error.error_len() {
                    Some(length) => rest = &rest[valid + length..],
                    None => break,
                }
            }
        }
    }
    text
}

fn append_unique(found: &mut Vec<ScribdDocument>, seen: &mut HashSet<String>, docs: Vec<ScribdDocument>) {
    for doc in docs {
        if seen.insert(doc.document_id.clone()) { found.push(doc); }
    }
}

/// Read supported files up to 2 MiB, ignoring unreadable or oversized files.
/// The limit is enforced during reading too, in case a file grows after stat.
pub fn read_urls_from_files(paths: &[PathBuf]) -> Vec<ScribdDocument> {
    let mut found = Vec::new();
    let mut seen = HashSet::new();
    for path in paths {
        if !supported_file(path) { continue; }
        let Ok(file) = std::fs::File::open(path) else { continue };
        let Ok(metadata) = file.metadata() else { continue };
        if !metadata.is_file() || metadata.len() > MAX_LINK_FILE_BYTES { continue; }
        let mut bytes = Vec::new();
        if file.take(MAX_LINK_FILE_BYTES + 1).read_to_end(&mut bytes).is_err() || bytes.len() as u64 > MAX_LINK_FILE_BYTES { continue; }
        append_unique(&mut found, &mut seen, extract_scribd_urls(&utf8_ignoring_errors(&bytes)));
    }
    found
}

/// Parse pasted/dropped text and fallback tkdnd paths in first-seen order.
/// GUI integrations that have Tk's splitlist should pass those paths directly
/// to `read_urls_from_files`, since Tcl escaping is richer than this fallback.
pub fn parse_drop_data(data: &str) -> Vec<ScribdDocument> {
    let mut found = Vec::new();
    let mut seen = HashSet::new();
    append_unique(&mut found, &mut seen, extract_scribd_urls(data));
    let paths: Vec<PathBuf> = split_drop_files(data).into_iter().map(PathBuf::from).collect();
    append_unique(&mut found, &mut seen, read_urls_from_files(&paths));
    found
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    static NEXT: AtomicUsize = AtomicUsize::new(0);
    struct TempDir(PathBuf);
    impl TempDir {
        fn new() -> Self {
            let dir = std::env::temp_dir().join(format!("scribd-import-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
            std::fs::create_dir(&dir).unwrap(); Self(dir)
        }
    }
    impl Drop for TempDir { fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); } }

    #[test]
    fn splits_braces_and_nested_paths() {
        assert_eq!(split_drop_files("{C:/My Docs/a.txt} C:/plain.txt {D:/a {b}/c.url}"), ["C:/My Docs/a.txt", "C:/plain.txt", "D:/a {b}/c.url"]);
        assert!(split_drop_files("  ").is_empty());
    }
    #[test]
    fn ignores_invalid_utf8_like_python() {
        assert_eq!(utf8_ignoring_errors(b"a\xffb\xc3\xa9\xe2\x82"), "abé");
    }
    #[test]
    fn imports_limits_extensions_and_deduplicates() {
        let dir = TempDir::new();
        let a = dir.0.join("my links.TXT");
        let b = dir.0.join("links.url");
        let ignored = dir.0.join("photo.png");
        let large = dir.0.join("large.txt");
        std::fs::write(&a, b"scribd.com/document/1/A\n\xffscribd.com/doc/2/B").unwrap();
        std::fs::write(&b, b"scribd.com/doc/1/duplicate scribd.com/doc/3/C").unwrap();
        std::fs::write(&ignored, b"scribd.com/doc/4/D").unwrap();
        let f = std::fs::File::create(&large).unwrap(); f.set_len(MAX_LINK_FILE_BYTES + 1).unwrap();
        let docs = read_urls_from_files(&[a.clone(), b, ignored, large, dir.0.join("missing.txt")]);
        assert_eq!(docs.iter().map(|d| d.document_id.as_str()).collect::<Vec<_>>(), ["1", "2", "3"]);
        let dropped = parse_drop_data(&format!("{{{}}}", a.display()));
        assert_eq!(dropped.iter().map(|d| d.document_id.as_str()).collect::<Vec<_>>(), ["1", "2"]);
    }
}
