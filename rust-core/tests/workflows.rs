//! Public API tests: no browser, network, or GUI required.
use scribd_core::{
    download::{download, DownloadBackend, DownloadError, DownloadOptions, Progress},
    engine::BatchStatus,
    extract_scribd_urls, parse_scribd_url, sanitize_filename,
    output::{choose_output, ConflictPolicy, OutputDecision},
    queue::{dedupe_infos, queue_title_text, QueueItem, QueueStatus},
};
use std::{path::{Path, PathBuf}, sync::atomic::{AtomicBool, Ordering}, time::Duration};

#[test]
fn url_to_queue_handles_multiple_forms_and_duplicates() {
    let input = "https://de.scribd.com/document/10/First-Doc?x=1\nhttps://www.scribd.com/doc/20/Other\nhttps://www.scribd.com/embeds/10/content\nhttps://www.scribd.com/embeds/30/content";
    let docs = extract_scribd_urls(input);
    assert_eq!(docs.iter().map(|d| d.document_id.as_str()).collect::<Vec<_>>(), ["10", "20", "30"]);
    let fresh = dedupe_infos(&docs, &["20"]);
    let queue: Vec<QueueItem> = fresh.iter().map(|d| QueueItem::from(*d)).collect();
    assert_eq!(queue[0].title, "First Doc");
    assert_eq!(queue[1].filename, "scribd-30.pdf");
    assert_eq!(queue_title_text(&queue), "Queue  (2)  ·  2 waiting");
    assert!(parse_scribd_url("example.org/document/99").is_none());
}

#[test]
fn filenames_handle_reserved_and_forbidden_characters() {
    for name in ["con", "NUL", "Com1", "lpt9"] {
        assert!(sanitize_filename(name, "fallback").ends_with('_'));
    }
    assert_eq!(sanitize_filename("a/b:*?.pdf", "fallback"), "a-b---");
    assert_eq!(sanitize_filename(" ... ", "fallback"), "fallback");
    assert_eq!(sanitize_filename(&"é".repeat(180), "fallback").chars().count(), 150);
}

#[test]
fn file_conflict_policies_do_not_modify_existing_file() {
    let dir = std::env::temp_dir().join(format!("scribd-policy-integration-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("doc.pdf");
    std::fs::write(&path, b"original").unwrap();
    assert!(matches!(choose_output(&path, ConflictPolicy::Skip), OutputDecision::Skip { .. }));
    assert!(matches!(choose_output(&path, ConflictPolicy::Overwrite), OutputDecision::Download { existed_before: true, .. }));
    let copy = choose_output(&path, ConflictPolicy::Copy);
    assert!(matches!(copy, OutputDecision::Download { path: ref p, .. } if p.ends_with("doc (1).pdf")));
    assert_eq!(std::fs::read(&path).unwrap(), b"original");
    std::fs::remove_dir_all(dir).unwrap();
}

#[derive(Default)]
struct FakeBackend {
    actions: Vec<String>,
    fail_release: bool,
    fail_merge: bool,
    page_sheets: usize,
}
impl DownloadBackend for FakeBackend {
    fn open_and_prepare(&mut self, _: &str) -> Result<usize, String> { self.actions.push("open".into()); Ok(3) }
    fn probe_batch(&mut self, pages: &[usize]) -> Result<BatchStatus, String> {
        self.actions.push(format!("probe {pages:?}"));
        Ok(BatchStatus { supported: true, total: pages.len(), ready: pages.len(), total_images: 0, pending_images: 0, pages: vec![] })
    }
    fn print_page(&mut self, page: usize) -> Result<usize, String> { self.actions.push(format!("page {page}")); Ok(self.page_sheets) }
    fn release_batch(&mut self, _: &[usize]) -> Result<(), String> { self.actions.push("release".into()); if self.fail_release { Err("release error".into()) } else { Ok(()) } }
    fn merge(&mut self, path: &Path) -> Result<PathBuf, String> { self.actions.push("merge".into()); if self.fail_merge { Err("merge error".into()) } else { Ok(path.to_owned()) } }
    fn cleanup(&mut self) { self.actions.push("cleanup".into()); }
}
fn options() -> DownloadOptions { DownloadOptions { batch_size: 2, page_load_timeout: Duration::from_secs(1), poll_interval: Duration::from_millis(1) } }

#[test]
fn successful_download_is_sequential_and_emits_final_progress() {
    let mut fake = FakeBackend { page_sheets: 1, ..Default::default() };
    let mut events: Vec<Progress> = vec![];
    let result = download(&mut fake, "url", Path::new("result.pdf"), &options(), &AtomicBool::new(false), &mut |p| events.push(p));
    assert_eq!(result.unwrap(), PathBuf::from("result.pdf"));
    assert_eq!(fake.actions, ["open", "probe [1, 2]", "page 1", "page 2", "release", "probe [3]", "page 3", "release", "merge", "cleanup"]);
    assert_eq!(events.last().unwrap().stage, "done");
    assert_eq!(events.last().unwrap().current, 3);
}

#[test]
fn release_or_merge_failure_always_cleans_up_and_never_reports_done() {
    for (release, merge) in [(true, false), (false, true)] {
        let mut fake = FakeBackend { page_sheets: 1, fail_release: release, fail_merge: merge, ..Default::default() };
        let mut events = vec![];
        assert!(matches!(download(&mut fake, "url", Path::new("out"), &options(), &AtomicBool::new(false), &mut |p| events.push(p.stage)), Err(DownloadError::Backend(_))));
        assert_eq!(fake.actions.last().unwrap(), "cleanup");
        assert!(!events.contains(&"done"));
        if release { assert!(!fake.actions.contains(&"merge".to_owned())); }
    }
}

#[test]
fn invalid_page_or_cancellation_never_merges() {
    let mut bad = FakeBackend { page_sheets: 2, ..Default::default() };
    assert_eq!(download(&mut bad, "url", Path::new("out"), &options(), &AtomicBool::new(false), &mut |_| {}), Err(DownloadError::InvalidPage { page: 1, sheets: 2 }));
    assert!(!bad.actions.contains(&"merge".to_owned()));
    let mut fake = FakeBackend { page_sheets: 1, ..Default::default() };
    let cancel = AtomicBool::new(false);
    let result = download(&mut fake, "url", Path::new("out"), &options(), &cancel, &mut |p| {
        if p.stage == "export" && p.current == 1 { cancel.store(true, Ordering::Relaxed); }
    });
    assert_eq!(result, Err(DownloadError::Cancelled));
    assert!(!fake.actions.contains(&"page 2".to_owned()));
    assert_eq!(fake.actions.last().unwrap(), "cleanup");
}

#[test]
fn queue_items_start_pending() {
    let doc = parse_scribd_url("scribd.com/doc/5/Test").unwrap();
    assert_eq!(QueueItem::from(&doc).status, QueueStatus::Pending);
}

#[test]
fn invalid_options_cleanup_backend_without_opening() {
    for invalid in [
        DownloadOptions { batch_size: 0, ..options() },
        DownloadOptions { page_load_timeout: Duration::ZERO, ..options() },
        DownloadOptions { poll_interval: Duration::ZERO, ..options() },
    ] {
        let mut fake = FakeBackend::default();
        let result = download(&mut fake, "url", Path::new("out"), &invalid, &AtomicBool::new(false), &mut |_| {});
        assert!(matches!(result, Err(DownloadError::InvalidOptions(_))));
        assert_eq!(fake.actions, ["cleanup"]);
    }
}

#[test]
fn cancellation_just_before_merge_leaves_output_untouched() {
    let cancel = AtomicBool::new(false);
    let mut fake = FakeBackend { page_sheets: 1, ..Default::default() };
    let result = download(&mut fake, "url", Path::new("out"), &options(), &cancel, &mut |p| {
        if p.stage == "export" && p.current == p.total { cancel.store(true, Ordering::Relaxed); }
    });
    assert_eq!(result, Err(DownloadError::Cancelled));
    assert!(!fake.actions.contains(&"merge".to_owned()));
    assert_eq!(fake.actions.last().unwrap(), "cleanup");
}

#[test]
fn unsupported_page_loader_fails_cleanly() {
    struct Unsupported { cleanup_called: bool }
    impl DownloadBackend for Unsupported {
        fn open_and_prepare(&mut self, _: &str) -> Result<usize, String> { Ok(1) }
        fn probe_batch(&mut self, _: &[usize]) -> Result<BatchStatus, String> {
            Ok(BatchStatus { supported: false, total: 1, ready: 0, total_images: 0, pending_images: 0, pages: vec![] })
        }
        fn print_page(&mut self, _: usize) -> Result<usize, String> { panic!("must not print") }
        fn release_batch(&mut self, _: &[usize]) -> Result<(), String> { panic!("must not release") }
        fn merge(&mut self, _: &Path) -> Result<PathBuf, String> { panic!("must not merge") }
        fn cleanup(&mut self) { self.cleanup_called = true; }
    }
    let mut fake = Unsupported { cleanup_called: false };
    let result = download(&mut fake, "url", Path::new("out"), &options(), &AtomicBool::new(false), &mut |_| {});
    assert_eq!(result, Err(DownloadError::Backend("Scribd direct page loader is unavailable.".into())));
    assert!(fake.cleanup_called);
}
