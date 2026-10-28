//! Backend-independent download orchestration.
//!
//! Browser operations and temporary PDF storage are provided by a backend.
use crate::engine::{BatchDecision, BatchStatus, BatchTracker};
use std::{path::{Path, PathBuf}, sync::atomic::{AtomicBool, Ordering}, thread, time::{Duration, Instant}};

#[derive(Debug, Clone)]
pub struct DownloadOptions {
    pub batch_size: usize,
    pub page_load_timeout: Duration,
    pub poll_interval: Duration,
}
impl Default for DownloadOptions {
    fn default() -> Self { Self { batch_size: 8, page_load_timeout: Duration::from_secs(120), poll_interval: Duration::from_millis(250) } }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Progress { pub stage: &'static str, pub current: usize, pub total: usize, pub message: String }

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DownloadError { Cancelled, InvalidOptions(&'static str), Backend(String), Timeout(String), InvalidPage { page: usize, sheets: usize } }

/// Backend implementations must keep each printed page in temporary storage,
/// validate/return its PDF sheet count, and only write output in `merge`.
/// `cleanup` must remove temporary storage and close any browser session; it
/// is called once even if another backend method returns an error.
pub trait DownloadBackend {
    fn open_and_prepare(&mut self, url: &str) -> Result<usize, String>;
    fn probe_batch(&mut self, pages: &[usize]) -> Result<BatchStatus, String>;
    fn print_page(&mut self, page: usize) -> Result<usize, String>;
    fn release_batch(&mut self, pages: &[usize]) -> Result<(), String>;
    fn merge(&mut self, output: &Path) -> Result<PathBuf, String>;
    fn cleanup(&mut self);
}

fn check_cancel(cancel: &AtomicBool) -> Result<(), DownloadError> {
    if cancel.load(Ordering::Relaxed) { Err(DownloadError::Cancelled) } else { Ok(()) }
}

/// Run one document in bounded, sequential page batches.
/// Chrome calls themselves can block: the backend must apply command timeouts.
pub fn download<B: DownloadBackend>(backend: &mut B, url: &str, output: &Path, options: &DownloadOptions, cancel: &AtomicBool, progress: &mut dyn FnMut(Progress)) -> Result<PathBuf, DownloadError> {
    if options.batch_size == 0 { return Err(DownloadError::InvalidOptions("batch size must be at least 1")); }
    if options.page_load_timeout.is_zero() || options.poll_interval.is_zero() { return Err(DownloadError::InvalidOptions("timeouts and poll interval must be positive")); }
    let result = download_inner(backend, url, output, options, cancel, progress);
    backend.cleanup();
    result
}

fn download_inner<B: DownloadBackend>(backend: &mut B, url: &str, output: &Path, options: &DownloadOptions, cancel: &AtomicBool, progress: &mut dyn FnMut(Progress)) -> Result<PathBuf, DownloadError> {
    check_cancel(cancel)?;
    progress(Progress { stage: "connect", current: 0, total: 0, message: "Starting Chrome…".into() });
    let total = backend.open_and_prepare(url).map_err(DownloadError::Backend)?;
    if total == 0 { return Err(DownloadError::Backend("No printable document pages found.".into())); }
    let mut first = 1;
    while first <= total {
        check_cancel(cancel)?;
        let last = first + (total - first).min(options.batch_size - 1);
        let pages: Vec<usize> = (first..=last).collect();
        let started = Instant::now();
        let mut tracker = BatchTracker::new(options.page_load_timeout);
        loop {
            match tracker.before_probe(started.elapsed(), cancel.load(Ordering::Relaxed)) {
                BatchDecision::Cancelled => return Err(DownloadError::Cancelled),
                BatchDecision::TimedOut(details) => return Err(DownloadError::Timeout(details)),
                _ => {}
            }
            let decision = match backend.probe_batch(&pages) {
                Ok(status) => {
                    progress(Progress { stage: "load", current: first - 1, total, message: format!("Loading pages {first}-{last}: {}/{} ready", status.ready, status.total) });
                    tracker.observe(status)
                }
                Err(error) => tracker.browser_error(&error),
            };
            match decision {
                BatchDecision::Ready => break,
                BatchDecision::Unsupported => return Err(DownloadError::Backend("Scribd direct page loader is unavailable.".into())),
                BatchDecision::BrowserStopped(error) => return Err(DownloadError::Backend(error)),
                _ => {}
            }
            let remaining = options.page_load_timeout.saturating_sub(started.elapsed());
            thread::sleep(options.poll_interval.min(remaining));
        }
        for &page in &pages {
            check_cancel(cancel)?;
            progress(Progress { stage: "export", current: page - 1, total, message: format!("Printing page {page} of {total}…") });
            let sheets = backend.print_page(page).map_err(DownloadError::Backend)?;
            if sheets != 1 { return Err(DownloadError::InvalidPage { page, sheets }); }
            progress(Progress { stage: "export", current: page, total, message: format!("Page {page} of {total}") });
        }
        backend.release_batch(&pages).map_err(DownloadError::Backend)?;
        if last == total { break; }
        first = last + 1;
    }
    check_cancel(cancel)?;
    progress(Progress { stage: "merge", current: total, total, message: format!("Merging {total} pages into one PDF…") });
    let result = backend.merge(output).map_err(DownloadError::Backend)?;
    progress(Progress { stage: "done", current: total, total, message: "PDF saved successfully.".into() });
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::BatchStatus;
    #[derive(Default)]
    struct Fake { actions: Vec<String>, invalid: bool, fail_open: bool, fatal: bool, pending: bool }
    impl DownloadBackend for Fake {
        fn open_and_prepare(&mut self, _: &str) -> Result<usize, String> { self.actions.push("open".into()); if self.fail_open { Err("open failed".into()) } else { Ok(3) } }
        fn probe_batch(&mut self, pages: &[usize]) -> Result<BatchStatus, String> { self.actions.push(format!("probe {pages:?}")); if self.fatal { return Err("invalid session id".into()); } Ok(BatchStatus { supported: true, total: pages.len(), ready: if self.pending { 0 } else { pages.len() }, total_images: 0, pending_images: 0, pages: vec![] }) }
        fn print_page(&mut self, page: usize) -> Result<usize, String> { self.actions.push(format!("print {page}")); Ok(if self.invalid { 2 } else { 1 }) }
        fn release_batch(&mut self, pages: &[usize]) -> Result<(), String> { self.actions.push(format!("release {pages:?}")); Ok(()) }
        fn merge(&mut self, path: &Path) -> Result<PathBuf, String> { self.actions.push("merge".into()); Ok(path.into()) }
        fn cleanup(&mut self) { self.actions.push("cleanup".into()); }
    }
    fn options() -> DownloadOptions { DownloadOptions { batch_size: 2, page_load_timeout: Duration::from_millis(5), poll_interval: Duration::from_millis(1) } }
    #[test]
    fn runs_batches_then_merges_and_cleans_up() {
        let mut fake = Fake::default(); let mut progress = vec![];
        let result = download(&mut fake, "url", Path::new("out.pdf"), &options(), &AtomicBool::new(false), &mut |p| progress.push(p.stage));
        assert_eq!(result, Ok(PathBuf::from("out.pdf")));
        assert_eq!(fake.actions, ["open", "probe [1, 2]", "print 1", "print 2", "release [1, 2]", "probe [3]", "print 3", "release [3]", "merge", "cleanup"]);
        assert_eq!(progress.last(), Some(&"done"));
    }
    #[test]
    fn failures_and_cancellation_never_merge_and_always_cleanup() {
        for mut fake in [Fake { invalid: true, ..Default::default() }, Fake { fail_open: true, ..Default::default() }, Fake { fatal: true, ..Default::default() }, Fake { pending: true, ..Default::default() }] {
            assert!(download(&mut fake, "url", Path::new("out.pdf"), &options(), &AtomicBool::new(false), &mut |_| {}).is_err());
            assert_eq!(fake.actions.last().unwrap(), "cleanup");
            assert!(!fake.actions.contains(&"merge".into()));
        }
        let mut fake = Fake::default();
        assert_eq!(download(&mut fake, "url", Path::new("out.pdf"), &options(), &AtomicBool::new(true), &mut |_| {}), Err(DownloadError::Cancelled));
        assert_eq!(fake.actions, ["cleanup"]);
    }
    #[test]
    fn cancellation_between_pages_does_not_merge() {
        let cancel = AtomicBool::new(false); let mut fake = Fake::default();
        let result = download(&mut fake, "url", Path::new("out.pdf"), &options(), &cancel, &mut |p| { if p.stage == "export" && p.current == 1 { cancel.store(true, Ordering::Relaxed); } });
        assert_eq!(result, Err(DownloadError::Cancelled));
        assert!(!fake.actions.contains(&"print 2".into()));
        assert_eq!(fake.actions.last().unwrap(), "cleanup");
    }
}
