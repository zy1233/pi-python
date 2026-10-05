pub mod config;
pub(crate) mod dual_clock;
pub(crate) mod grok_auth_credentials;
pub(crate) mod subprocess;

// The foundation utilities live in `pi-shell-base` (upstream of this
// crate so they build in parallel). Re-exported at the original paths so
// existing `crate::util::…` / `pi_shell::util::…` users compile
// unchanged.
pub use pi_shell_base::util::*;

#[cfg(test)]
mod expand_home_tests {
    

}

#[cfg(test)]
mod is_user_instruction_path_tests {
        

}
