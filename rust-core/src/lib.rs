//! URL and filename helpers ported from the Python download engine.
//!
//! This crate is not yet connected to the Python GUI or download engine.

pub mod download;
pub mod engine;
pub mod scripts;
pub mod import;
pub mod output;
pub mod queue;
pub mod settings;

use percent_encoding::percent_decode_str;
use regex::Regex;
use std::collections::HashSet;
use std::sync::OnceLock;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScribdDocument {
    pub document_id: String,
    pub title: String,
    pub embed_url: String,
    pub filename: String,
    pub source_url: String,
}

fn url_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        // Intentionally matches the existing Python helper, including finding
        // links inside pasted prose rather than requiring a complete URL.
        Regex::new(r"(?i)scribd\.com/(?:document|doc|embeds)/(\d+)(?:/([^/?#\s]+))?")
            .expect("valid Scribd URL pattern")
    })
}

/// Apply the same Windows filename rules as the Python engine.
/// The PDF extension is deliberately not appended.
pub fn sanitize_filename(name: &str, fallback: &str) -> String {
    let replaced: String = name.chars().map(|c| {
        if matches!(c, '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*') || c <= '\u{1f}' {
            '-'
        } else {
            c
        }
    }).collect();
    let collapsed = replaced.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut cleaned = collapsed.trim_end_matches(['.', ' ']).to_owned();
    if cleaned.to_lowercase().ends_with(".pdf") {
        // The suffix is ASCII, so the byte boundary is safe.
        cleaned.truncate(cleaned.len() - 4);
        cleaned = cleaned.trim_end_matches(['.', ' ']).to_owned();
    }
    if cleaned.is_empty() {
        cleaned = fallback.to_owned();
    }
    let upper = cleaned.to_uppercase();
    let reserved = matches!(upper.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        || (1..=9).any(|n| upper == format!("COM{n}") || upper == format!("LPT{n}"));
    if reserved {
        cleaned.push('_');
    }
    let shortened: String = cleaned.chars().take(150).collect();
    let result = shortened.trim_end_matches(['.', ' ']);
    if result.is_empty() { fallback.to_owned() } else { result.to_owned() }
}

fn from_captures(captures: &regex::Captures<'_>, extracting: bool) -> ScribdDocument {
    let id = captures.get(1).expect("document id").as_str();
    let raw_slug = captures.get(2).map_or("", |m| m.as_str());
    // Python's urllib.parse.unquote replaces invalid UTF-8 with U+FFFD.
    let decoded = percent_decode_str(raw_slug).decode_utf8_lossy();
    let slug = if decoded.eq_ignore_ascii_case("content") { "" } else { decoded.as_ref() };
    let title = slug.split(|c| c == '-' || c == '_')
        .filter(|part| !part.is_empty()).collect::<Vec<_>>().join(" ").trim().to_owned();
    let default_name = format!("scribd-{id}");
    let filename = format!("{}.pdf", sanitize_filename(if slug.is_empty() { &default_name } else { slug }, "scribd-document"));
    // extract_scribd_urls in Python tests the raw slug here; parse_scribd_url
    // tests the decoded slug. Preserve that distinction for compatibility.
    let include_slug = if extracting { !raw_slug.is_empty() && !raw_slug.eq_ignore_ascii_case("content") } else { !raw_slug.is_empty() && !slug.is_empty() };
    let source_url = format!("https://www.scribd.com/document/{id}{}", if include_slug { format!("/{raw_slug}") } else { String::new() });
    ScribdDocument {
        document_id: id.to_owned(), title,
        embed_url: format!("https://www.scribd.com/embeds/{id}/content"),
        filename, source_url,
    }
}

/// Find the first recognized document link in text.
pub fn parse_scribd_url(text: &str) -> Option<ScribdDocument> {
    url_pattern().captures(text).map(|caps| from_captures(&caps, false))
}

/// Find distinct document IDs in first-seen order.
pub fn extract_scribd_urls(text: &str) -> Vec<ScribdDocument> {
    let mut seen = HashSet::new();
    url_pattern().captures_iter(text).filter_map(|caps| {
        let document = from_captures(&caps, true);
        if seen.insert(document.document_id.clone()) { Some(document) } else { None }
    }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_document_legacy_embed_and_pasted_text() {
        let doc = parse_scribd_url("read https://de.scribd.com/document/123/My-File?x=1").unwrap();
        assert_eq!(doc.document_id, "123");
        assert_eq!(doc.title, "My File");
        assert_eq!(doc.filename, "My-File.pdf");
        assert_eq!(doc.source_url, "https://www.scribd.com/document/123/My-File");
        assert_eq!(doc.embed_url, "https://www.scribd.com/embeds/123/content");
        assert_eq!(parse_scribd_url("scribd.com/doc/42/Old-One").unwrap().document_id, "42");
        assert_eq!(parse_scribd_url("scribd.com/embeds/99/content").unwrap().filename, "scribd-99.pdf");
        assert!(parse_scribd_url("https://example.com/document/1/x").is_none());
    }

    #[test]
    fn extracts_in_order_and_deduplicates() {
        let docs = extract_scribd_urls("scribd.com/doc/1/A scribd.com/document/2/B scribd.com/embeds/1/content");
        assert_eq!(docs.iter().map(|d| d.document_id.as_str()).collect::<Vec<_>>(), vec!["1", "2"]);
        assert!(extract_scribd_urls("nothing").is_empty());
    }

    #[test]
    fn decodes_slugs_and_preserves_raw_source() {
        let doc = parse_scribd_url("scribd.com/document/4/Caf%C3%A9%3A-Notes").unwrap();
        assert_eq!(doc.title, "Café: Notes");
        assert_eq!(doc.filename, "Café--Notes.pdf");
        assert!(doc.source_url.ends_with("/Caf%C3%A9%3A-Notes"));
        assert_eq!(parse_scribd_url("scribd.com/document/4/%63ontent").unwrap().source_url, "https://www.scribd.com/document/4");
        assert!(extract_scribd_urls("scribd.com/document/4/%63ontent")[0].source_url.ends_with("/%63ontent"));
    }

    #[test]
    fn sanitizes_windows_filenames() {
        assert_eq!(sanitize_filename("a<b>:\"/\\|?*c", "fallback"), "a-b--------c");
        assert_eq!(sanitize_filename("CON", "fallback"), "CON_");
        assert_eq!(sanitize_filename("  name...  ", "fallback"), "name");
        assert_eq!(sanitize_filename("file.pdf", "fallback"), "file");
        assert_eq!(sanitize_filename("", "fallback"), "fallback");
        assert_eq!(sanitize_filename(&"é".repeat(151), "fallback").chars().count(), 150);
    }
}
