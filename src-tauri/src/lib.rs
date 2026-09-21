pub mod batch;
pub mod config;
pub mod engines;
pub mod img;
pub mod licensing;
pub mod net;
pub mod secure;
pub mod svg;

use crate::batch::{run_batch, BatchEvent};
use crate::config::{load as config_load, save as config_save, AppConfig};
use crate::engines::{common_batch_options, EngineOptions, OptionDef};
use crate::licensing::{AccessDecision, LicenseStatus, LicensingState};
use parking_lot::Mutex;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use tauri::path::BaseDirectory;
use tauri::{AppHandle, Emitter, Manager, State};

#[derive(Serialize)]
struct EngineInfo {
    id: String,
    name: String,
    options_schema: Vec<OptionDef>,
    input_exts: Vec<String>,
}

#[derive(Serialize)]
struct FileInfo {
    path: String,
    name: String,
    size: u64,
    ext: String,
}

/// Shared batch control. Every start gets fresh controls so Stop can cancel
/// that exact generation without a later reset erasing the request.
#[derive(Default)]
struct BatchState {
    next_generation: AtomicU64,
    active: Mutex<Option<BatchStart>>,
}

#[derive(Clone)]
struct BatchStart {
    generation: u64,
    cancel: Arc<AtomicBool>,
    pause: Arc<AtomicBool>,
}

impl BatchState {
    fn begin_start(&self) -> BatchStart {
        let start = BatchStart {
            generation: self.next_generation.fetch_add(1, Ordering::Relaxed) + 1,
            cancel: Arc::new(AtomicBool::new(false)),
            pause: Arc::new(AtomicBool::new(false)),
        };
        *self.active.lock() = Some(start.clone());
        start
    }

    fn cancel_active(&self) {
        if let Some(start) = self.active.lock().as_ref() {
            start.cancel.store(true, Ordering::Release);
        }
    }

    fn pause_active(&self, paused: bool) {
        if let Some(start) = self.active.lock().as_ref() {
            start.pause.store(paused, Ordering::Release);
        }
    }
}

impl BatchStart {
    fn into_controls(
        self,
        state: &BatchState,
    ) -> Result<(Arc<AtomicBool>, Arc<AtomicBool>), String> {
        let active_generation = state.active.lock().as_ref().map(|active| active.generation);
        if active_generation != Some(self.generation) {
            return Err("batch start was superseded".into());
        }
        if self.cancel.load(Ordering::Acquire) {
            return Err("batch cancelled before processing started".into());
        }
        Ok((self.cancel, self.pause))
    }
}

fn config_path(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|d| d.join("config.json"))
}

/// Scan `dir` for files whose extension is in `exts` (default: images).
fn scan_files(dir: &Path, exts: &[String]) -> Result<Vec<PathBuf>, String> {
    let entries = std::fs::read_dir(dir).map_err(|e| e.to_string())?;
    let mut files = Vec::new();
    for entry in entries.flatten() {
        let p = entry.path();
        if p.is_file()
            && p.extension()
                .and_then(|e| e.to_str())
                .map(|e| exts.iter().any(|x| x.eq_ignore_ascii_case(e)))
                .unwrap_or(false)
        {
            files.push(p);
        }
    }
    files.sort();
    Ok(files)
}

#[tauri::command]
fn list_engines() -> Vec<EngineInfo> {
    engines::registry()
        .into_iter()
        .map(|e| EngineInfo {
            id: e.id().to_string(),
            name: e.name().to_string(),
            options_schema: {
                // opsi mitigasi batch (jeda antar file + retry 403) dipasang
                // di sini sekali, berlaku untuk semua engine.
                let mut s = e.options_schema();
                s.extend(common_batch_options());
                s
            },
            input_exts: e.input_exts().iter().map(|s| s.to_string()).collect(),
        })
        .collect()
}

#[tauri::command]
fn scan_dir(path: String, exts: Option<Vec<String>>) -> Result<Vec<FileInfo>, String> {
    let exts = exts.unwrap_or_else(|| {
        ["jpg", "jpeg", "png", "webp"]
            .iter()
            .map(|s| s.to_string())
            .collect()
    });
    Ok(scan_files(Path::new(&path), &exts)?
        .into_iter()
        .map(|p| {
            let ext = p
                .extension()
                .and_then(|e| e.to_str())
                .unwrap_or("")
                .to_string();
            let size = std::fs::metadata(&p).map(|m| m.len()).unwrap_or(0);
            FileInfo {
                path: p.to_string_lossy().replace('\\', "/"),
                name: p
                    .file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("?")
                    .to_string(),
                size,
                ext,
            }
        })
        .collect())
}

#[tauri::command]
async fn start_batch(
    app: AppHandle,
    files: Vec<String>,
    output: String,
    engine_id: String,
    options: EngineOptions,
    state: State<'_, BatchState>,
    licensing: State<'_, LicensingState>,
) -> Result<(), String> {
    let batch_start = state.begin_start();
    // Validasi dulu secara eager agar id tak dikenal ditolak lewat path
    // Err normal invoke (bukan panic di dalam spawned task).
    if !engines::registry().iter().any(|e| e.id() == engine_id) {
        return Err(format!("engine not found: {engine_id}"));
    }
    let decision = licensing.manager.preflight(&engine_id, 1).await.map_err(|error| error.to_string())?;
    ensure_batch_allowed(&decision)?;
    // Satu engine per worker dibangun dari registry yang sama; engine
    // stateless berbagi perilaku, state rotator tidak balapan antar worker.
    let engine_id_cl = engine_id.clone();
    let make_engines = move |worker: usize| {
        let _ = worker;
        engines::registry()
            .into_iter()
            .find(|e| e.id() == engine_id_cl)
            .expect("engine id tervalidasi di atas — unreachable backstop")
    };
    let files: Vec<PathBuf> = files.into_iter().map(PathBuf::from).collect();
    if files.is_empty() {
        return Err("no files to process".into());
    }
    let out_dir = PathBuf::from(&output);
    std::fs::create_dir_all(&out_dir).map_err(|e| e.to_string())?;

    let (cancel, pause) = batch_start.into_controls(&state)?;
    let total = files.len();
    let emit_app = app.clone();
    let usage_manager = licensing.manager.clone();
    let usage_engine_id = engine_id.clone();
    tauri::async_runtime::spawn(async move {
        let (ok, fail) = run_batch(
            make_engines,
            files,
            &out_dir,
            &options,
            cancel,
            pause,
            move |ev| {
                if let BatchEvent::FileDone { input, output, usage_event_id, .. } = &ev {
                    if let Err(error) = usage_manager.record_success_with_event_id(
                        &usage_engine_id,
                        Path::new(input),
                        Path::new(output),
                        usage_event_id,
                    ) {
                        eprintln!("LICENSE USAGE ERROR: {error}");
                    }
                    let sync_manager = usage_manager.clone();
                    tauri::async_runtime::spawn(async move { let _ = sync_manager.sync_pending_usage().await; });
                }
                let _ = emit_app.emit("batch://event", ev);
            },
        )
        .await;
        let _ = app.emit(
            "batch://done",
            serde_json::json!({ "ok": ok, "fail": fail, "total": total }),
        );
    });
    Ok(())
}

fn ensure_batch_allowed(decision: &AccessDecision) -> Result<(), String> {
    if decision.allowed { Ok(()) } else { Err(decision.message.clone()) }
}

#[tauri::command]
async fn license_status(state: State<'_, LicensingState>) -> Result<LicenseStatus, String> { state.manager.status().await.map_err(|error| error.to_string()) }

#[tauri::command]
async fn license_purchase_url(state: State<'_, LicensingState>) -> Result<String, String> { state.manager.checkout_url().await.map_err(|error| error.to_string()) }

#[tauri::command]
async fn activate_license(state: State<'_, LicensingState>, license_key: String) -> Result<LicenseStatus, String> { state.manager.activate(license_key).await.map_err(|error| error.to_string()) }

#[tauri::command]
async fn refresh_license(state: State<'_, LicensingState>) -> Result<LicenseStatus, String> { state.manager.refresh().await.map_err(|error| error.to_string()) }

#[tauri::command]
async fn license_preflight(state: State<'_, LicensingState>, engine_id: String, requested_files: usize) -> Result<AccessDecision, String> { state.manager.preflight(&engine_id, requested_files).await.map_err(|error| error.to_string()) }

#[tauri::command]
async fn license_sync_usage(state: State<'_, LicensingState>) -> Result<(), String> { state.manager.sync_pending_usage().await.map_err(|error| error.to_string()) }

#[tauri::command]
fn stat_files(files: Vec<String>) -> Vec<u64> {
    files
        .iter()
        .map(|p| std::fs::metadata(p).map(|m| m.len()).unwrap_or(0))
        .collect()
}

#[tauri::command]
fn stop_batch(state: State<'_, BatchState>) {
    state.cancel_active();
}

#[tauri::command]
fn pause_batch(state: State<'_, BatchState>, paused: bool) {
    state.pause_active(paused);
}

#[tauri::command]
fn open_dir(path: String) -> Result<(), String> {
    let p = std::path::Path::new(&path);
    if !p.exists() {
        std::fs::create_dir_all(p).map_err(|e| e.to_string())?;
    }
    std::process::Command::new("explorer")
        .arg(&path)
        .spawn()
        .map_err(|e| format!("cannot open folder: {e}"))?;
    Ok(())
}

#[tauri::command]
fn get_config(app: AppHandle) -> AppConfig {
    match config_path(&app) {
        Some(p) => config_load(&p),
        None => AppConfig::default(),
    }
}

#[tauri::command]
fn save_config(app: AppHandle, cfg: AppConfig) -> Result<(), String> {
    let p = config_path(&app).ok_or("cannot resolve app config dir")?;
    if let Some(parent) = p.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    config_save(&p, &cfg)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            // Point the SVG Converter engine at the bundled portable Inkscape
            // (resource dir in release; repo dir in dev when present).
            let exe = [
                app.path()
                    .resolve(
                        "inkscape-portable/inkscape/bin/inkscape.exe",
                        BaseDirectory::Resource,
                    )
                    .ok(),
                Some(PathBuf::from("inkscape-portable/inkscape/bin/inkscape.exe")),
                Some(PathBuf::from(
                    "src-tauri/inkscape-portable/inkscape/bin/inkscape.exe",
                )),
            ]
            .into_iter()
            .flatten()
            .find(|p| p.exists());
            crate::engines::svg_converter::set_bundled_inkscape(exe);

            let app_data_dir = app.path().app_data_dir()
                .map_err(|error| format!("cannot resolve licensing data directory: {error}"))?;
            app.manage(LicensingState::new(&app_data_dir).map_err(|error| error.to_string())?);
            Ok(())
        })
        .manage(BatchState::default())
        .invoke_handler(tauri::generate_handler![
            list_engines,
            scan_dir,
            stat_files,
            start_batch,
            stop_batch,
            pause_batch,
            open_dir,
            get_config,
            save_config,
            license_status,
            license_purchase_url,
            activate_license,
            refresh_license,
            license_preflight,
            license_sync_usage
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|_, _| {});
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stop_before_processing_cancels_the_same_batch_generation() {
        let state = BatchState::default();
        let start = state.begin_start();

        state.cancel_active();

        assert!(start.into_controls(&state).is_err());
    }
}
