//! The `svg-converter` engine — batch SVG → EPS/PNG via Inkscape, ported
//! from `svg2eps.bat` + the three node helpers:
//!
//! - **EPS**: Inkscape export → if the tight `%%BoundingBox` artwork is < 4MP
//!   (Shutterstock minimum), scale the SVG up (target 5MP) and re-render
//!   (mirrors `XIX-EpsScale.js`) → patch the bbox with 7% padding (mirrors
//!   `XIX-EPS-Fix.js`). Zero-byte output = failure.
//! - **PNG**: export width computed so W×H ≈ 24.9MP for any aspect ratio
//!   (mirrors `XIX-PngSize.js`), `--export-area-page`.
//!
//! Inkscape is located in this order: bundled portable copy (set at startup
//! from the app resource dir), PATH, then the standard Windows install dirs.
//! The path is cached after the first lookup.

use crate::engines::{Engine, EngineError, EngineOptions, OptionDef, OptionKind};
use crate::net::http::{BoxFuture, ProgressSink};
use regex::Regex;
use std::env;
use std::path::{Path, PathBuf};
use std::process::Command;
use parking_lot::Mutex;
use std::sync::OnceLock;

/// Hanya satu Inkscape yang boleh berjalan pada satu waktu — Inkscape / GTK
/// di Windows crash dengan `Gio::DBus::Error` kalau dua proses paralel.
static INKSCAPE_SERIAL: std::sync::LazyLock<Mutex<()>> =
    std::sync::LazyLock::new(|| Mutex::new(()));
/// Portable Inkscape bundled with the installer (set once at app startup via
/// [`set_bundled_inkscape`] from the resource dir). `None`/unset → fall back
/// to a system install.
static BUNDLED_INKSCAPE: OnceLock<Option<PathBuf>> = OnceLock::new();

/// Point the engine at the bundled portable Inkscape executable (or `None`
/// when the bundle is missing, e.g. dev without running `fetch-inkscape.bat`).
pub fn set_bundled_inkscape(exe: Option<PathBuf>) {
    let _ = BUNDLED_INKSCAPE.set(exe);
}

fn bundled_inkscape() -> Option<String> {
    BUNDLED_INKSCAPE
        .get()
        .and_then(|o| o.as_ref())
        .filter(|p| p.exists())
        .map(|p| p.to_string_lossy().into_owned())
}

/// Artwork target ≥ this many megapixels avoids the Shutterstock undersize
/// check failing (mirrors XIX-EpsScale: scale to 5MP for margin).
const EPS_TARGET_MP: f64 = 5.0;
/// Artwork below this many megapixels is re-rendered scaled up.
const EPS_MIN_MP: f64 = 4.0;
/// Padding ratio for the patched EPS bbox (mirrors XIX-EPS-Fix).
const EPS_PAD: f64 = 0.07;
/// PNG export keeps W×H at or below this (Vecteezy max 25MP).
const PNG_MAX_PIXELS: f64 = 24_900_000.0;
/// Square fallback export width when the SVG has no viewBox/dimensions.
const PNG_FALLBACK_WIDTH: u32 = 4900;

pub struct SvgConverterEngine {
    inkscape: Mutex<Option<String>>,
}

impl SvgConverterEngine {
    pub fn new() -> Self {
        SvgConverterEngine {
            inkscape: Mutex::new(None),
        }
    }

    /// Test hook: pin a specific inkscape executable (real path or bogus).
    pub fn new_with(inkscape: impl Into<String>) -> Self {
        SvgConverterEngine {
            inkscape: Mutex::new(Some(inkscape.into())),
        }
    }

    fn format(&self, opts: &EngineOptions) -> String {
        opts.get("format")
            .and_then(|v| v.as_str())
            .unwrap_or("eps")
            .to_string()
    }

    fn suffix(&self, opts: &EngineOptions) -> String {
        opts.get("suffix").and_then(|v| v.as_str()).unwrap_or("").to_string()
    }

    fn skip_existing(&self, opts: &EngineOptions) -> bool {
        opts.get("skip_existing").and_then(|v| v.as_bool()).unwrap_or(false)
    }

    /// Locate inkscape (PATH first, then standard Windows install dirs) and
    /// cache the result for the rest of the run.
    fn get_inkscape(&self) -> Result<String, EngineError> {
        {
            let cached = self.inkscape.lock();
            if let Some(p) = cached.as_ref() {
                return Ok(p.clone());
            }
        }
        let found = Self::find_inkscape();
        let path = found.ok_or_else(|| {
            EngineError::Other(
                "Inkscape tidak ditemukan (bundled ataupun terinstall). ".into(),
            )
        })?;
        *self.inkscape.lock() = Some(path.clone());
        Ok(path)
    }

    fn find_inkscape() -> Option<String> {
        // Bundled portable copy first: deterministic version, no install
        // needed on the user's machine.
        if let Some(b) = bundled_inkscape() {
            return Some(b);
        }
        if Command::new("inkscape")
            .arg("--version")
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
        {
            return Some("inkscape".into());
        }
        let dirs = [
            env::var("ProgramFiles").ok(),
            env::var("ProgramFiles(x86)").ok(),
            env::var("LOCALAPPDATA").ok(),
        ];
        for d in dirs.into_iter().flatten() {
            let p = PathBuf::from(d).join("Inkscape").join("bin").join("inkscape.exe");
            if p.exists() {
                return Some(p.to_string_lossy().into_owned());
            }
        }
        None
    }

    /// Kirim satu tick progress bila ada listener (engine svg-converter
    /// sinkron, jadi UI bergantung pada tick buatan per-tahap agar baris
    /// "processing" (jam pasir) terlihat hidup saat file dikerjakan).
    fn tick(progress: &Option<&ProgressSink>, pct: u8) {
        if let Some(tx) = progress {
            let _ = tx.send(pct);
        }
    }

    async fn run_inkscape(
        inkscape: String,
        input: PathBuf,
        args: Vec<String>,
    ) -> Result<(), EngineError> {
        tokio::task::spawn_blocking(move || {
            let _guard = INKSCAPE_SERIAL.lock();
            let mut cmd = Command::new(&inkscape);
            cmd.arg(&input);
            for a in &args {
                cmd.arg(a);
            }
            let out = cmd
                .output()
                .map_err(|e| EngineError::Other(format!("inkscape exec: {e}")))?;
            if !out.status.success() {
                let stderr = String::from_utf8_lossy(&out.stderr);
                let snippet: String = stderr.chars().take(200).collect();
                return Err(EngineError::Other(format!("inkscape gagal: {snippet}")));
            }
            Ok(())
        })
        .await
        .map_err(|e| EngineError::Other(format!("inkscape join error: {e}")))?
    }
    async fn render_eps(
        inkscape: &str,
        svg: &Path,
        out: &Path,
        svg_src: &str,
        progress: &Option<&ProgressSink>,
    ) -> Result<(), EngineError> {
        Self::tick(progress, 25);
        Self::run_inkscape(
            inkscape.to_string(),
            svg.to_path_buf(),
            vec![format!("--export-filename={}", out.display())],
        )
        .await?;
        if let Ok(eps) = std::fs::read_to_string(out) {
            if let Some((w, h)) = eps_bbox_size(&eps) {
                let mp = (w * h) as f64 / 1e6;
                if w > 0 && h > 0 && mp < EPS_MIN_MP {
                    Self::tick(progress, 50);
                    let factor = (EPS_TARGET_MP / mp).sqrt();
                    if let Some(tmp_src) = scaled_svg(svg_src, factor) {
                        let tmp = out.with_extension("tmp.svg");
                        if std::fs::write(&tmp, tmp_src).is_ok() {
                            let _ = Self::run_inkscape(
                                inkscape.to_string(),
                                tmp.clone(),
                                vec![format!("--export-filename={}", out.display())],
                            )
                            .await;
                            let _ = std::fs::remove_file(&tmp);
                        }
                    }
                }
            }
        }
        Self::tick(progress, 75);
        if let Ok(eps) = std::fs::read_to_string(out) {
            if let Some(patched) = eps_pad_bbox(&eps) {
                let _ = std::fs::write(out, patched);
            }
        }
        Self::tick(progress, 100);
        if std::fs::metadata(out).map(|m| m.len() == 0).unwrap_or(true) {
            return Err(EngineError::Other(format!(
                "EPS kosong (0 bytes): {}",
                out.display()
            )));
        }
        Ok(())
    }

    async fn render_png(
        inkscape: &str,
        svg: &Path,
        out: &Path,
        svg_src: &str,
        progress: &Option<&ProgressSink>,
    ) -> Result<(), EngineError> {
        Self::tick(progress, 50);
        let w = png_width(svg_src);
        Self::run_inkscape(
            inkscape.to_string(),
            svg.to_path_buf(),
            vec![
                "--export-type=png".into(),
                format!("--export-width={w}"),
                "--export-area-page".into(),
                format!("--export-filename={}", out.display()),
            ],
        )
        .await?;
        Self::tick(progress, 100);
        if std::fs::metadata(out).map(|m| m.len() == 0).unwrap_or(true) {
            return Err(EngineError::Other(format!(
                "PNG kosong (0 bytes): {}",
                out.display()
            )));
        }
        Ok(())
    }

    /// JPG via Inkscape's own exporter is broken on Windows (the Python
    /// raster chain fails on its intermediate file), so render a PNG first
    async fn render_jpg(
        inkscape: &str,
        svg: &Path,
        out: &Path,
        svg_src: &str,
        progress: &Option<&ProgressSink>,
    ) -> Result<(), EngineError> {
        let tmp_png = out.with_extension("tmp.jpg.png");
        Self::tick(progress, 30);
        Self::render_png(inkscape, svg, &tmp_png, svg_src, progress).await?;
        let result = (|| -> Result<(), EngineError> {
            let img = image::load_from_memory(&std::fs::read(&tmp_png)?)
                .map_err(|e| EngineError::Other(format!("decode PNG untuk JPG: {e}")))?;
            let jpg = crate::img::encode_jpeg(&img, 90)
                .map_err(EngineError::Other)?;
            std::fs::write(out, &jpg)?;
            Ok(())
        })();
        let _ = std::fs::remove_file(&tmp_png);
        result?;
        Self::tick(progress, 100);
        if std::fs::metadata(out).map(|m| m.len() == 0).unwrap_or(true) {
            return Err(EngineError::Other(format!(
                "JPG kosong (0 bytes): {}",
                out.display()
            )));
        }
        Ok(())
    }
}

impl Engine for SvgConverterEngine {
    fn id(&self) -> &str {
        "svg-converter"
    }

    fn name(&self) -> &str {
        "SVG Converter"
    }

    fn input_exts(&self) -> &'static [&'static str] {
        &["svg"]
    }

    fn options_schema(&self) -> Vec<OptionDef> {
        vec![
            OptionDef {
                id: "format".into(),
                label: "Format".into(),
                kind: OptionKind::Select(vec![
                    ("eps".into(), "EPS".into()),
                    ("png".into(), "PNG ~25MP".into()),
                    ("jpg".into(), "JPG q90".into()),
                    ("both".into(), "EPS + PNG".into()),
                    ("all".into(), "EPS + JPG + PNG".into()),
                ]),
                default: serde_json::json!("eps"),
            },
            OptionDef {
                id: "skip_existing".into(),
                label: "Skip existing".into(),
                kind: OptionKind::Bool,
                default: serde_json::json!(false),
            },
        ]
    }

    fn process<'a>(
        &'a self,
        file: &'a Path,
        out_dir: &'a Path,
        opts: &'a EngineOptions,
        progress: Option<&'a crate::net::http::ProgressSink>,
    ) -> BoxFuture<'a, Result<Vec<u8>, EngineError>> {
        Box::pin(async move {
            let base = file.file_stem().and_then(|s| s.to_str()).unwrap_or("out");
            let fmt = self.format(opts);
            let suffix = self.suffix(opts);
            let inkscape = self.get_inkscape()?;
            let svg_src = std::fs::read_to_string(file)?;
            let mut outputs: Vec<PathBuf> = Vec::new();
            let prog = &progress;

            if fmt == "eps" || fmt == "both" || fmt == "all" {
                let out = out_dir.join(format!("{base}{suffix}.eps"));
                if !(self.skip_existing(opts) && out.exists()) {
                    Self::render_eps(&inkscape, file, &out, &svg_src, prog).await?;
                }
                outputs.push(out);
            }
            if fmt == "png" || fmt == "both" || fmt == "all" {
                let out = out_dir.join(format!("{base}{suffix}.png"));
                if !(self.skip_existing(opts) && out.exists()) {
                    Self::render_png(&inkscape, file, &out, &svg_src, prog).await?;
                }
                outputs.push(out);
            }
            if fmt == "jpg" || fmt == "all" {
                let out = out_dir.join(format!("{base}{suffix}.jpg"));
                if !(self.skip_existing(opts) && out.exists()) {
                    Self::render_jpg(&inkscape, file, &out, &svg_src, prog).await?;
                }
                outputs.push(out);
            }

            let last = outputs
                .last()
                .ok_or_else(|| EngineError::Other("format output tidak dipilih".into()))?;
            Ok(std::fs::read(last)?)
        })
    }

    fn output_name(&self, file: &Path, opts: &EngineOptions) -> String {
        let base = file.file_stem().and_then(|s| s.to_str()).unwrap_or("out");
        let fmt = self.format(opts);
        let ext = match fmt.as_str() {
            "png" => "png",
            "jpg" => "jpg",
            _ => "eps", // eps / both / all → EPS adalah output utama
        };
        format!("{base}{}.{ext}", self.suffix(opts))
    }

}

// ==================== helpers (ported from the node scripts) ====================

/// `%%BoundingBox: llx lly urx ury` → (width, height) in pt, or `None`.
fn eps_bbox_size(eps: &str) -> Option<(i64, i64)> {
    let re = Regex::new(r"(?m)^%%BoundingBox: (-?\d+) (-?\d+) (-?\d+) (-?\d+)$").ok()?;
    let c = re.captures(eps)?;
    let llx: i64 = c[1].parse().ok()?;
    let lly: i64 = c[2].parse().ok()?;
    let urx: i64 = c[3].parse().ok()?;
    let ury: i64 = c[4].parse().ok()?;
    Some((urx - llx, ury - lly))
}

/// Replace the EPS `%%BoundingBox` with a 7%-padded version (XIX-EPS-Fix).
fn eps_pad_bbox(eps: &str) -> Option<String> {
    let re = Regex::new(r"(?m)^%%BoundingBox: (-?\d+) (-?\d+) (-?\d+) (-?\d+)$").ok()?;
    let c = re.captures(eps)?;
    let llx: i64 = c[1].parse().ok()?;
    let lly: i64 = c[2].parse().ok()?;
    let urx: i64 = c[3].parse().ok()?;
    let ury: i64 = c[4].parse().ok()?;
    let w = urx - llx;
    let h = ury - lly;
    if w <= 0 || h <= 0 {
        return None;
    }
    let llx2 = (llx as f64 - w as f64 * EPS_PAD).floor() as i64;
    let lly2 = (lly as f64 - h as f64 * EPS_PAD).floor() as i64;
    let urx2 = (urx as f64 + w as f64 * EPS_PAD).ceil() as i64;
    let ury2 = (ury as f64 + h as f64 * EPS_PAD).ceil() as i64;
    Some(
        re.replace(
            eps,
            format!("%%BoundingBox: {llx2} {lly2} {urx2} {ury2}"),
        )
        .into_owned(),
    )
}

/// SVG open-tag helper regexes.
fn svg_tag_regexes() -> (Regex, Regex, Regex) {
    (
        Regex::new(r#"viewBox\s*=\s*"([-\d.]+)[ ,]+([-\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)""#).unwrap(),
        Regex::new(r#"\swidth\s*=\s*"([\d.]+)"#).unwrap(),
        Regex::new(r#"\sheight\s*=\s*"([\d.]+)"#).unwrap(),
    )
}

/// Return a copy of the SVG with width/height multiplied by `factor` (and a
/// viewBox injected when missing) — used to re-render undersized EPS artwork
/// at ≥5MP (XIX-EpsScale).
fn scaled_svg(svg: &str, factor: f64) -> Option<String> {
    let open_re = Regex::new(r"<svg[^>]*>").ok()?;
    let tag = open_re.find(svg)?.as_str();
    let (vb_re, w_re, h_re) = svg_tag_regexes();
    let (sw, sh): (f64, f64) = if let (Some(mw), Some(mh)) = (w_re.captures(tag), h_re.captures(tag))
    {
        (mw[1].parse().ok()?, mh[1].parse().ok()?)
    } else if let Some(vb) = vb_re.captures(tag) {
        (vb[3].parse().ok()?, vb[4].parse().ok()?)
    } else {
        return None;
    };
    if sw <= 0.0 || sh <= 0.0 {
        return None;
    }
    let mut ntag = tag.to_string();
    if !vb_re.is_match(tag) {
        ntag = ntag.replacen("<svg", &format!(r#"<svg viewBox="0 0 {sw} {sh}""#), 1);
    }
    let strip_w = Regex::new(r#"\swidth\s*=\s*"[^"]*""#).unwrap();
    let strip_h = Regex::new(r#"\sheight\s*=\s*"[^"]*""#).unwrap();
    ntag = strip_w.replace(&ntag, "").into_owned();
    ntag = strip_h.replace(&ntag, "").into_owned();
    let nw = (sw * factor).ceil();
    let nh = (sh * factor).ceil();
    ntag = ntag.replacen("<svg", &format!(r#"<svg width="{nw}" height="{nh}""#), 1);
    Some(svg.replacen(tag, &ntag, 1))
}

/// Export width (px) so W×H stays ≤ ~24.9MP for any aspect ratio, or the
/// 4900 square fallback when the SVG has no dimensions (XIX-PngSize).
fn png_width(svg: &str) -> u32 {
    let (vb_re, w_re, h_re) = svg_tag_regexes();
    let (w, h): (f64, f64) = if let Some(vb) = vb_re.captures(svg) {
        (vb[3].parse().unwrap_or(0.0), vb[4].parse().unwrap_or(0.0))
    } else if let (Some(mw), Some(mh)) = (w_re.captures(svg), h_re.captures(svg)) {
        (mw[1].parse().unwrap_or(0.0), mh[1].parse().unwrap_or(0.0))
    } else {
        (0.0, 0.0)
    };
    if w <= 0.0 || h <= 0.0 {
        return PNG_FALLBACK_WIDTH;
    }
    (PNG_MAX_PIXELS * (w / h)).sqrt().floor() as u32
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engines::EngineOptions;

    fn opts(pairs: &[(&str, serde_json::Value)]) -> EngineOptions {
        pairs.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
    }

    #[test]
    fn eps_bbox_size_parses_tight_box() {
        let eps = "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 1200 800\n%%HiResBoundingBox: ...\n";
        let (w, h) = eps_bbox_size(eps).unwrap();
        assert_eq!((w, h), (1200, 800));
        assert!(eps_bbox_size("no bbox here").is_none());
    }

    #[test]
    fn eps_pad_bbox_adds_7_percent_padding() {
        let eps = "%!PS\n%%BoundingBox: 100 100 1300 900\nrest\n";
        let out = eps_pad_bbox(eps).unwrap();
        // w=1200 h=800 → pad 84/56. 0.07 f64 = 0.07000000000000001 sehingga
        // floor(100 - 84.00000000000001) = 15 — sama persis dengan JS Math.floor.
        assert!(out.contains("%%BoundingBox: 15 43 1384 956"), "got: {out}");
        assert!(out.contains("rest"), "rest of file preserved");
    }

    #[test]
    fn scaled_svg_rewrites_dimensions_keeping_viewbox() {
        let svg = r#"<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400" viewBox="0 0 600 400"><path d="M0 0"/></svg>"#;
        let out = scaled_svg(svg, 2.0).unwrap();
        assert!(out.contains(r#"width="1200" height="800""#), "got: {out}");
        assert!(!out.contains(r#"width="600""#));
        assert!(out.contains(r#"viewBox="0 0 600 400""#));
        assert!(out.contains(r#"<path d="M0 0"/>"#), "content preserved");
    }

    #[test]
    fn scaled_svg_injects_viewbox_when_missing() {
        let svg = r#"<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"></svg>"#;
        let out = scaled_svg(svg, 3.0).unwrap();
        assert!(out.contains(r#"viewBox="0 0 100 50""#));
        assert!(out.contains(r#"width="300" height="150""#));
    }

    #[test]
    fn png_width_targets_24_9mp_and_fallback() {
        // 16:9 → width = sqrt(24900000 * 16/9) = sqrt(44266666) ≈ 6653
        let svg = r#"<svg viewBox="0 0 1920 1080"></svg>"#;
        let w = png_width(svg);
        assert!(w > 6600 && w < 6700, "16:9 width ~6653, got {w}");
        // square → sqrt(24900000) ≈ 4989
        assert_eq!(png_width(r#"<svg viewBox="0 0 1000 1000"></svg>"#), 4989);
        // no dims → fallback
        assert_eq!(png_width("<svg></svg>"), 4900);
    }

    #[test]
    fn output_name_matches_format() {
        let eng = SvgConverterEngine::new_with("/fake/inkscape");
        assert_eq!(eng.output_name(Path::new("a.svg"), &opts(&[])), "a.eps");
        let o = opts(&[("format", serde_json::json!("png"))]);
        assert_eq!(eng.output_name(Path::new("a.svg"), &o), "a.png");
        let oj = opts(&[("format", serde_json::json!("jpg"))]);
        assert_eq!(eng.output_name(Path::new("a.svg"), &oj), "a.jpg");
        let oa = opts(&[("format", serde_json::json!("all"))]);
        assert_eq!(eng.output_name(Path::new("a.svg"), &oa), "a.eps");
        let o2 = opts(&[("suffix", serde_json::json!("-v2"))]);
        assert_eq!(eng.output_name(Path::new("a.svg"), &o2), "a-v2.eps");
    }

    #[tokio::test]
    async fn process_errors_cleanly_when_inkscape_missing() {
        let eng = SvgConverterEngine::new_with("/nonexistent/inkscape");
        let dir = std::env::temp_dir().join("xix-svgconv-missing");
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("a.svg");
        std::fs::write(&file, "<svg></svg>").unwrap();
        let err = eng.process(&file, &dir, &opts(&[]), None).await.unwrap_err();
        assert!(matches!(err, EngineError::Other(_)), "got: {err}");
    }

    #[test]
    fn input_exts_is_svg() {
        let eng = SvgConverterEngine::new_with("/fake/inkscape");
        assert_eq!(eng.input_exts(), &["svg"]);
    }

    #[test]
    fn bundled_inkscape_takes_priority_and_missing_falls_through() {
        // No bundle set → PATH/install-dir logic runs (no inkscape here in
        // tests, so it must come back None rather than crash).
        let none_or_path = SvgConverterEngine::find_inkscape();
        assert!(none_or_path.is_none() || !none_or_path.unwrap().is_empty());
        // Bundle pointing at a real file wins.
        let dir = std::env::temp_dir().join("xix-svg-bundled");
        std::fs::create_dir_all(&dir).unwrap();
        let fake = dir.join("inkscape.exe");
        std::fs::write(&fake, b"x").unwrap();
        set_bundled_inkscape(Some(fake.clone()));
        assert_eq!(
            SvgConverterEngine::find_inkscape().unwrap(),
            fake.to_string_lossy()
        );
        // Bundle removed after set → treated as missing (falls through to a
        // system install; never the removed bundled path).
        std::fs::remove_file(&fake).unwrap();
        let after = SvgConverterEngine::find_inkscape();
        assert_ne!(
            after.as_deref(),
            Some(fake.to_str().unwrap()),
            "bundled path must be skipped once the file is gone, got {after:?}"
        );
    }

    #[tokio::test]
    async fn jpg_render_emits_progress_ticks() {
        // Engine svg-converter sinkron: tick progress buatan per-tahap
        // membuat baris "processing" (jam pasir) terlihat. Tick 25 harus
        // terkirim sebelum exec pertama (yang di sini sengaja gagal).
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<u8>();
        let dir = std::env::temp_dir().join("xix-svg-progress");
        std::fs::create_dir_all(&dir).unwrap();
        let fake = dir.join("inkscape.exe");
        std::fs::write(&fake, b"x").unwrap();
        let svg = dir.join("a.svg");
        std::fs::write(&svg, "<svg></svg>").unwrap();
        let out = dir.join("a.eps");
        let prog = Some(&tx);
        let _ = SvgConverterEngine::render_eps(
            &fake.to_string_lossy(),
            &svg,
            &out,
            "<svg></svg>",
            &prog,
        )
        .await;
        assert_eq!(rx.try_recv().ok(), Some(25), "tick pertama harus 25 sebelum exec");
    }

}
