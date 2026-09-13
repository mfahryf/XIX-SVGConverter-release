//! Engine abstraction — every batch tool (vectorize v2, upscaler, remove-bg,
//! …) is a pluggable `Engine`. The desktop UI renders the options schema
//! dynamically, so adding an engine never touches the frontend: register a new
//! struct implementing `Engine` in `registry()`.

pub mod svg_converter;

use serde::Serialize;
use std::collections::HashMap;
use std::fmt;
use std::path::Path;

#[derive(Debug)]
pub enum EngineError {
    Network(String),
    Parse(String),
    RateLimit,
    /// Server-side quota exhausted (svgai HTTP 402) — rotate proxy / stop.
    Quota(String),
    /// Authentication / token rejected (photoroom remove-bg 401) — retry
    /// with a freshly minted anonymous token.
    Auth(String),
    SslPin(String),
    Io(std::io::Error),
    Other(String),
}

impl Clone for EngineError {
    fn clone(&self) -> Self {
        match self {
            EngineError::Network(m) => EngineError::Network(m.clone()),
            EngineError::Parse(m) => EngineError::Parse(m.clone()),
            EngineError::RateLimit => EngineError::RateLimit,
            EngineError::Quota(m) => EngineError::Quota(m.clone()),
            EngineError::Auth(m) => EngineError::Auth(m.clone()),
            EngineError::SslPin(m) => EngineError::SslPin(m.clone()),
            EngineError::Io(e) => EngineError::Io(std::io::Error::new(e.kind(), e.to_string())),
            EngineError::Other(m) => EngineError::Other(m.clone()),
        }
    }
}

impl fmt::Display for EngineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EngineError::Network(m) => write!(f, "network error: {m}"),
            EngineError::Parse(m) => write!(f, "parse error: {m}"),
            EngineError::RateLimit => write!(f, "rate limit reached (429)"),
            EngineError::Quota(m) => write!(f, "quota exhausted: {m}"),
            EngineError::Auth(m) => write!(f, "auth error: {m}"),
            EngineError::SslPin(m) => write!(f, "certificate pin mismatch: {m}"),
            EngineError::Io(e) => write!(f, "io error: {e}"),
            EngineError::Other(m) => write!(f, "{m}"),
        }
    }
}

impl std::error::Error for EngineError {}

impl From<std::io::Error> for EngineError {
    fn from(e: std::io::Error) -> Self {
        EngineError::Io(e)
    }
}

/// Shape of one UI control in the options panel.
#[derive(Clone, Serialize)]
pub enum OptionKind {
    /// Dropdown: (value, label) pairs.
    Select(Vec<(String, String)>),
    Number { min: f64, max: f64, step: f64 },
    Bool,
    Text,
}

#[derive(Clone, Serialize)]
pub struct OptionDef {
    pub id: String,
    pub label: String,
    pub kind: OptionKind,
    pub default: serde_json::Value,
}

pub type EngineOptions = HashMap<String, serde_json::Value>;

/// Opsi mitigasi batch yang berlaku untuk semua engine (dipasang oleh
/// `list_engines` di lib.rs sehingga muncul di panel ADV tiap engine):
/// - `batch_delay`: jeda antar file (detik) — menghormati rate limit svg.new
///   / photoroom yang mem-flag IP setelah N request beruntun (403).
/// - `retry_403`: retry sekali dengan backoff saat server menolak 403.
/// - `concurrency`: jumlah worker paralel (1..=8, default 3); gate pacing
///   global tetap membatasi satu START per `batch_delay` detik.
pub fn common_batch_options() -> Vec<OptionDef> {
    vec![
        OptionDef {
            id: "batch_delay".into(),
            label: "Delay".into(),
            kind: OptionKind::Number { min: 0.0, max: 60.0, step: 1.0 },
            default: serde_json::json!(3),
        },
        OptionDef {
            id: "retry_403".into(),
            label: "Retry saat 403".into(),
            kind: OptionKind::Bool,
            default: serde_json::json!(true),
        },
        OptionDef {
            id: "concurrency".into(),
            label: "Concurrency".into(),
            kind: OptionKind::Number { min: 1.0, max: 8.0, step: 1.0 },
            default: serde_json::json!(3),
        },
    ]
}

/// True jika error adalah HTTP 403 dari server (format Network kita selalu
/// `HTTP {status}: ...`) — dipakai batch loop untuk retry dengan backoff.
pub fn is_http_403(e: &EngineError) -> bool {
    matches!(e, EngineError::Network(m) if m.contains("HTTP 403"))
}

/// True jika error adalah parse "response bukan SVG" — svgai/svg.new balas
/// 200 tapi body bukan SVG. Lewat proxy publik ini biasanya block page yang
/// disisipkan proxy itu sendiri, jadi sinyal kuat proxy buruk (dipakai untuk
/// blacklist + rotate).
pub fn is_not_svg_parse_error(e: &EngineError) -> bool {
    matches!(e, EngineError::Parse(m) if m.contains("response bukan SVG"))
}

pub trait Engine: Send + Sync {
    fn id(&self) -> &str;
    fn name(&self) -> &str;
    fn options_schema(&self) -> Vec<OptionDef>;
    /// File extensions this engine accepts as input (used by the UI file
    /// picker / folder scan). Default: raster images; SVG converter overrides.
    fn input_exts(&self) -> &'static [&'static str] {
        &["jpg", "jpeg", "png", "webp"]
    }
    /// Output file name for one input (used by the batch loop for progress
    /// events — the engine owns the naming scheme, e.g. `{stem}-v2.svg`).
    fn output_name(&self, file: &Path, opts: &EngineOptions) -> String;
    /// Process one input file into output bytes. Async because engines make
    /// network calls; the batch loop runs inside a Tauri async command.
    /// `progress` (v2 only) receives live percent ticks from the SSE stream;
    /// other engines ignore it.
    fn process<'a>(
        &'a self,
        file: &'a Path,
        out_dir: &'a Path,
        opts: &'a EngineOptions,
        progress: Option<&'a crate::net::http::ProgressSink>,
    ) -> crate::net::http::BoxFuture<'a, Result<Vec<u8>, EngineError>>;
}

/// All registered engines (v2 first = default in the UI dropdown).
pub fn registry() -> Vec<Box<dyn Engine>> {
    vec![Box::new(crate::engines::svg_converter::SvgConverterEngine::new())]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_error_display_readable() {
        let e = EngineError::RateLimit;
        assert!(e.to_string().contains("rate"));
    }

    #[test]
    fn option_def_serializes() {
        let def = OptionDef {
            id: "format".into(),
            label: "Format".into(),
            kind: OptionKind::Select(vec![("svg".into(), "SVG".into())]),
            default: serde_json::json!("svg"),
        };
        let v = serde_json::to_value(def).unwrap();
        assert_eq!(v["id"], "format");
    }

    #[test]
    fn registry_contains_only_svg_converter_engine() {
        let ids: Vec<_> = registry().into_iter().map(|engine| engine.id().to_string()).collect();
        assert_eq!(ids, ["svg-converter"]);
    }
}
