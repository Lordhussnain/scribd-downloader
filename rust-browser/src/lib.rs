//! ChromeDriver-backed Rust downloader, supporting a bundled browser runtime.
pub mod runtime;
use base64::{engine::general_purpose::STANDARD, Engine as _};
use lopdf::{dictionary, Document, Object, ObjectId};
use scribd_core::{download::DownloadBackend, engine::{BatchStatus, PageCountTracker, PageStatus}, parse_scribd_url, scripts};
use serde_json::{json, Value};
use std::{net::TcpListener, path::{Path, PathBuf}, process::{Child, Command, Stdio}, thread, time::{Duration, Instant}};

pub struct Browser {
    driver: Option<Child>, session: Option<String>, port: u16, agent: ureq::Agent,
    temp: tempfile::TempDir, pages: Vec<PathBuf>,
    show_browser: bool, page_timeout: Duration, overwrite: bool, show_driver_console: bool,
}

impl Browser {
    pub fn new(show_browser: bool, page_timeout: Duration, command_timeout: Duration, overwrite: bool) -> Result<Self, String> {
        let temp = tempfile::Builder::new().prefix("scribd-rust-").tempdir().map_err(|e| e.to_string())?;
        let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        drop(listener);
        let agent = ureq::AgentBuilder::new().timeout(command_timeout).build();
        Ok(Self { driver: None, session: None, port, agent, temp, pages: Vec::new(), show_browser, page_timeout, overwrite, show_driver_console: false })
    }
    /// Show the ChromeDriver console for troubleshooting. By default Windows
    /// starts it without a console window. This does not control Chrome itself.
    pub fn with_driver_console(mut self, show: bool) -> Self {
        self.show_driver_console = show;
        self
    }

    fn driver_command(&self, executable: std::ffi::OsString) -> Command {
        let mut command = Command::new(executable);
        command.arg(format!("--port={}", self.port)).stdin(Stdio::null());
        if !self.show_driver_console {
            command.stdout(Stdio::null()).stderr(Stdio::null());
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            // CREATE_NO_WINDOW prevents a console flash even for windowed
            // releases. For diagnostics, explicitly request a new console.
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            const CREATE_NEW_CONSOLE: u32 = 0x0000_0010;
            command.creation_flags(if self.show_driver_console { CREATE_NEW_CONSOLE } else { CREATE_NO_WINDOW });
        }
        command
    }
    fn call(&self, method: &str, route: &str, payload: Option<Value>) -> Result<Value, String> {
        let url = format!("http://127.0.0.1:{}{}", self.port, route);
        let request = self.agent.request(method, &url);
        let response = match payload { Some(data) => request.send_json(data), None => request.call() };
        let response = match response { Ok(r) => r, Err(ureq::Error::Status(_, r)) => r, Err(e) => return Err(format!("ChromeDriver request failed: {e}")) };
        let result: Value = response.into_json().map_err(|e| format!("Invalid ChromeDriver response: {e}"))?;
        let value = &result["value"];
        if let Some(error) = value.get("error").and_then(Value::as_str) { return Err(format!("{error}: {}", value["message"].as_str().unwrap_or(""))); }
        Ok(value.clone())
    }
    fn endpoint(&self, suffix: &str) -> Result<String, String> {
        Ok(format!("/session/{}{}", self.session.as_deref().ok_or("No browser session")?, suffix))
    }
    fn execute(&self, script: &str, args: Value) -> Result<Value, String> {
        self.call("POST", &self.endpoint("/execute/sync")?, Some(json!({"script": script, "args": args})))
    }
    fn cdp(&self, cmd: &str, params: Value) -> Result<Value, String> {
        self.call("POST", &self.endpoint("/goog/cdp/execute")?, Some(json!({"cmd":cmd,"params":params})))
    }
}

impl DownloadBackend for Browser {
    fn open_and_prepare(&mut self, url: &str) -> Result<usize, String> {
        let embed = parse_scribd_url(url).ok_or("Invalid Scribd document URL")?.embed_url;
        let bundled = runtime::bundled_runtime()?;
        let executable = match &bundled {
            Some(pair) => pair.driver.clone().into_os_string(),
            None => std::env::var_os("CHROMEDRIVER").unwrap_or_else(|| "chromedriver".into()),
        };
        let driver = self.driver_command(executable).spawn().map_err(|e| format!("Could not start ChromeDriver: {e}. Install a compatible chromedriver and add it to PATH."))?;
        self.driver = Some(driver);
        let deadline = Instant::now() + Duration::from_secs(15);
        while self.call("GET", "/status", None).is_err() {
            if Instant::now() >= deadline { return Err("ChromeDriver did not start within 15 seconds".into()); }
            if let Some(status) = self.driver.as_mut().unwrap().try_wait().map_err(|e| e.to_string())? { return Err(format!("ChromeDriver exited: {status}")); }
            thread::sleep(Duration::from_millis(200));
        }
        let mut arguments = vec!["--disable-gpu".to_owned(), "--disable-dev-shm-usage".to_owned(), "--hide-scrollbars".to_owned(), "--force-color-profile=srgb".to_owned(), format!("--user-data-dir={}", self.temp.path().join("chrome-profile").display())];
        if !self.show_browser { arguments.push("--headless=new".into()); }
        let mut chrome_options = json!({"args": arguments});
        if let Some(chrome) = bundled.and_then(|pair| pair.chrome) {
            chrome_options["binary"] = json!(chrome.to_string_lossy());
        }
        let session = self.call("POST", "/session", Some(json!({"capabilities":{"alwaysMatch":{"browserName":"chrome","goog:chromeOptions":chrome_options}}})))?;
        self.session = Some(session["sessionId"].as_str().ok_or("ChromeDriver returned no session ID")?.to_owned());
        self.call("POST", &self.endpoint("/timeouts")?, Some(json!({"pageLoad":self.page_timeout.as_millis().min(u64::MAX as u128) as u64,"script":self.page_timeout.as_millis().min(u64::MAX as u128) as u64})))?;
        self.call("POST", &self.endpoint("/url")?, Some(json!({"url":embed})))?;
        let _ = self.execute(scripts::HIDE_COOKIE_DIALOGS, json!([]));
        let mut counts = PageCountTracker::default();
        let deadline = Instant::now() + self.page_timeout;
        let count = loop {
            if Instant::now() >= deadline { return Err("Timed out waiting for document pages. The document may be unavailable or blocked.".into()); }
            if let Ok(snapshot) = self.execute(scripts::PAGE_COUNT_SNAPSHOT, json!([])) {
                let outer = snapshot["outerPages"].as_u64().unwrap_or(0) as usize;
                let manager = snapshot["managerPages"].as_u64().unwrap_or(0) as usize;
                if let Some(count) = counts.observe(outer, manager) { break count; }
            }
            thread::sleep(Duration::from_millis(500));
        };
        self.execute(scripts::PREPARE_PRINT, json!([]))?;
        self.execute(scripts::PRINT_STYLES, json!([]))?;
        Ok(count)
    }
    fn probe_batch(&mut self, pages: &[usize]) -> Result<BatchStatus, String> {
        let value = self.execute(scripts::PROBE_BATCH, json!([pages]))?;
        Ok(BatchStatus { supported: value["supported"].as_bool().unwrap_or(false), total: value["total"].as_u64().unwrap_or(pages.len() as u64) as usize, ready: value["ready"].as_u64().unwrap_or(0) as usize, total_images: value["totalImages"].as_u64().unwrap_or(0) as usize, pending_images: value["pendingImages"].as_u64().unwrap_or(0) as usize, pages: value["pages"].as_array().map(|a| a.iter().map(|p| PageStatus { number: p["pageNum"].as_u64().unwrap_or(0) as usize, ready: p["ready"].as_bool().unwrap_or(false), reason: p["reason"].as_str().unwrap_or("").into() }).collect()).unwrap_or_default() })
    }
    fn print_page(&mut self, page: usize) -> Result<usize, String> {
        let geometry = self.execute(scripts::ISOLATE_PAGE, json!([page - 1]))?;
        let w = geometry["width"].as_f64().filter(|w| *w > 0.0).ok_or_else(|| format!("Page {page} has invalid width"))?;
        let h = geometry["height"].as_f64().filter(|h| *h > 0.0).ok_or_else(|| format!("Page {page} has invalid height"))?;
        self.cdp("Emulation.setEmulatedMedia", json!({"media":"print"}))?;
        let result = self.cdp("Page.printToPDF", json!({"landscape":false,"displayHeaderFooter":false,"printBackground":true,"scale":1,"paperWidth":w/96.0,"paperHeight":h/96.0,"marginTop":0,"marginBottom":0,"marginLeft":0,"marginRight":0,"preferCSSPageSize":true,"pageRanges":"1","transferMode":"ReturnAsBase64"}))?;
        let bytes = STANDARD.decode(result["data"].as_str().ok_or("Chrome returned no PDF data")?).map_err(|e| e.to_string())?;
        let doc = Document::load_mem(&bytes).map_err(|e| format!("Invalid PDF page {page}: {e}"))?;
        let count = doc.get_pages().len();
        if count == 1 { let path = self.temp.path().join(format!("page-{page:08}.pdf")); std::fs::write(&path, bytes).map_err(|e| e.to_string())?; self.pages.push(path); }
        Ok(count)
    }
    fn release_batch(&mut self, pages: &[usize]) -> Result<(), String> { self.execute(scripts::RELEASE_BATCH, json!([pages]))?; let _ = self.cdp("HeapProfiler.collectGarbage", json!({})); Ok(()) }
    fn merge(&mut self, output: &Path) -> Result<PathBuf, String> {
        let mut merged = Document::with_version("1.5");
        let root_pages = merged.new_object_id();
        let mut page_ids = Vec::new();
        for path in &self.pages {
            let mut doc = Document::load(path).map_err(|e| e.to_string())?;
            doc.renumber_objects_with(merged.max_id + 1);
            merged.max_id = doc.max_id;
            let ids: Vec<ObjectId> = doc.get_pages().into_values().collect();
            if ids.len() != 1 { return Err("Expected one page in each temporary PDF".into()); }
            let page_id = ids[0];
            // Page resources and geometry may be inherited from parent nodes.
            let mut page = doc.get_dictionary(page_id).map_err(|e| e.to_string())?.clone();
            let mut parent = page.get(b"Parent").ok().and_then(|o| o.as_reference().ok());
            let mut seen = std::collections::HashSet::new();
            while let Some(id) = parent {
                if !seen.insert(id) { return Err("Cycle in PDF page tree".into()); }
                let dict = doc.get_dictionary(id).map_err(|e| e.to_string())?;
                for key in [b"Resources".as_slice(), b"MediaBox", b"CropBox", b"Rotate"] {
                    if page.get(key).is_err() { if let Ok(value) = dict.get(key) { page.set(key, value.clone()); } }
                }
                parent = dict.get(b"Parent").ok().and_then(|o| o.as_reference().ok());
            }
            page.set("Parent", root_pages);
            doc.objects.insert(page_id, Object::Dictionary(page));
            page_ids.push(page_id);
            merged.objects.extend(doc.objects);
        }
        if page_ids.is_empty() { return Err("No PDF pages to merge".into()); }
        merged.objects.insert(root_pages, Object::Dictionary(dictionary! { "Type" => "Pages", "Count" => page_ids.len() as i64, "Kids" => page_ids.into_iter().map(Object::Reference).collect::<Vec<_>>() }));
        let catalog = merged.add_object(dictionary! { "Type" => "Catalog", "Pages" => root_pages });
        merged.trailer.set("Root", catalog);
        let parent = output.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or_else(|| Path::new("."));
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
        merged.save_to(file.as_file_mut()).map_err(|e| e.to_string())?;
        if self.overwrite {
            file.persist(output).map_err(|e| e.to_string())?;
        } else {
            file.persist_noclobber(output).map_err(|e| format!("Could not save without overwriting an existing file: {e}"))?;
        }
        Ok(output.to_owned())
    }
    fn cleanup(&mut self) {
        if let Ok(endpoint) = self.endpoint("") { let _ = self.call("DELETE", &endpoint, None); }
        self.session = None;
        if let Some(mut child) = self.driver.take() { let _ = child.kill(); let _ = child.wait(); }
    }
}
impl Drop for Browser { fn drop(&mut self) { self.cleanup(); } }

#[cfg(test)]
mod tests {
    use super::*;
    fn page(path: &Path, width: i64) {
        let mut doc = Document::with_version("1.5");
        let pages = doc.new_object_id();
        let page = doc.add_object(dictionary! { "Type" => "Page", "Parent" => pages, "MediaBox" => vec![0.into(), 0.into(), width.into(), 200.into()], "Resources" => dictionary! {} });
        doc.objects.insert(pages, Object::Dictionary(dictionary! { "Type" => "Pages", "Kids" => vec![Object::Reference(page)], "Count" => 1 }));
        let catalog = doc.add_object(dictionary! { "Type" => "Catalog", "Pages" => pages });
        doc.trailer.set("Root", catalog);
        doc.save(path).unwrap();
    }
    #[test]
    fn merge_produces_ordered_pages_and_preserves_existing_on_error() {
        let dir = tempfile::tempdir().unwrap();
        let first = dir.path().join("1.pdf"); let second = dir.path().join("2.pdf"); let output = dir.path().join("result.pdf");
        page(&first, 100); page(&second, 300);
        let mut browser = Browser::new(false, Duration::from_secs(1), Duration::from_secs(1), true).unwrap();
        browser.pages = vec![first, second];
        browser.merge(&output).unwrap();
        let result = Document::load(&output).unwrap();
        let widths: Vec<i64> = result.get_pages().values().map(|id| result.get_dictionary(*id).unwrap().get(b"MediaBox").unwrap().as_array().unwrap()[2].as_i64().unwrap()).collect();
        assert_eq!(widths, [100, 300]);
        let before = std::fs::read(&output).unwrap();
        browser.pages.push(dir.path().join("missing.pdf"));
        assert!(browser.merge(&output).is_err());
        assert_eq!(std::fs::read(&output).unwrap(), before);
    }
    #[test]
    fn non_overwrite_policy_preserves_existing_file() {
        let dir = tempfile::tempdir().unwrap();
        let source = dir.path().join("source.pdf");
        let output = dir.path().join("output.pdf");
        page(&source, 123);
        std::fs::write(&output, b"original").unwrap();
        let mut browser = Browser::new(false, Duration::from_secs(1), Duration::from_secs(1), false).unwrap();
        browser.pages.push(source);
        assert!(browser.merge(&output).is_err());
        assert_eq!(std::fs::read(&output).unwrap(), b"original");
    }
    #[test]
    fn empty_merge_does_not_create_output() {
        let dir = tempfile::tempdir().unwrap();
        let output = dir.path().join("output.pdf");
        let mut browser = Browser::new(false, Duration::from_secs(1), Duration::from_secs(1), false).unwrap();
        assert!(browser.merge(&output).is_err());
        assert!(!output.exists());
    }
    #[test]
    fn invalid_url_never_launches_a_browser() {
        let mut browser = Browser::new(false, Duration::from_secs(1), Duration::from_secs(1), false).unwrap();
        assert!(browser.open_and_prepare("https://example.com/not-a-document").is_err());
        assert!(browser.driver.is_none());
        assert!(browser.session.is_none());
    }
    #[test]
    fn merge_rejects_invalid_pdf_and_keeps_existing_file() {
        let dir = tempfile::tempdir().unwrap();
        let input = dir.path().join("bad.pdf");
        let output = dir.path().join("output.pdf");
        std::fs::write(&input, b"not a PDF").unwrap();
        std::fs::write(&output, b"original").unwrap();
        let mut browser = Browser::new(false, Duration::from_secs(1), Duration::from_secs(1), true).unwrap();
        browser.pages.push(input);
        assert!(browser.merge(&output).is_err());
        assert_eq!(std::fs::read(&output).unwrap(), b"original");
    }

    fn mock_response(status: &str, body: &str) -> (u16, std::thread::JoinHandle<String>) {
        use std::io::{Read, Write};
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let status = status.to_owned();
        let body = body.to_owned();
        let handle = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
            let mut request = Vec::new();
            let mut buf = [0; 1024];
            while !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
                let n = stream.read(&mut buf).unwrap();
                if n == 0 { break; }
                request.extend_from_slice(&buf[..n]);
            }
            let response = format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
            stream.write_all(response.as_bytes()).unwrap();
            String::from_utf8_lossy(&request).into_owned()
        });
        (port, handle)
    }
    #[test]
    fn webdriver_protocol_reads_status_response() {
        let (port, thread) = mock_response("200 OK", r#"{"value":{"ready":true}}"#);
        let mut browser = Browser::new(false, Duration::from_secs(1), Duration::from_secs(3), false).unwrap();
        browser.port = port;
        let response = browser.call("GET", "/status", None).unwrap();
        assert_eq!(response["ready"], true);
        assert!(thread.join().unwrap().starts_with("GET /status "));
    }
    #[test]
    fn webdriver_protocol_surfaces_structured_errors() {
        let (port, thread) = mock_response("404 Not Found", r#"{"value":{"error":"invalid session id","message":"gone"}}"#);
        let mut browser = Browser::new(false, Duration::from_secs(1), Duration::from_secs(3), false).unwrap();
        browser.port = port;
        let message = browser.call("GET", "/missing", None).unwrap_err();
        assert!(message.contains("invalid session id"));
        assert!(message.contains("gone"));
        assert!(thread.join().unwrap().starts_with("GET /missing "));
    }

    #[test]
    fn driver_console_defaults_hidden_and_is_independent_of_browser_visibility() {
        let browser = Browser::new(true, Duration::from_secs(1), Duration::from_secs(1), false).unwrap();
        assert!(browser.show_browser);
        assert!(!browser.show_driver_console);
        let browser = browser.with_driver_console(true);
        assert!(browser.show_browser);
        assert!(browser.show_driver_console);
        let command = browser.driver_command("chromedriver".into());
        assert_eq!(command.get_program(), std::ffi::OsStr::new("chromedriver"));
        assert!(command.get_args().any(|arg| arg.to_string_lossy().starts_with("--port=")));
    }

}
