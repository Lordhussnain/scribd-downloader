#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use eframe::egui;
use scribd_core::{extract_scribd_urls, import, output::{choose_output, ConflictPolicy, OutputDecision}, queue::{QueueItem, QueueStatus}};
use scribd_core::download::{self, DownloadError, DownloadOptions, Progress};
use std::{path::PathBuf, process::Command, sync::{Arc, atomic::{AtomicBool, Ordering}, mpsc::{self, Receiver}}, thread, time::Duration};

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
}

impl Default for App {
    fn default() -> Self {
        let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
        let downloads = home.join("Downloads");
        let folder = if downloads.is_dir() { downloads } else { home };
        Self { text: String::new(), folder: folder.to_string_lossy().into_owned(), items: Vec::new(), active: None, running: false, policy: ConflictPolicy::Copy, batch: 8, page_timeout: 120, export_timeout: 600, show_browser: false, settings_open: false, log: String::new() }
    }
}

impl App {
    fn add(&mut self, documents: Vec<scribd_core::ScribdDocument>) {
        let mut known: std::collections::HashSet<String> = self.items.iter().filter(|i| matches!(i.status, QueueStatus::Pending | QueueStatus::Downloading)).map(|i| i.document_id.clone()).collect();
        for doc in documents { if known.insert(doc.document_id.clone()) { self.items.push(QueueItem::from(&doc)); } }
    }

    fn start_next(&mut self) {
        if !self.running || self.active.is_some() { return; }
        let Some(index) = self.items.iter().position(|i| i.status == QueueStatus::Pending) else { self.running = false; return };
        let item = &mut self.items[index];
        let destination = PathBuf::from(&self.folder).join(&item.filename);
        let path = match choose_output(&destination, self.policy) {
            OutputDecision::Skip { path } => { item.status = QueueStatus::Skipped; item.output_path = Some(path); return; }
            OutputDecision::Download { path, .. } => path,
        };
        let url = item.source_url.clone();
        let output = path.clone();
        let show_browser = self.show_browser;
        let page_timeout = Duration::from_secs(self.page_timeout as u64);
        let command_timeout = Duration::from_secs(self.export_timeout as u64);
        let options = DownloadOptions { batch_size: self.batch as usize, page_load_timeout: page_timeout, ..Default::default() };
        let cancel = Arc::new(AtomicBool::new(false));
        let worker_cancel = cancel.clone();
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            let result = match scribd_browser::Browser::new(show_browser, page_timeout, command_timeout) {
                Ok(mut browser) => download::download(&mut browser, &url, &output, &options, &worker_cancel, &mut |p| { let _ = tx.send(WorkerEvent::Progress(p)); }),
                Err(error) => Err(DownloadError::Backend(error)),
            };
            let _ = tx.send(WorkerEvent::Finished(result));
        });
        item.status = QueueStatus::Downloading; item.activity = "Starting Chrome…".into(); item.percent = 0; item.detail.clear(); item.output_path = Some(path);
        self.active = Some(Active { cancel, events: rx, index, result_received: false });
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
                        item.activity = progress.message.clone();
                        self.log.push_str(&format!("{}\n", progress.message));
                        if progress.total > 0 { item.percent = (progress.current.saturating_mul(100) / progress.total).min(100) as u32; }
                    }
                    WorkerEvent::Finished(result) => {
                        active.result_received = true;
                        item.activity.clear();
                        match result {
                            Ok(path) => { item.status = QueueStatus::Done; item.percent = 100; item.output_path = Some(path); }
                            Err(DownloadError::Cancelled) => { item.status = QueueStatus::Cancelled; }
                            Err(error) => { item.status = QueueStatus::Failed; item.detail = format!("{error:?}"); self.log.push_str(&format!("{}\n", item.detail)); }
                        }
                    }
                }
            }
            if disconnected {
                let item = &mut self.items[active.index];
                if !active.result_received { item.status = QueueStatus::Failed; item.detail = "Download worker stopped unexpectedly.".into(); }
                self.active = None;
            }
        }
        self.start_next();
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll();
        if ctx.input(|i| i.viewport().close_requested()) && self.active.is_some() {
            self.cancel(true);
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            self.log.push_str("Stopping current download. Close the window after it finishes.\n");
        }
        if self.active.is_some() || self.running { ctx.request_repaint_after(std::time::Duration::from_millis(100)); }
        egui::TopBottomPanel::top("header").show(ctx, |ui| { ui.horizontal(|ui| { ui.heading("Scribd Downloader — Rust preview"); if ui.button("Settings").clicked() { self.settings_open = true; } }); });
        if self.settings_open {
            egui::Window::new("Settings").open(&mut self.settings_open).show(ctx, |ui| {
                ui.label("Changes apply to the next document. Settings are not yet saved.");
                ui.horizontal(|ui| { ui.label("Pages loaded at once:"); for n in [4, 8, 16] { ui.selectable_value(&mut self.batch, n, n.to_string()); } });
                ui.add(egui::Slider::new(&mut self.page_timeout, 10..=600).text("Page timeout (seconds)"));
                ui.add(egui::Slider::new(&mut self.export_timeout, 30..=3600).text("Export timeout (seconds)"));
                ui.checkbox(&mut self.show_browser, "Show Chrome window");
            });
        }
        egui::CentralPanel::default().show(ctx, |ui| {
            ui.label("Add Scribd links (Ctrl+V to paste)");
            ui.add(egui::TextEdit::multiline(&mut self.text).desired_rows(3).desired_width(f32::INFINITY));
            ui.horizontal(|ui| {
                if ui.button("Add to queue").clicked() { let docs = extract_scribd_urls(&self.text); self.add(docs); self.text.clear(); }
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
                            if item.status == QueueStatus::Pending && self.active.is_none() && ui.button("Remove").clicked() { remove = Some(index); }
                            if matches!(item.status, QueueStatus::Failed | QueueStatus::Cancelled) && ui.button("Retry").clicked() { item.status = QueueStatus::Pending; item.detail.clear(); }
                            if item.status == QueueStatus::Done && ui.button("Open").clicked() { if let Some(path) = &item.output_path { #[cfg(windows)] { let _ = Command::new("explorer").arg(path).spawn(); } } }
                        });
                        if item.status == QueueStatus::Pending && !self.running && self.active.is_none() {
                            ui.horizontal(|ui| { ui.label("Filename:"); ui.text_edit_singleline(&mut item.filename); });
                        } else { ui.label(scribd_core::queue::queue_item_subtext(item)); }
                        if item.status == QueueStatus::Downloading { ui.add(egui::ProgressBar::new(item.percent as f32 / 100.0).show_percentage()); }
                    });
                }
            });
            if let Some(index) = remove { self.items.remove(index); }
            ui.horizontal(|ui| {
                if ui.add_enabled(!self.running && self.active.is_none(), egui::Button::new("Start queue")).clicked() {
                    // Never let an edited filename escape the selected output directory.
                    for item in &mut self.items { if item.status == QueueStatus::Pending { item.filename = format!("{}.pdf", scribd_core::sanitize_filename(&item.filename, "scribd-document")); } }
                    self.running = true;
                }
                if ui.add_enabled(self.active.is_some(), egui::Button::new("Skip current document")).clicked() { self.cancel(false); }
                if ui.add_enabled(self.running || self.active.is_some(), egui::Button::new("Stop queue")).clicked() { self.cancel(true); }
                if ui.add_enabled(!self.running && self.active.is_none(), egui::Button::new("Clear finished")).clicked() { self.items.retain(|i| matches!(i.status, QueueStatus::Pending | QueueStatus::Downloading)); }
                if ui.add_enabled(!self.running && self.active.is_none(), egui::Button::new("Clear all")).clicked() { self.items.clear(); }
            });
            ui.separator(); ui.label("Activity log");
            egui::ScrollArea::vertical().max_height(150.0).show(ui, |ui| { ui.add(egui::TextEdit::multiline(&mut self.log).desired_width(f32::INFINITY).interactive(false)); });
        });
    }
}

fn main() -> eframe::Result<()> {
    eframe::run_native("Scribd Downloader", eframe::NativeOptions::default(), Box::new(|_cc| Box::<App>::default()))
}
