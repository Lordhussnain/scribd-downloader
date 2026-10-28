#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod state;
mod theme;

use eframe::egui;
use scribd_core::{extract_scribd_urls, import, output::{choose_output, ConflictPolicy, OutputDecision}, queue::{QueueItem, QueueStatus}};
use scribd_core::download::{self, DownloadError, DownloadOptions, Progress};
use std::{path::PathBuf, process::Command, sync::{Arc, atomic::{AtomicBool, Ordering}, mpsc::{self, Receiver}}, thread, time::{Duration, Instant}};

enum WorkerEvent { Progress(Progress), Finished(Result<PathBuf, DownloadError>) }
struct Active {
    cancel: Arc<AtomicBool>,
    events: Receiver<WorkerEvent>,
    index: usize,
    result_received: bool,
}

struct App {
    text: String,
    folder: String,
    items: Vec<QueueItem>,
    active: Option<Active>,
    running: bool,
    policy: ConflictPolicy,
    batch: u32,
    page_timeout: u32,
    export_timeout: u32,
    show_browser: bool,
    show_driver_console: bool,
    settings_open: bool,
    log: String,
    appearance: String,
    open_when_done: bool,
    clipboard_check: bool,
    last_clipboard: String,
    clipboard_prefill: Option<String>,
    clipboard_suggestion: Option<String>,
    recents: Vec<state::Recent>,
    save_status: String,
    last_saved: Option<state::Settings>,
    current_stage: String,
    started_at: Option<Instant>,
    was_focused: bool,
    pages_completed: usize,
    pages_total: usize,
    environment_status: String,
    input_notice: String,
    input_error: bool,
}

impl Default for App {
    fn default() -> Self {
        Self::from_settings(state::Settings::load(&state::config_dir().join("settings-rust.json")))
    }
}

impl App {
    fn from_settings(settings: state::Settings) -> Self {
        let items = settings.pending_queue.iter().filter_map(|entry| {
            let info = scribd_core::parse_scribd_url(&entry.source_url)?;
            let mut item = QueueItem::from(&info);
            item.filename = format!("{}.pdf", scribd_core::sanitize_filename(&entry.filename, "scribd-document"));
            Some(item)
        }).collect();
        let policy = match settings.conflict_policy.as_str() { "skip" => ConflictPolicy::Skip, "overwrite" => ConflictPolicy::Overwrite, _ => ConflictPolicy::Copy };
        Self { text: String::new(), folder: settings.output_dir.clone(), items, active: None, running: false, policy, batch: settings.batch_size, page_timeout: settings.page_load_timeout, export_timeout: settings.export_timeout, show_browser: settings.show_browser, show_driver_console: settings.show_driver_console, settings_open: false, log: String::new(), appearance: settings.appearance.clone(), open_when_done: settings.open_when_done, clipboard_check: settings.clipboard_check, last_clipboard: String::new(), clipboard_prefill: None, clipboard_suggestion: None, recents: settings.recents.clone(), save_status: String::new(), last_saved: Some(settings), current_stage: String::new(), started_at: None, was_focused: false, pages_completed: 0, pages_total: 0, environment_status: Self::environment_status(), input_notice: String::new(), input_error: false }
    }
}

impl App {
    fn executable_on_path(name: &str) -> bool {
        std::env::var_os("PATH").map(|paths| std::env::split_paths(&paths).any(|dir| dir.join(name).is_file())).unwrap_or(false)
    }
    fn environment_status() -> String {
        let driver = std::env::var_os("CHROMEDRIVER").map(|path| {
            let path = PathBuf::from(path);
            path.is_file() || path.file_name().and_then(|name| name.to_str()).map(Self::executable_on_path).unwrap_or(false)
        }).unwrap_or_else(|| Self::executable_on_path(if cfg!(windows) { "chromedriver.exe" } else { "chromedriver" }));
        let chrome = if cfg!(windows) {
            ["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"].iter().filter_map(std::env::var_os)
                .any(|base| PathBuf::from(base).join("Google/Chrome/Application/chrome.exe").is_file())
                || Self::executable_on_path("chrome.exe")
        } else {
            Self::executable_on_path("google-chrome") || Self::executable_on_path("chromium") || Self::executable_on_path("chromium-browser")
        };
        match (chrome, driver) {
            (true, true) => "Chrome and ChromeDriver detected (versions not checked)".into(),
            (false, true) => "Chrome not detected; install Chrome or check its location".into(),
            (true, false) => "ChromeDriver not found; add a compatible driver to PATH".into(),
            (false, false) => "Chrome and ChromeDriver not detected".into(),
        }
    }
    fn snapshot(&self) -> state::Settings {
        state::Settings {
            output_dir: self.folder.clone(), appearance: self.appearance.clone(), batch_size: self.batch,
            page_load_timeout: self.page_timeout, export_timeout: self.export_timeout, show_browser: self.show_browser, show_driver_console: self.show_driver_console,
            open_when_done: self.open_when_done, clipboard_check: self.clipboard_check,
            conflict_policy: match self.policy { ConflictPolicy::Copy => "copy", ConflictPolicy::Skip => "skip", ConflictPolicy::Overwrite => "overwrite" }.into(),
            pending_queue: self.items.iter().filter(|i| matches!(i.status, QueueStatus::Pending | QueueStatus::Downloading)).map(|i| state::Pending { source_url: i.source_url.clone(), filename: i.filename.clone() }).collect(),
            recents: self.recents.clone(),
        }
    }
    fn save_if_changed(&mut self) {
        let state = self.snapshot();
        if self.last_saved.as_ref() == Some(&state) { return; }
        match state.save(&state::config_dir().join("settings-rust.json")) {
            Ok(()) => { self.last_saved = Some(state); self.save_status = "Saved ✓".into(); }
            Err(error) => { self.save_status = format!("Could not save settings: {error}"); }
        }
    }
    /// Only replace text that was automatically prefilled, never a user's edit.
    fn observe_clipboard(&mut self, text: &str) {
        if !self.clipboard_check || text == self.last_clipboard { return; }
        self.last_clipboard = text.to_owned();
        if extract_scribd_urls(text).is_empty() {
            self.clipboard_suggestion = None;
            return;
        }
        let may_replace = self.text.trim().is_empty()
            || self.clipboard_prefill.as_deref() == Some(self.text.as_str());
        if may_replace {
            self.text = text.to_owned();
            self.clipboard_prefill = Some(self.text.clone());
            self.clipboard_suggestion = None;
            self.input_notice = "Scribd link detected from clipboard. Review it, then add it to the queue.".into();
        } else {
            self.clipboard_suggestion = Some(text.to_owned());
            self.input_notice = "New Scribd link copied. Add it without replacing your current text.".into();
        }
        self.input_error = false;
    }
    fn add_clipboard_suggestion(&mut self) {
        if let Some(text) = self.clipboard_suggestion.take() {
            let count = self.add(extract_scribd_urls(&text));
            self.input_error = count == 0;
            self.input_notice = if count == 0 { "Those documents are already in your queue.".into() } else { format!("Added {count} document{} from clipboard.", if count == 1 { "" } else { "s" }) };
        }
    }
    fn read_clipboard(&mut self, add: bool) {
        match arboard::Clipboard::new().and_then(|mut c| c.get_text()) {
            Ok(text) => {
                self.last_clipboard = text.clone();
                self.clipboard_suggestion = None;
                let docs = extract_scribd_urls(&text);
                if !docs.is_empty() {
                    if add {
                        let added = self.add(docs);
                        self.input_error = added == 0;
                        self.input_notice = if added == 0 { "Those documents are already in your queue.".into() } else { format!("Added {added} document{} from clipboard.", if added == 1 { "" } else { "s" }) };
                    } else { self.text = text; }
                } else if add { self.input_error = true; self.input_notice = "Clipboard does not contain a Scribd link.".into(); }
            }
            Err(e) => if add { self.input_error = true; self.input_notice = format!("Could not read clipboard: {e}"); },
        }
    }
    fn open_path(path: &std::path::Path, reveal: bool) {
        #[cfg(windows)] {
            let mut cmd = Command::new("explorer");
            if reveal && path.is_file() { cmd.arg(format!("/select,{}", path.display())); } else { cmd.arg(path); }
            let _ = cmd.spawn();
        }
        #[cfg(not(windows))] {
            let path = if reveal && path.is_file() { path.parent().unwrap_or(path) } else { path };
            let _ = Command::new("xdg-open").arg(path).spawn();
        }
    }
    fn diagnostic(message: &str) {
        use std::io::Write;
        let folder = state::config_dir();
        if std::fs::create_dir_all(&folder).is_ok() {
            if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(folder.join("diagnostic-rust.log")) {
                let _ = writeln!(file, "{message}");
            }
        }
    }
    fn friendly_error(error: &DownloadError) -> String {
        match error {
            DownloadError::Cancelled => "Download cancelled.".into(),
            DownloadError::Timeout(details) => format!("Pages did not load in time: {details}. Try increasing the page timeout."),
            DownloadError::InvalidPage { page, sheets } => format!("Page {page} produced {sheets} PDF sheets; expected one."),
            DownloadError::InvalidOptions(message) => (*message).into(),
            DownloadError::Backend(message) => {
                let text = message.to_lowercase();
                if text.contains("chromedriver") || text.contains("session not created") || text.contains("cannot find chrome") {
                    format!("Chrome or ChromeDriver could not be started. Check that both are installed and compatible. {message}")
                } else { message.clone() }
            }
        }
    }
    fn add(&mut self, documents: Vec<scribd_core::ScribdDocument>) -> usize {
        let mut known: std::collections::HashSet<String> = self.items.iter().filter(|i| matches!(i.status, QueueStatus::Pending | QueueStatus::Downloading)).map(|i| i.document_id.clone()).collect();
        let mut added = 0;
        for doc in documents { if known.insert(doc.document_id.clone()) { self.items.push(QueueItem::from(&doc)); added += 1; } }
        added
    }

    fn add_from_input(&mut self) -> usize {
        let documents = extract_scribd_urls(&self.text);
        if documents.is_empty() {
            self.input_notice = "No Scribd document links found. Check the link and try again.".into();
            self.input_error = true;
            return 0;
        }
        let added = self.add(documents);
        self.input_error = added == 0;
        self.input_notice = if added == 0 {
            "Those documents are already in your queue.".into()
        } else {
            self.text.clear();
            self.clipboard_prefill = None;
            format!("Added {added} document{} to your queue.", if added == 1 { "" } else { "s" })
        };
        added
    }

    fn start_next(&mut self) {
        if !self.running || self.active.is_some() { return; }
        let Some(index) = self.items.iter().position(|i| i.status == QueueStatus::Pending) else { self.running = false; return };
        let item = &mut self.items[index];
        // Pending names can be edited while the queue is running.
        item.filename = format!("{}.pdf", scribd_core::sanitize_filename(&item.filename, "scribd-document"));
        let destination = PathBuf::from(&self.folder).join(&item.filename);
        let path = match choose_output(&destination, self.policy) {
            OutputDecision::Skip { path } => { item.status = QueueStatus::Skipped; item.output_path = Some(path); return; }
            OutputDecision::Download { path, .. } => path,
        };
        let url = item.source_url.clone();
        let output = path.clone();
        let show_browser = self.show_browser;
        let show_driver_console = self.show_driver_console;
        let overwrite = self.policy == ConflictPolicy::Overwrite;
        let page_timeout = Duration::from_secs(self.page_timeout as u64);
        let command_timeout = Duration::from_secs(self.export_timeout as u64);
        let options = DownloadOptions { batch_size: self.batch as usize, page_load_timeout: page_timeout, ..Default::default() };
        let cancel = Arc::new(AtomicBool::new(false));
        let worker_cancel = cancel.clone();
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            let result = match scribd_browser::Browser::new(show_browser, page_timeout, command_timeout, overwrite) {
                Ok(browser) => {
                    let mut browser = browser.with_driver_console(show_driver_console);
                    download::download(&mut browser, &url, &output, &options, &worker_cancel, &mut |p| { let _ = tx.send(WorkerEvent::Progress(p)); })
                },
                Err(error) => Err(DownloadError::Backend(error)),
            };
            let _ = tx.send(WorkerEvent::Finished(result));
        });
        item.status = QueueStatus::Downloading; item.activity = "Starting Chrome…".into(); item.percent = 0; item.detail.clear(); item.output_path = Some(path);
        self.active = Some(Active { cancel, events: rx, index, result_received: false });
        self.started_at = Some(Instant::now());
        self.current_stage = "connect".into();
        self.pages_completed = 0;
        self.pages_total = 0;
    }

    fn start_queue(&mut self) {
        if self.running || self.active.is_some() { return; }
        if !self.text.trim().is_empty() {
            let _ = self.add_from_input();
        }
        if self.folder.trim().is_empty() {
            self.input_error = true;
            self.input_notice = "Choose an output folder before starting the queue.".into();
            return;
        }
        for item in &mut self.items {
            if item.status == QueueStatus::Pending {
                item.filename = format!("{}.pdf", scribd_core::sanitize_filename(&item.filename, "scribd-document"));
            }
        }
        if self.items.iter().any(|i| i.status == QueueStatus::Pending) { self.running = true; }
        else if self.input_notice.is_empty() { self.input_error = true; self.input_notice = "Add a Scribd document before starting the queue.".into(); }
    }
    fn cancel(&mut self, stop: bool) {
        if stop { self.running = false; }
        if let Some(active) = &self.active { active.cancel.store(true, Ordering::Relaxed); }

    }

    fn poll(&mut self) {
        if let Some(active) = &mut self.active {
            let mut disconnected = false;
            loop {
                let event = match active.events.try_recv() {
                    Ok(event) => event,
                    Err(mpsc::TryRecvError::Empty) => break,
                    Err(mpsc::TryRecvError::Disconnected) => { disconnected = true; break; }
                };
                let item = &mut self.items[active.index];
                match event {
                    WorkerEvent::Progress(progress) => {
                        self.current_stage = progress.stage.into();
                        if progress.stage == "export" && progress.total > 0 {
                            self.pages_completed = self.pages_completed.max(progress.current);
                            self.pages_total = progress.total;
                        }
                        item.activity = progress.message.clone();
                        self.log.push_str(&format!("{}\n", progress.message));
                        Self::diagnostic(&progress.message);
                        if progress.total > 0 { item.percent = (progress.current.saturating_mul(100) / progress.total).min(100) as u32; }
                    }
                    WorkerEvent::Finished(result) => {
                        active.result_received = true;
                        item.activity.clear();
                        match result {
                            Ok(path) => {
                                item.status = QueueStatus::Done; item.percent = 100; item.output_path = Some(path.clone());
                                let path_text = path.to_string_lossy().into_owned();
                                self.recents.retain(|r| r.path != path_text);
                                self.recents.insert(0, state::Recent { path: path_text, title: item.title.clone() });
                                self.recents.truncate(5);
                                if self.open_when_done { Self::open_path(&path, false); }
                            }
                            Err(DownloadError::Cancelled) => { item.status = QueueStatus::Cancelled; }
                            Err(error) => { item.status = QueueStatus::Failed; item.detail = Self::friendly_error(&error); self.log.push_str(&format!("{}\n", item.detail)); Self::diagnostic(&item.detail); }
                        }
                    }
                }
            }
            if disconnected {
                let item = &mut self.items[active.index];
                if !active.result_received { item.status = QueueStatus::Failed; item.detail = "Download worker stopped unexpectedly.".into(); }
                self.active = None;
                self.started_at = None;
            }
        }
        self.start_next();
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, frame: &mut eframe::Frame) {
        self.poll();
        let dropped = ctx.input(|i| i.raw.dropped_files.clone());
        if !dropped.is_empty() {
            let paths: Vec<PathBuf> = dropped.iter().filter_map(|f| f.path.clone()).collect();
            let mut added = self.add(import::read_urls_from_files(&paths));
            for file in dropped {
                if let Some(bytes) = file.bytes {
                    if bytes.len() <= import::MAX_LINK_FILE_BYTES as usize {
                        if let Ok(text) = std::str::from_utf8(&bytes) { added += self.add(extract_scribd_urls(text)); }
                    }
                }
            }
            self.input_error = added == 0;
            self.input_notice = if added == 0 { "No new Scribd links found in the dropped files.".into() } else { format!("Added {added} document{} from dropped files.", if added == 1 { "" } else { "s" }) };
        }
        let focused = ctx.input(|i| i.focused);
        if focused && !self.was_focused && self.clipboard_check {
            if let Ok(mut clipboard) = arboard::Clipboard::new() {
                if let Ok(text) = clipboard.get_text() { self.observe_clipboard(&text); }
            }
        }
        self.was_focused = focused;
        if ctx.input(|i| i.modifiers.ctrl && i.key_pressed(egui::Key::Enter)) { self.start_queue(); }
        if ctx.input(|i| i.key_pressed(egui::Key::Escape)) && self.active.is_some() && !self.settings_open { self.cancel(true); }
        if ctx.input(|i| i.viewport().close_requested()) && self.active.is_some() {
            self.cancel(true);
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            self.log.push_str("Stopping current download. Close the window after it finishes.\n");
        }
        if self.active.is_some() || self.running { ctx.request_repaint_after(std::time::Duration::from_millis(100)); }
        egui::TopBottomPanel::top("header").show(ctx, |ui| {
            egui::Frame::none().fill(ui.visuals().panel_fill).inner_margin(egui::Margin::symmetric(24.0, 16.0)).show(ui, |ui| {
                ui.horizontal(|ui| {
                    egui::Frame::none().fill(theme::ACCENT).rounding(10.0).inner_margin(egui::Margin::symmetric(14.0, 8.0)).show(ui, |ui| {
                        ui.label(egui::RichText::new("S").size(22.0).strong().color(egui::Color32::WHITE));
                    });
                    ui.vertical(|ui| {
                        ui.label(egui::RichText::new("Scribd Downloader").size(21.0).strong());
                        ui.label(egui::RichText::new(format!("DESKTOP  /  v{}", env!("CARGO_PKG_VERSION"))).size(10.0).color(theme::muted(ui)));
                    });
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if ui.button("Settings").clicked() { self.settings_open = true; }
                    });
                });
            });
        });
        if self.settings_open {
            egui::Window::new("Settings").open(&mut self.settings_open).default_width(430.0).show(ctx, |ui| {
                ui.label(egui::RichText::new("Make it yours").size(18.0).strong());
                ui.label(egui::RichText::new("Changes save automatically and apply to the next document.").color(theme::muted(ui)).size(12.0));
                ui.add_space(8.0);
                ui.label(egui::RichText::new("Appearance").strong());
                ui.horizontal(|ui| { for name in ["System", "Light", "Dark"] {
                    if ui.selectable_label(self.appearance == name, name).clicked() {
                        self.appearance = name.into();
                        let dark = match name { "Light" => false, "Dark" => true, _ => frame.info().system_theme.map(|t| t == eframe::Theme::Dark).unwrap_or(true) };
                        theme::apply(ctx, dark);
                    }
                } });
                ui.add_space(8.0);
                ui.label(egui::RichText::new("Download preferences").strong());
                ui.horizontal(|ui| { ui.label("Pages loaded at once"); for n in [4, 8, 16] { ui.selectable_value(&mut self.batch, n, n.to_string()); } });
                ui.add(egui::Slider::new(&mut self.page_timeout, 10..=600).text("Page timeout (seconds)"));
                ui.add(egui::Slider::new(&mut self.export_timeout, 30..=3600).text("Export timeout (seconds)"));
                ui.checkbox(&mut self.show_browser, "Show Chrome window while downloading");
                ui.checkbox(&mut self.show_driver_console, "Show ChromeDriver console (diagnostics)");
                ui.checkbox(&mut self.open_when_done, "Open PDF when done");
                ui.checkbox(&mut self.clipboard_check, "Detect Scribd links from clipboard");
                ui.add_space(6.0);
                ui.label(egui::RichText::new(&self.save_status).color(theme::ACCENT_LIGHT).size(12.0));
            });
        }
        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().id_source("main_scroll").auto_shrink([false, false]).show(ui, |ui| {
                ui.add_space(12.0);
                // Keep the content comfortable on wide windows, without forcing a
                // minimum width on smaller Windows displays.
                let width = ui.available_width().min(900.0);
                ui.vertical_centered(|ui| {
                    ui.set_max_width(width);
                    ui.vertical(|ui| {
                        ui.label(egui::RichText::new("Your documents, ready when you are.").size(24.0).strong());
                        ui.label(egui::RichText::new("Paste a link, build your queue, and save clean PDFs locally.").size(13.0).color(theme::muted(ui)));
                        ui.label(egui::RichText::new(&self.environment_status).size(11.0).color(theme::muted(ui)));
                        ui.add_space(10.0);

                        theme::card(ui, |ui| {
                            theme::section(ui, "Add Scribd documents", "Paste one or more links, or import a link file.");
                            if ui.add(egui::TextEdit::multiline(&mut self.text).hint_text("https://www.scribd.com/document/...").desired_rows(2).desired_width(f32::INFINITY)).changed() {
                                self.input_notice.clear();
                                self.input_error = false;
                                self.clipboard_prefill = None;
                            }
                            ui.horizontal_wrapped(|ui| {
                                if ui.add(theme::primary("Add to queue")).clicked() { self.add_from_input(); }
                                if ui.button("Paste").clicked() { self.read_clipboard(true); }
                                if self.clipboard_suggestion.is_some() && ui.button("Add copied link").clicked() { self.add_clipboard_suggestion(); }
                                if ui.button("Add from file…").clicked() {
                                    if let Some(paths) = rfd::FileDialog::new().add_filter("Link files", &["txt", "text", "md", "csv", "url", "html", "htm"]).pick_files() {
                                        let docs = import::read_urls_from_files(&paths);
                                        let added = self.add(docs);
                                        self.input_error = added == 0;
                                        self.input_notice = if added == 0 { "No new Scribd links found in those files.".into() } else { format!("Added {added} document{} from file.", if added == 1 { "" } else { "s" }) };
                                    }
                                }
                            });
                            if self.input_notice.is_empty() {
                                ui.label(egui::RichText::new("Tip: you can also drop link files into the window.").size(11.0).color(theme::muted(ui)));
                            } else {
                                let color = if self.input_error { ui.visuals().error_fg_color } else { theme::ACCENT_LIGHT };
                                ui.label(egui::RichText::new(&self.input_notice).size(12.0).color(color));
                            }
                        });
                        ui.add_space(10.0);

                        if let (Some(started), Some(active)) = (self.started_at, self.active.as_ref()) {
                            let item = &self.items[active.index];
                            theme::card(ui, |ui| {
                                ui.horizontal_wrapped(|ui| {
                                    ui.label(egui::RichText::new("Downloading").size(16.0).strong());
                                    ui.label(egui::RichText::new(&item.title).color(theme::muted(ui)));
                                });
                                ui.add_space(4.0);
                                ui.horizontal_wrapped(|ui| {
                                    for (key, title) in [("connect", "Connect"), ("load", "Load pages"), ("export", "Export PDF"), ("merge", "Merge")] {
                                        let color = if self.current_stage == key { theme::ACCENT_LIGHT } else { theme::muted(ui) };
                                        ui.label(egui::RichText::new(title).strong().size(12.0).color(color));
                                    }
                                });
                                ui.add_space(6.0);
                                if self.pages_total == 0 {
                                    ui.horizontal(|ui| { ui.spinner(); ui.label(egui::RichText::new("Connecting and preparing pages…").size(12.0).color(theme::muted(ui))); });
                                } else {
                                    let progress = self.pages_completed as f32 / self.pages_total as f32;
                                    ui.add(egui::ProgressBar::new(progress.clamp(0.0, 1.0)).fill(theme::ACCENT).desired_width(ui.available_width()).show_percentage());
                                }
                                let activity = if item.activity.is_empty() { "Starting…" } else { &item.activity };
                                ui.label(egui::RichText::new(activity).size(12.0));
                                let elapsed = started.elapsed().as_secs();
                                let eta = if self.pages_completed > 0 && self.pages_total > self.pages_completed {
                                    let remaining = elapsed.saturating_mul((self.pages_total - self.pages_completed) as u64) / self.pages_completed as u64;
                                    format!("  ·  estimated {remaining}s remaining")
                                } else { String::new() };
                                let pages = if self.pages_total > 0 { format!("{} / {} pages  ·  ", self.pages_completed, self.pages_total) } else { String::new() };
                                ui.label(egui::RichText::new(format!("{pages}{elapsed}s elapsed{eta}")).size(12.0).color(theme::muted(ui)));
                            });
                            ui.add_space(10.0);
                        }

                        theme::card(ui, |ui| {
                            ui.horizontal_wrapped(|ui| {
                                ui.label(egui::RichText::new("Queue").size(16.0).strong());
                                ui.label(egui::RichText::new(format!("{} documents", self.items.len())).color(theme::muted(ui)).size(12.0));
                                if ui.add_enabled(!self.running && self.active.is_none(), egui::Button::new("Clear finished")).clicked() { self.items.retain(|i| matches!(i.status, QueueStatus::Pending | QueueStatus::Downloading)); }
                                if ui.add_enabled(!self.running && self.active.is_none(), egui::Button::new("Clear all")).clicked() { self.items.clear(); }
                            });
                            ui.label(egui::RichText::new(scribd_core::queue::queue_title_text(&self.items)).size(11.0).color(theme::muted(ui)));
                            ui.add_space(5.0);
                            let mut remove = None;
                            if self.items.is_empty() {
                                egui::Frame::none().fill(ui.visuals().extreme_bg_color).rounding(10.0).inner_margin(18.0).show(ui, |ui| {
                                    ui.set_min_width(ui.available_width());
                                    ui.vertical_centered(|ui| {
                                        ui.label(egui::RichText::new("Your queue is empty").strong());
                                        ui.label(egui::RichText::new("Add a document above to get started.").size(12.0).color(theme::muted(ui)));
                                    });
                                });
                            } else {
                                egui::ScrollArea::vertical().id_source("queue_scroll").max_height(250.0).show(ui, |ui| {
                                    for (index, item) in self.items.iter_mut().enumerate() {
                                        egui::Frame::none().fill(ui.visuals().extreme_bg_color).rounding(9.0).inner_margin(12.0).show(ui, |ui| {
                                            ui.set_min_width(ui.available_width());
                                            ui.horizontal_wrapped(|ui| {
                                                let color = match item.status { QueueStatus::Done => theme::ACCENT_LIGHT, QueueStatus::Failed => ui.visuals().error_fg_color, QueueStatus::Downloading => theme::ACCENT_LIGHT, _ => theme::muted(ui) };
                                                ui.label(egui::RichText::new("●").color(color));
                                                ui.label(egui::RichText::new(&item.title).strong());
                                                ui.label(egui::RichText::new(format!("{:?}", item.status)).size(11.0).color(theme::muted(ui)));
                                                if item.status != QueueStatus::Downloading && self.active.as_ref().map_or(true, |a| a.index != index) && ui.small_button("Remove").clicked() { remove = Some(index); }
                                                if matches!(item.status, QueueStatus::Failed | QueueStatus::Cancelled) && self.active.as_ref().map_or(true, |a| a.index != index) && ui.small_button("Retry").clicked() { item.status = QueueStatus::Pending; item.detail.clear(); }
                                                if item.status == QueueStatus::Done && ui.small_button("Open").clicked() { if let Some(path) = &item.output_path { Self::open_path(path, false); } }
                                                if item.status == QueueStatus::Done && ui.small_button("Folder").clicked() { if let Some(path) = &item.output_path { Self::open_path(path, true); } }
                                            });
                                            if item.status == QueueStatus::Pending {
                                                ui.horizontal(|ui| { ui.label(egui::RichText::new("Filename").size(12.0).color(theme::muted(ui))); ui.add(egui::TextEdit::singleline(&mut item.filename).desired_width((ui.available_width() - 20.0).max(100.0))); });
                                            } else { ui.label(egui::RichText::new(scribd_core::queue::queue_item_subtext(item)).size(12.0).color(theme::muted(ui))); }
                                            if item.status == QueueStatus::Downloading { ui.add(egui::ProgressBar::new(item.percent as f32 / 100.0).fill(theme::ACCENT).show_percentage()); }
                                        });
                                        ui.add_space(5.0);
                                    }
                                });
                            }
                            if let Some(index) = remove { self.items.remove(index); if let Some(active) = &mut self.active { if index < active.index { active.index -= 1; } } }
                        });
                        ui.add_space(10.0);

                        theme::card(ui, |ui| {
                            theme::section(ui, "Save PDFs to", "Choose a folder and what to do when a file already exists.");
                            ui.horizontal_wrapped(|ui| {
                                ui.add_enabled(!self.running && self.active.is_none(), egui::TextEdit::singleline(&mut self.folder).desired_width((ui.available_width() - 115.0).max(100.0)));
                                if ui.add_enabled(!self.running && self.active.is_none(), egui::Button::new("Browse…")).clicked() { if let Some(path) = rfd::FileDialog::new().pick_folder() { self.folder = path.to_string_lossy().into_owned(); } }
                            });
                            ui.add_enabled_ui(!self.running && self.active.is_none(), |ui| { ui.horizontal_wrapped(|ui| {
                                ui.label(egui::RichText::new("If a file exists:").size(12.0).color(theme::muted(ui)));
                                ui.selectable_value(&mut self.policy, ConflictPolicy::Copy, "Save as copy");
                                ui.selectable_value(&mut self.policy, ConflictPolicy::Skip, "Skip existing");
                                ui.selectable_value(&mut self.policy, ConflictPolicy::Overwrite, "Overwrite");
                            }); });
                        });
                        ui.add_space(12.0);
                        ui.horizontal_wrapped(|ui| {
                            if ui.add_enabled(!self.running && self.active.is_none(), theme::primary("Download PDF")).clicked() { self.start_queue(); }
                            if ui.add_enabled(self.active.is_some(), egui::Button::new("Skip current document")).clicked() { self.cancel(false); }
                            if ui.add_enabled(self.running || self.active.is_some(), egui::Button::new("Stop queue")).clicked() { self.cancel(true); }
                        });


                        if !self.recents.is_empty() {
                            ui.add_space(10.0);
                            theme::card(ui, |ui| {
                                ui.horizontal_wrapped(|ui| { ui.label(egui::RichText::new("Recent downloads").size(16.0).strong()); if ui.button("Open latest PDF").clicked() { Self::open_path(std::path::Path::new(&self.recents[0].path), false); } });
                                for recent in &self.recents { ui.horizontal_wrapped(|ui| {
                                    ui.label(&recent.title);
                                    if ui.small_button("Open").clicked() { Self::open_path(std::path::Path::new(&recent.path), false); }
                                    if ui.small_button("Folder").clicked() { Self::open_path(std::path::Path::new(&recent.path), true); }
                                }); }
                            });
                        }
                        ui.add_space(10.0);
                        theme::card(ui, |ui| {
                            ui.horizontal_wrapped(|ui| {
                                ui.label(egui::RichText::new("Activity log").size(16.0).strong());
                                if ui.button("Copy log").clicked() { ui.output_mut(|o| o.copied_text = self.log.clone()); }
                                if ui.button("Clear").clicked() { self.log.clear(); }
                                if ui.button("Diagnostics folder").clicked() { Self::open_path(&state::config_dir(), true); }
                            });
                            egui::ScrollArea::vertical().id_source("log_scroll").max_height(140.0).show(ui, |ui| {
                                ui.add(egui::TextEdit::multiline(&mut self.log).hint_text("Download activity will appear here.").desired_width(f32::INFINITY).desired_rows(3).interactive(false));
                            });
                        });
                        ui.add_space(10.0);
                        ui.horizontal_wrapped(|ui| {
                            ui.label(egui::RichText::new(&self.environment_status).size(11.0).color(theme::muted(ui)));
                            if ui.small_button("Recheck").clicked() { self.environment_status = Self::environment_status(); }
                            ui.hyperlink_to("Chrome", "https://www.google.com/chrome/");
                            ui.hyperlink_to("ChromeDriver", "https://developer.chrome.com/docs/chromedriver/downloads");
                        });
                        ui.label(egui::RichText::new("Runs locally in Chrome  ·  Only download documents you have the right to access.").size(11.0).color(theme::muted(ui)));
                        ui.add_space(20.0);
                    });
                });
            });
        });
        self.save_if_changed();
    }
}

fn main() -> eframe::Result<()> {
    let options = eframe::NativeOptions { viewport: egui::ViewportBuilder::default().with_drag_and_drop(true).with_inner_size([900.0, 800.0]), ..Default::default() };
    eframe::run_native("Scribd Downloader", options, Box::new(|cc| {
        let app = App::default();
        theme::install_fonts(&cc.egui_ctx);
        let dark = match app.appearance.as_str() { "Light" => false, "Dark" => true, _ => cc.egui_ctx.style().visuals.dark_mode };
        theme::apply(&cc.egui_ctx, dark);
        Box::new(app)
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn friendly_errors_distinguish_cancel_timeout_and_invalid_page() {
        assert!(App::friendly_error(&DownloadError::Cancelled).contains("cancelled"));
        assert!(App::friendly_error(&DownloadError::Timeout("image loading".into())).contains("image loading"));
        assert!(App::friendly_error(&DownloadError::InvalidPage { page: 3, sheets: 2 }).contains("Page 3"));
    }
    #[test]
    fn clipboard_queue_deduplication_keeps_first_pending_document() {
        let mut app = App::from_settings(state::Settings::default());
        let docs = extract_scribd_urls("scribd.com/doc/5/One scribd.com/doc/6/Two");
        app.add(docs.clone());
        app.add(docs);
        assert_eq!(app.items.len(), 2);
        assert_eq!(app.items[0].document_id, "5");
        assert_eq!(app.items[1].document_id, "6");
    }
    #[test]
    fn invalid_input_is_preserved_with_actionable_feedback() {
        let mut app = App::from_settings(state::Settings::default());
        app.text = "https://example.org/not-scribd".into();
        assert_eq!(app.add_from_input(), 0);
        assert_eq!(app.text, "https://example.org/not-scribd");
        assert!(app.input_error);
        assert!(app.input_notice.contains("No Scribd"));
        assert!(app.items.is_empty());
    }
    #[test]
    fn valid_and_duplicate_input_update_queue_and_feedback() {
        let mut app = App::from_settings(state::Settings::default());
        app.text = "scribd.com/document/7/One scribd.com/doc/8/Two".into();
        assert_eq!(app.add_from_input(), 2);
        assert!(!app.input_error);
        assert!(app.text.is_empty());
        assert_eq!(app.items.len(), 2);
        app.text = "scribd.com/doc/7/One".into();
        assert_eq!(app.add_from_input(), 0);
        assert!(app.input_error);
        assert_eq!(app.items.len(), 2);
        assert!(!app.text.is_empty());
    }
    #[test]
    fn start_rejects_empty_output_folder_without_losing_queue() {
        let mut app = App::from_settings(state::Settings::default());
        app.folder = " ".into();
        app.text = "scribd.com/document/1/Example".into();
        app.start_queue();
        assert!(!app.running);
        assert_eq!(app.items.len(), 1);
        assert!(app.input_notice.contains("output folder"));
    }
    #[test]
    fn snapshot_saves_pending_and_active_but_not_finished_items() {
        let mut app = App::from_settings(state::Settings::default());
        app.add(extract_scribd_urls("scribd.com/doc/1/A scribd.com/doc/2/B scribd.com/doc/3/C"));
        app.items[1].status = QueueStatus::Downloading;
        app.items[2].status = QueueStatus::Done;
        let saved = app.snapshot();
        assert_eq!(saved.pending_queue.len(), 2);
        assert!(saved.pending_queue[0].source_url.contains("/1/"));
        assert!(saved.pending_queue[1].source_url.contains("/2/"));
        let restored = App::from_settings(saved);
        assert_eq!(restored.items.len(), 2);
        assert!(restored.items.iter().all(|item| item.status == QueueStatus::Pending));
    }

    #[test]
    fn clipboard_prefill_updates_when_a_second_link_is_copied() {
        let mut app = App::from_settings(state::Settings::default());
        app.observe_clipboard("https://www.scribd.com/document/1/First");
        assert!(app.text.contains("/1/"));
        app.observe_clipboard("https://www.scribd.com/document/2/Second");
        assert!(app.text.contains("/2/"));
        assert!(app.clipboard_suggestion.is_none());
    }
    #[test]
    fn clipboard_detection_preserves_user_edits_and_offers_the_new_link() {
        let mut app = App::from_settings(state::Settings::default());
        app.observe_clipboard("scribd.com/doc/1/First");
        app.text = "My draft that must not be lost".into();
        app.observe_clipboard("scribd.com/doc/2/Second");
        assert_eq!(app.text, "My draft that must not be lost");
        assert!(app.clipboard_suggestion.is_some());
        app.add_clipboard_suggestion();
        assert_eq!(app.items.len(), 1);
        assert_eq!(app.items[0].document_id, "2");
        assert_eq!(app.text, "My draft that must not be lost");
        assert!(app.clipboard_suggestion.is_none());
    }
    #[test]
    fn clipboard_detection_can_be_disabled_and_ignores_non_links() {
        let mut app = App::from_settings(state::Settings::default());
        app.clipboard_check = false;
        app.observe_clipboard("scribd.com/doc/1/One");
        assert!(app.text.is_empty());
        app.clipboard_check = true;
        app.observe_clipboard("plain text");
        assert!(app.text.is_empty());
        app.observe_clipboard("scribd.com/doc/2/Two");
        assert!(app.text.contains("/2/"));
    }
    #[test]
    fn same_clipboard_contents_are_not_reinserted_after_adding() {
        let mut app = App::from_settings(state::Settings::default());
        let link = "scribd.com/doc/1/First";
        app.observe_clipboard(link);
        assert_eq!(app.add_from_input(), 1);
        app.observe_clipboard(link);
        assert!(app.text.is_empty());
        assert_eq!(app.items.len(), 1);
    }

}
