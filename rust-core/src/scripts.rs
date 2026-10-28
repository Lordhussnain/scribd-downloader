//! JavaScript used by the browser backend,
//! using WebDriver's `arguments` convention.
pub const HIDE_COOKIE_DIALOGS: &str = include_str!("browser_scripts/hide_cookie_dialogs.js");
pub const PREPARE_PRINT: &str = include_str!("browser_scripts/prepare_print.js");
pub const PRINT_STYLES: &str = include_str!("browser_scripts/print_styles.js");
pub const PAGE_COUNT_SNAPSHOT: &str = include_str!("browser_scripts/page_count_snapshot.js");
pub const PROBE_BATCH: &str = include_str!("browser_scripts/probe_batch.js");
pub const RELEASE_BATCH: &str = include_str!("browser_scripts/release_batch.js");
pub const ISOLATE_PAGE: &str = include_str!("browser_scripts/isolate_page.js");
