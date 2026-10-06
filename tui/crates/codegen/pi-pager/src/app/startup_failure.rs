//! Startup failures as data. [`StartupFailure::user_report`] is the only place
//! they become the text a reader sees.

mod render;

use std::fmt;
use std::path::PathBuf;
use std::time::Duration;

use pi_telemetry::startup::{AgentKind, PhaseSnapshot};

#[derive(Debug)]
pub struct StartupFailure {
    reason: Reason,
    context: Context,
}

#[derive(Debug)]
enum Reason {
    TimedOut {
        waited: Duration,
        timings: PhaseSnapshot,
    },
    Cancelled,
}

#[derive(Debug)]
pub(crate) struct Context {
    pub(crate) target: AgentKind,
    pub(crate) version: String,
    pub(crate) log_path: PathBuf,
}

impl StartupFailure {
    pub(crate) fn timed_out(context: Context, waited: Duration, timings: PhaseSnapshot) -> Self {
        Self {
            reason: Reason::TimedOut { waited, timings },
            context,
        }
    }

    pub(crate) fn cancelled(context: Context) -> Self {
        Self {
            reason: Reason::Cancelled,
            context,
        }
    }

    pub fn user_report(&self) -> String {
        render::render(self)
    }
}

// One clause: this lands in log fields and `{e:#}` chains, where the full
// report does not belong.
impl fmt::Display for StartupFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.reason {
            Reason::TimedOut { waited, .. } => {
                write!(
                    f,
                    "startup timed out after {}",
                    render::whole_seconds(*waited)
                )
            }
            Reason::Cancelled => write!(f, "startup cancelled before an agent was ready"),
        }
    }
}

impl std::error::Error for StartupFailure {}
