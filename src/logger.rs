use csv::Writer;
use chrono::Utc;
use std::fs::{File, OpenOptions};
use std::io::BufWriter;
use std::path::Path;
use std::sync::{Mutex, OnceLock};
use uuid::Uuid;

/// Log entry for CSV logging
pub struct LogEntry {
    pub timestamp: String,
    pub batch_id: String,
    pub operation_id: String,
    pub operation: String,
    pub input: String,
    pub output: String,
    pub status: String,
    pub time_taken_ms: u128,
    pub details: String,
    pub input_checksum: String,
    pub output_size: u64,
}

impl LogEntry {
    pub fn new(operation: &str, input: &str, output: &str) -> Self {
        Self {
            timestamp: Utc::now().to_rfc3339(),
            batch_id: Uuid::new_v4().to_string(),
            operation_id: Uuid::new_v4().to_string(),
            operation: operation.to_string(),
            input: input.to_string(),
            output: output.to_string(),
            status: "STARTED".to_string(),
            time_taken_ms: 0,
            details: String::new(),
            input_checksum: String::new(),
            output_size: 0,
        }
    }
}

/// Logger for CSV-based observability (Phase 9 prep)
pub struct PhotoOpsLogger {
    writer: Option<Writer<BufWriter<File>>>,
    enabled: bool,
    log_path: String,
}

impl PhotoOpsLogger {
    pub fn new() -> Self {
        Self {
            writer: None,
            enabled: false,
            log_path: "logs/photo_ops.csv".to_string(),
        }
    }

    pub fn enable(&mut self, path: Option<&str>) {
        if let Some(p) = path {
            self.log_path = p.to_string();
        }

        if let Some(parent) = Path::new(&self.log_path).parent() {
            std::fs::create_dir_all(parent).ok();
        }

        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_path);

        match file {
            Ok(f) => {
                let writer = Writer::from_writer(BufWriter::new(f));
                self.writer = Some(writer);
                self.enabled = true;
            }
            Err(e) => eprintln!("Failed to open log file: {}", e),
        }
    }

    pub fn disable(&mut self) {
        self.enabled = false;
        if let Some(mut w) = self.writer.take() {
            w.flush().ok();
        }
    }

    pub fn log(&mut self, entry: LogEntry) {
        if !self.enabled {
            return;
        }

        if let Some(writer) = &mut self.writer {
            let _ = writer.serialize((
                entry.timestamp,
                entry.batch_id,
                entry.operation_id,
                entry.operation,
                entry.input,
                entry.output,
                entry.status,
                entry.time_taken_ms,
                entry.details,
                entry.input_checksum,
                entry.output_size,
            ));
            let _ = writer.flush();
        }
    }
}

// Safe global logger: initialised once, guarded by a Mutex for thread safety.
// OnceLock ensures the Mutex itself is created exactly once without any unsafe code.
// Rayon threads and PyO3 calls can both call log_operation concurrently — the
// Mutex serialises access so only one writer is active at a time.
static GLOBAL_LOGGER: OnceLock<Mutex<PhotoOpsLogger>> = OnceLock::new();

fn global_logger() -> &'static Mutex<PhotoOpsLogger> {
    GLOBAL_LOGGER.get_or_init(|| Mutex::new(PhotoOpsLogger::new()))
}

/// Call once from the PyO3 module initialiser. Safe to call multiple times —
/// subsequent calls are no-ops because OnceLock is already set.
pub fn init_logger() {
    let _ = global_logger();
}

pub fn set_logging(enabled: bool, path: Option<&str>) {
    match global_logger().lock() {
        Ok(mut logger) => {
            if enabled {
                logger.enable(path);
            } else {
                logger.disable();
            }
        }
        Err(e) => eprintln!("Logger mutex poisoned in set_logging: {}", e),
    }
}

pub fn log_operation(entry: LogEntry) {
    match global_logger().lock() {
        Ok(mut logger) => logger.log(entry),
        Err(e) => eprintln!("Logger mutex poisoned in log_operation: {}", e),
    }
}