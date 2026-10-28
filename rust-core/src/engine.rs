//! Browser-independent parts of the Rust download engine.
//!
//! These types implement page-count stabilization and batch readiness rules.
//! They do not launch Chrome, print pages, or create PDFs yet.
use std::time::Duration;

/// Track the initial Scribd page count until it is sufficiently stable.
#[derive(Default, Debug)]
pub struct PageCountTracker {
    last_count: usize,
    stable_polls: usize,
}

impl PageCountTracker {
    /// Return the count once the manager agrees or the DOM count repeats twice.
    pub fn observe(&mut self, outer_pages: usize, manager_pages: usize) -> Option<usize> {
        if outer_pages > 0 && outer_pages == self.last_count {
            self.stable_polls += 1;
        } else {
            self.last_count = outer_pages;
            self.stable_polls = 0;
        }
        if outer_pages > 0 && (self.stable_polls >= 2 || manager_pages == outer_pages) {
            Some(outer_pages)
        } else {
            None
        }
    }
}

/// Create bounded, one-based batches of page numbers, as used by the engine.
pub fn page_batches(page_count: usize, batch_size: usize) -> Result<Vec<Vec<usize>>, &'static str> {
    if batch_size == 0 { return Err("batch size must be at least 1"); }
    // Avoid allocating for more pages than the document contains, and avoid
    // overflow when callers supply very large batch sizes.
    let mut result = Vec::new();
    let mut next = 1usize;
    while next <= page_count {
        let remaining = page_count - next;
        let end = next + remaining.min(batch_size - 1);
        result.push((next..=end).collect());
        if end == page_count { break; }
        next = end + 1;
    }
    Ok(result)
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PageStatus {
    pub number: usize,
    pub ready: bool,
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BatchStatus {
    pub supported: bool,
    pub total: usize,
    pub ready: usize,
    pub total_images: usize,
    pub pending_images: usize,
    pub pages: Vec<PageStatus>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum BatchDecision {
    Pending,
    Ready,
    Unsupported,
    BrowserStopped(String),
    TimedOut(String),
    Cancelled,
}

/// Deterministic state machine for bounded polling of a page batch.
/// The caller owns the clock and performs cancellation checks between probes.
pub struct BatchTracker {
    timeout: Duration,
    stable_ready_polls: usize,
    last_status: Option<BatchStatus>,
    last_error: Option<String>,
}

impl BatchTracker {
    pub fn new(timeout: Duration) -> Self {
        Self { timeout, stable_ready_polls: 0, last_status: None, last_error: None }
    }

    /// Check cancellation/deadline *before* starting the next browser probe.
    pub fn before_probe(&self, elapsed: Duration, cancelled: bool) -> BatchDecision {
        if cancelled { return BatchDecision::Cancelled; }
        if elapsed >= self.timeout { return BatchDecision::TimedOut(self.timeout_details()); }
        BatchDecision::Pending
    }

    /// Process a successful browser probe. Text-only pages need one ready
    /// result; batches with images need two consecutive ready results.
    pub fn observe(&mut self, status: BatchStatus) -> BatchDecision {
        self.last_error = None;
        if !status.supported { return BatchDecision::Unsupported; }
        let ready = status.ready >= status.total;
        if ready { self.stable_ready_polls += 1; } else { self.stable_ready_polls = 0; }
        let done = ready && (status.total_images == 0 || self.stable_ready_polls >= 2);
        self.last_status = Some(status);
        if done { BatchDecision::Ready } else { BatchDecision::Pending }
    }

    /// Fatal browser-session failures terminate immediately; transient errors
    /// can be retried until the deadline. Like the Python implementation, a
    /// transient error does not clear the last successful readiness count.
    pub fn browser_error(&mut self, error: &str) -> BatchDecision {
        self.last_error = Some(error.to_owned());
        let lower = error.to_lowercase();
        if ["invalid session id", "session deleted", "chrome not reachable", "target window already closed", "no such window", "not connected to devtools"].iter().any(|marker| lower.contains(marker)) {
            BatchDecision::BrowserStopped(error.to_owned())
        } else {
            BatchDecision::Pending
        }
    }

    fn timeout_details(&self) -> String {
        if let Some(status) = &self.last_status {
            let details: Vec<String> = status.pages.iter().filter(|p| !p.ready).map(|p| format!("{} ({})", p.number, if p.reason.is_empty() { "page did not finish loading" } else { &p.reason })).collect();
            if !details.is_empty() { return details.join(", "); }
        }
        if let Some(error) = &self.last_error { return format!("browser did not respond ({error})"); }
        "pages did not finish loading".to_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn status(ready: usize, images: usize) -> BatchStatus {
        BatchStatus { supported: true, total: 2, ready, total_images: images, pending_images: 0, pages: vec![] }
    }
    #[test]
    fn page_counts_require_agreement_or_repeated_count() {
        let mut tracker = PageCountTracker::default();
        assert_eq!(tracker.observe(0, 0), None);
        assert_eq!(tracker.observe(2, 0), None);
        assert_eq!(tracker.observe(4, 0), None);
        assert_eq!(tracker.observe(4, 0), None);
        assert_eq!(tracker.observe(4, 0), Some(4));
        assert_eq!(PageCountTracker::default().observe(12, 12), Some(12));
    }
    #[test]
    fn batches_are_one_based_bounded_and_ordered() {
        assert_eq!(page_batches(5, 2).unwrap(), vec![vec![1, 2], vec![3, 4], vec![5]]);
        assert!(page_batches(0, 2).unwrap().is_empty());
        assert_eq!(page_batches(2, usize::MAX).unwrap(), vec![vec![1, 2]]);
        assert!(page_batches(5, 0).is_err());
    }
    #[test]
    fn image_batches_need_stable_readiness() {
        let mut tracker = BatchTracker::new(Duration::from_secs(10));
        assert_eq!(tracker.observe(status(2, 5)), BatchDecision::Pending);
        assert_eq!(tracker.observe(status(1, 5)), BatchDecision::Pending);
        assert_eq!(tracker.observe(status(2, 5)), BatchDecision::Pending);
        assert_eq!(tracker.observe(status(2, 5)), BatchDecision::Ready);
        assert_eq!(BatchTracker::new(Duration::from_secs(10)).observe(status(2, 0)), BatchDecision::Ready);
    }
    #[test]
    fn cancellation_timeout_and_fatal_errors() {
        let mut tracker = BatchTracker::new(Duration::from_secs(10));
        assert_eq!(tracker.before_probe(Duration::ZERO, true), BatchDecision::Cancelled);
        assert_eq!(tracker.browser_error("temporary execution context error"), BatchDecision::Pending);
        assert_eq!(tracker.before_probe(Duration::from_secs(10), false), BatchDecision::TimedOut("browser did not respond (temporary execution context error)".into()));
        assert!(matches!(tracker.browser_error("Invalid session id"), BatchDecision::BrowserStopped(_)));
        assert_eq!(tracker.observe(BatchStatus { supported: false, ..status(0, 0) }), BatchDecision::Unsupported);
    }
    #[test]
    fn timeout_reports_unready_pages() {
        let mut tracker = BatchTracker::new(Duration::from_secs(1));
        let mut probe = status(1, 2);
        probe.pages = vec![PageStatus { number: 8, ready: false, reason: "image loading".into() }];
        tracker.observe(probe);
        assert_eq!(tracker.before_probe(Duration::from_secs(1), false), BatchDecision::TimedOut("8 (image loading)".into()));
    }
}
