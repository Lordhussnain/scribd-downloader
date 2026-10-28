#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod state;

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
    settings_open: bool,
    log: String,
    appearance: String,
    open_when_done: bool,
    clipboard_check: bool,
    last_clipboard: String,
    recents: Vec<state::Recent>,
    save_status: String,
    last_saved: Option<state::Settings>,
    current_stage: String,
    started_at: Option<Instant>,
    was_focused: bool,
    pages_completed: usize,
    pages_total: usize,
    environment_status: String,
}

impl Default for App {
    fn default() -> Self {
        let settings = state::Settings::load(&state::config_dir().join("settings-rust.json"));
        let items = settings.pending_queue.iter().filter_map(|entry| {
            let info = scribd_core::parse_scribd_url(&entry.source_url)?;
            let mut item = QueueItem::from(&info);
            item.filename = format!("{}.pdf", scribd_core::sanitize_filename(&entry.filename, "scribd-document"));
            Some(item)
        }).collect();
        let policy = match settings.conflict_policy.as_str() { "skip" => ConflictPolicy::Skip, "overwrite" => ConflictPolicy::Overwrite, _ => ConflictPolicy::Copy };
        Self { text: String::new(), folder: settings.output_dir.clone(), items, active: None, running: false, policy, batch: settings.batch_size, page_timeout: settings.page_load_timeout, export_timeout: settings.export_timeout, show_browser: settings.show_browser, settings_open: false, log: String::new(), appearance: settings.appearance.clone(), open_when_done: settings.open_when_done, clipboard_check: settings.clipboard_check, last_clipboard: String::new(), recents: settings.recents.clone(), save_status: String::new(), last_saved: Some(settings), current_stage: String::new(), started_at: None, was_focused: false, pages_completed: 0, pages_total: 0, environment_status: Self::environment_status() }
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
            page_load_timeout: self.page_timeout, export_timeout: self.export_timeout, show_browser: self.show_browser,
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
    fn read_clipboard(&mut self, add: bool) {
        match arboard::Clipboard::new().and_then(|mut c| c.get_text()) {
            Ok(text) => {
                self.last_clipboard = text.clone();
                let docs = extract_scribd_urls(&text);
                if !docs.is_empty() { if add { self.add(docs); } else { self.text = text; } }
                else if add { self.log.push_str("Clipboard does not contain a Scribd link.\n"); }
            }
            Err(e) => if add { self.log.push_str(&format!("Could not read clipboard: {e}\n")); },
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
    fn add(&mut self, documents: Vec<scribd_core::ScribdDocument>) {
        let mut known: std::collections::HashSet<String> = self.items.iter().filter(|i| matches!(i.status, QueueStatus::Pending | QueueStatus::Downloading)).map(|i| i.document_id.clone()).collect();
        for doc in documents { if known.insert(doc.document_id.clone()) { self.items.push(QueueItem::from(&doc)); } }
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
        let overwrite = self.policy == ConflictPolicy::Overwrite;
        let page_timeout = Duration::from_secs(self.page_timeout as u64);
        let command_timeout = Duration::from_secs(self.export_timeout as u64);
        let options = DownloadOptions { batch_size: self.batch as usize, page_load_timeout: page_timeout, ..Default::default() };
        let cancel = Arc::new(AtomicBool::new(false));
        let worker_cancel = cancel.clone();
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            let result = match scribd_browser::Browser::new(show_browser, page_timeout, command_timeout, overwrite) {
                Ok(mut browser) => download::download(&mut browser, &url, &output, &options, &worker_cancel, &mut |p| { let _ = tx.send(WorkerEvent::Progress(p)); }),
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
        let docs = extract_scribd_urls(&self.text);
        if !docs.is_empty() { self.add(docs); self.text.clear(); }
        for item in &mut self.items {
            if item.status == QueueStatus::Pending {
                item.filename = format!("{}.pdf", scribd_core::sanitize_filename(&item.filename, "scribd-document"));
            }
        }
        if self.items.iter().any(|i| i.status == QueueStatus::Pending) { self.running = true; }
        else { self.log.push_str("Add a Scribd document before starting the queue.\n"); }
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
            self.add(import::read_urls_from_files(&paths));
            for file in dropped { if let Some(bytes) = file.bytes { if let Ok(text) = std::str::from_utf8(&bytes) { self.add(extract_scribd_urls(text)); } } }
        }
        let focused = ctx.input(|i| i.focused);
        if focused && !self.was_focused && self.clipboard_check && self.text.is_empty() {
            if let Ok(mut clipboard) = arboard::Clipboard::new() { if let Ok(text) = clipboard.get_text() {
                if text != self.last_clipboard { self.last_clipboard = text.clone(); if !extract_scribd_urls(&text).is_empty() { self.text = text; } }
            } }
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
            ui.horizontal(|ui| {
                ui.heading("Scribd Downloader");
                ui.label(concat!("v", env!("CARGO_PKG_VERSION")));
                if ui.button("Settings").clicked() { self.settings_open = true; }
            });
            ui.horizontal(|ui| {
                ui.label(&self.environment_status);
                if ui.button("Recheck").clicked() { self.environment_status = Self::environment_status(); }
                ui.hyperlink_to("Get Chrome", "https://www.google.com/chrome/");
                ui.hyperlink_to("Get ChromeDriver", "https://developer.chrome.com/docs/chromedriver/downloads");
            });
        });
        if self.settings_open {
            egui::Window::new("Settings").open(&mut self.settings_open).show(ctx, |ui| {
                ui.label("Changes are saved automatically and apply to the next document.");
                ui.label(&self.save_status);
                ui.horizontal(|ui| { ui.label("Appearance:"); for name in ["System", "Light", "Dark"] { if ui.selectable_label(self.appearance == name, name).clicked() { self.appearance = name.into(); if name == "Light" { ctx.set_visuals(egui::Visuals::light()); } else if name == "Dark" { ctx.set_visuals(egui::Visuals::dark()); } else if let Some(theme) = frame.info().system_theme { ctx.set_visuals(match theme { eframe::Theme::Dark => egui::Visuals::dark(), eframe::Theme::Light => egui::Visuals::light() }); } } } });
                ui.horizontal(|ui| { ui.label("Pages loaded at once:"); for n in [4, 8, 16] { ui.selectable_value(&mut self.batch, n, n.to_string()); } });
                ui.add(egui::Slider::new(&mut self.page_timeout, 10..=600).text("Page timeout (seconds)"));
                ui.add(egui::Slider::new(&mut self.export_timeout, 30..=3600).text("Export timeout (seconds)"));
                ui.checkbox(&mut self.show_browser, "Show Chrome window");
                ui.checkbox(&mut self.open_when_done, "Open PDF when done");
                ui.checkbox(&mut self.clipboard_check, "Detect Scribd links from clipboard");
            });
        }
        egui::CentralPanel::default().show(ctx, |ui| {
          egui::ScrollArea::vertical().id_source("main_scroll").show(ui, |ui| {
            ui.label("Add Scribd links (Ctrl+V to paste)");
            ui.add(egui::TextEdit::multiline(&mut self.text).desired_rows(3).desired_width(f32::INFINITY));
            ui.horizontal(|ui| {
                if ui.button("Add to queue").clicked() { let docs = extract_scribd_urls(&self.text); self.add(docs); self.text.clear(); }
                if ui.button("Paste").clicked() { self.read_clipboard(true); }
                if ui.button("Add from file…").clicked() { if let Some(paths) = rfd::FileDialog::new().add_filter("Link files", &["txt", "text", "md", "csv", "url", "html", "htm"]).pick_files() { self.add(import::read_urls_from_files(&paths)); } }
            });
            ui.horizontal(|ui| {
                ui.label("Output folder:"); ui.add_enabled(!self.running && self.active.is_none(), egui::TextEdit::singleline(&mut self.folder).desired_width(300.0));
                if ui.add_enabled(!self.running && self.active.is_none(), egui::Button::new("Browse…")).clicked() { if let Some(path) = rfd::FileDialog::new().pick_folder() { self.folder = path.to_string_lossy().into_owned(); } }
            });
            ui.add_enabled_ui(!self.running && self.active.is_none(), |ui| { ui.horizontal(|ui| { ui.label("Existing files:"); ui.selectable_value(&mut self.policy, ConflictPolicy::Copy, "Save as copy"); ui.selectable_value(&mut self.policy, ConflictPolicy::Skip, "Skip existing"); ui.selectable_value(&mut self.policy, ConflictPolicy::Overwrite, "Overwrite"); }); });
            ui.separator();
            ui.heading(scribd_core::queue::queue_title_text(&self.items));
            let mut remove = None;
            egui::ScrollArea::vertical().max_height(250.0).show(ui, |ui| {
                for (index, item) in self.items.iter_mut().enumerate() {
                    ui.group(|ui| {
                        ui.horizontal(|ui| {
                            ui.label(format!("{} — {:?}", item.title, item.status));
                            if item.status != QueueStatus::Downloading && self.active.as_ref().map_or(true, |a| a.index != index) && ui.button("Remove").clicked() { remove = Some(index); }
                            if matches!(item.status, QueueStatus::Failed | QueueStatus::Cancelled) && self.active.as_ref().map_or(true, |a| a.index != index) && ui.button("Retry").clicked() { item.status = QueueStatus::Pending; item.detail.clear(); }
                            if item.status == QueueStatus::Done && ui.button("Open").clicked() { if let Some(path) = &item.output_path { Self::open_path(path, false); } }
                            if item.status == QueueStatus::Done && ui.button("Folder").clicked() { if let Some(path) = &item.output_path { Self::open_path(path, true); } }
                        });
                        if item.status == QueueStatus::Pending {
                            ui.horizontal(|ui| { ui.label("Filename:"); ui.text_edit_singleline(&mut item.filename); });
                        } else { ui.label(scribd_core::queue::queue_item_subtext(item)); }
                        if item.status == QueueStatus::Downloading { ui.add(egui::ProgressBar::new(item.percent as f32 / 100.0).show_percentage()); }
                    });
                }
            });
            if let Some(index) = remove {
                self.items.remove(index);
                if let Some(active) = &mut self.active { if index < active.index { active.index -= 1; } }
            }
            ui.horizontal(|ui| {
                if ui.add_enabled(!self.running && self.active.is_none(), egui::Button::new("Start queue")).clicked() {
                    self.start_queue();
                }
                if ui.add_enabled(self.active.is_some(), egui::Button::new("Skip current document")).clicked() { self.cancel(false); }
                if ui.add_enabled(self.running || self.active.is_some(), egui::Button::new("Stop queue")).clicked() { self.cancel(true); }
                if ui.add_enabled(!self.running && self.active.is_none(), egui::Button::new("Clear finished")).clicked() { self.items.retain(|i| matches!(i.status, QueueStatus::Pending | QueueStatus::Downloading)); }
                if ui.add_enabled(!self.running && self.active.is_none(), egui::Button::new("Clear all")).clicked() { self.items.clear(); }
            });
            if let Some(started) = self.started_at {
                let elapsed = started.elapsed().as_secs();
                let eta = if self.pages_completed > 0 && self.pages_total > self.pages_completed {
                    let remaining = elapsed.saturating_mul((self.pages_total - self.pages_completed) as u64) / self.pages_completed as u64;
                    format!(" · estimated {remaining}s remaining")
                } else { String::new() };
                ui.horizontal(|ui| { ui.label(format!("Connect → Load pages → Export PDF → Merge  ·  {}  ·  {}s elapsed{}", self.current_stage, elapsed, eta)); });
            }
            if !self.recents.is_empty() {
                ui.separator(); ui.horizontal(|ui| { ui.heading("Recent downloads"); if ui.button("Open latest PDF").clicked() { Self::open_path(std::path::Path::new(&self.recents[0].path), false); } });
                for recent in &self.recents { ui.horizontal(|ui| {
                    ui.label(&recent.title);
                    if ui.button("Open").clicked() { Self::open_path(std::path::Path::new(&recent.path), false); }
                    if ui.button("Folder").clicked() { Self::open_path(std::path::Path::new(&recent.path), true); }
                }); }
            }
            ui.separator(); ui.horizontal(|ui| { ui.label("Activity log"); if ui.button("Copy log").clicked() { ui.output_mut(|o| o.copied_text = self.log.clone()); } if ui.button("Clear log").clicked() { self.log.clear(); } if ui.button("Diagnostics folder").clicked() { Self::open_path(&state::config_dir(), true); } });
            egui::ScrollArea::vertical().max_height(150.0).show(ui, |ui| { ui.add(egui::TextEdit::multiline(&mut self.log).desired_width(f32::INFINITY).interactive(false)); });
          });
        });
        self.save_if_changed();
    }
}

fn main() -> eframe::Result<()> {
    let options = eframe::NativeOptions { viewport: egui::ViewportBuilder::default().with_drag_and_drop(true).with_inner_size([900.0, 800.0]), ..Default::default() };
    eframe::run_native("Scribd Downloader", options, Box::new(|cc| {
        let app = App::default();
        if app.appearance == "Light" { cc.egui_ctx.set_visuals(egui::Visuals::light()); }
        if app.appearance == "Dark" { cc.egui_ctx.set_visuals(egui::Visuals::dark()); }
        Box::new(app)
    }))
}
