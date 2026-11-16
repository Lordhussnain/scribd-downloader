//! GUI-independent queue data and display helpers. This does not download files.
use crate::ScribdDocument;
use std::collections::HashSet;
use std::path::PathBuf;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum QueueStatus { Pending, Downloading, Done, Failed, Cancelled, Skipped }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueueItem {
    pub source_url: String,
    pub document_id: String,
    pub title: String,
    pub filename: String,
    pub status: QueueStatus,
    pub output_path: Option<PathBuf>,
    pub percent: u32,
    pub activity: String,
    pub detail: String,
}

impl From<&ScribdDocument> for QueueItem {
    fn from(doc: &ScribdDocument) -> Self {
        Self {
            source_url: doc.source_url.clone(), document_id: doc.document_id.clone(),
            title: if doc.title.is_empty() { format!("Scribd document {}", doc.document_id) } else { doc.title.clone() },
            filename: doc.filename.clone(), status: QueueStatus::Pending,
            output_path: None, percent: 0, activity: String::new(), detail: String::new(),
        }
    }
}

/// Keep the first occurrence of each document ID, excluding already-known IDs.
pub fn dedupe_infos<'a>(infos: &'a [ScribdDocument], known_ids: &[&str]) -> Vec<&'a ScribdDocument> {
    let mut seen: HashSet<&str> = known_ids.iter().copied().collect();
    infos.iter().filter(|info| seen.insert(info.document_id.as_str())).collect()
}

/// Produce the same queue heading as the Python GUI.
pub fn queue_title_text(items: &[QueueItem]) -> String {
    let mut parts = Vec::new();
    let pending = items.iter().filter(|i| i.status == QueueStatus::Pending).count();
    let active = items.iter().any(|i| i.status == QueueStatus::Downloading);
    let done = items.iter().filter(|i| i.status == QueueStatus::Done).count();
    if active { parts.push("downloading".to_owned()); }
    if pending > 0 { parts.push(format!("{pending} waiting")); }
    if done > 0 { parts.push(format!("{done} saved")); }
    let mut title = format!("Queue  ({})", items.len());
    if !parts.is_empty() { title.push_str(&format!("  ·  {}", parts.join("  ·  "))); }
    title
}

/// Secondary queue text. File metadata is read only for completed items.
pub fn queue_item_subtext(item: &QueueItem) -> String {
    if item.status == QueueStatus::Failed && !item.detail.is_empty() { return item.detail.clone(); }
    if item.status == QueueStatus::Downloading {
        let activity = if item.activity.is_empty() { "Downloading…" } else { &item.activity };
        return format!("{activity}  ·  {}", item.filename);
    }
    if item.status == QueueStatus::Skipped { return format!("{}  ·  skipped (file already exists)", item.filename); }
    if item.status == QueueStatus::Done {
        if let Some(path) = &item.output_path {
            if let (Ok(metadata), Some(name)) = (path.metadata(), path.file_name()) {
                return format!("{}  ·  {}", name.to_string_lossy(), human_size(metadata.len()));
            }
        }
    }
    format!("{}  ·  ID {}", item.filename, item.document_id)
}

fn human_size(bytes: u64) -> String {
    let mut size = bytes as f64;
    for unit in ["B", "KB", "MB", "GB"] {
        if size < 1024.0 || unit == "GB" {
            return if unit == "B" { format!("{size:.0} {unit}") } else { format!("{size:.1} {unit}") };
        }
        size /= 1024.0;
    }
    unreachable!()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn doc(id: &str, title: &str) -> ScribdDocument {
        ScribdDocument { document_id: id.into(), title: title.into(), filename: format!("{id}.pdf"), source_url: format!("url/{id}"), embed_url: String::new() }
    }
    #[test]
    fn converts_and_deduplicates() {
        let docs = vec![doc("1", ""), doc("2", "Title"), doc("2", "duplicate"), doc("3", "Third")];
        let item = QueueItem::from(&docs[0]);
        assert_eq!(item.title, "Scribd document 1");
        assert_eq!(item.status, QueueStatus::Pending);
        let fresh = dedupe_infos(&docs, &["1"]);
        assert_eq!(fresh.iter().map(|i| i.document_id.as_str()).collect::<Vec<_>>(), ["2", "3"]);
    }
    #[test]
    fn title_and_row_statuses() {
        let mut items = vec![QueueItem::from(&doc("1", "")), QueueItem::from(&doc("2", "")), QueueItem::from(&doc("3", ""))];
        items[0].status = QueueStatus::Downloading;
        items[1].status = QueueStatus::Done;
        assert_eq!(queue_title_text(&items), "Queue  (3)  ·  downloading  ·  1 waiting  ·  1 saved");
        assert_eq!(queue_item_subtext(&items[0]), "Downloading…  ·  1.pdf");
        items[0].status = QueueStatus::Failed;
        items[0].detail = "Error".into();
        assert_eq!(queue_item_subtext(&items[0]), "Error");
        items[0].status = QueueStatus::Skipped;
        assert_eq!(queue_item_subtext(&items[0]), "1.pdf  ·  skipped (file already exists)");
        assert_eq!(queue_item_subtext(&items[2]), "3.pdf  ·  ID 3");
        assert_eq!(queue_title_text(&[]), "Queue  (0)");
    }
    #[test]
    fn completed_file_size_and_missing_file() {
        let path = std::env::temp_dir().join(format!("scribd-queue-test-{}", std::process::id()));
        std::fs::write(&path, vec![0; 1024]).unwrap();
        let mut item = QueueItem::from(&doc("1", ""));
        item.status = QueueStatus::Done;
        item.output_path = Some(path.clone());
        assert!(queue_item_subtext(&item).ends_with("  ·  1.0 KB"));
        std::fs::remove_file(path).unwrap();
        assert_eq!(queue_item_subtext(&item), "1.pdf  ·  ID 1");
    }
}
