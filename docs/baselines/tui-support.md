# TUI: what the support layer keeps

This file is generated. Do not edit it by hand.

- Command: `python scripts/tui_callgraph.py keep`. Commit: `1a8abee`.
- Run the command again after a change that moves code between modules or crates.
- It replaces the hand-written list of examples in item 0.5 of
  [`PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`](../PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md).

## How to read it

- A *unit* is a module, cut to 2 path segments.
- A unit is *live* when live non-test code refers to it.
  The product crates (`pi-pager-bin`, `pi-pager`, `pi-pager-minimal`, `pi-update`) are live in full.
- The numbers are an estimate. A `use` counts. A type in a signature counts.
  A macro that reaches code without a name does not count.
- A *dead* unit is a candidate for deletion, not proof. Delete it, then run `cargo check`.
- *Check first* counts the dead units that have `impl` blocks for types that other units define.
  Code uses such an impl without a name (`?` calls `impl From<A> for B`), so the compiler may
  need the unit.
- Method, limits and flags: the docstring of `scripts/tui_callgraph.py`.


## Summary

| crate | non-test lines | live | dead | dead units | check first | provider-stack refs |
|---|---|---|---|---|---|---|
| `pi-shell` | 31,191 | 30,183 (97%) | 1,008 (3%) | 7 | 1 | 20 |
| `pi-sampling-types` | 12,043 | 12,043 (100%) | 0 (0%) | 0 | 0 | 1 |
| `pi-tools` | 130,727 | 130,407 (100%) | 320 (0%) | 2 | 1 | 1 |
| `pi-agent` | 22,902 | 22,878 (100%) | 24 (0%) | 1 | 0 | 5 |
| `pi-workspace` | 81,282 | 80,820 (99%) | 462 (1%) | 1 | 0 | 0 |
| `pi-hooks` | 9,026 | 4,687 (52%) | 4,339 (48%) | 5 | 0 | 0 |
| `pi-mcp` | 8,616 | 8,577 (100%) | 39 (0%) | 1 | 0 | 0 |

## Per crate

### `pi-shell`

Non-test code: 31,191 lines in 65 units.

| | units | lines | share |
|---|---|---|---|
| live | 58 | 30,183 | 97% |
| dead | 7 | 1,008 | 3% |

Provider stack (`pi_sampler`, `async_openai`, `pi_sampling_types`, `crate::sampling`): 8 live units, 8 files, 20 references: `agent::config` (1), `agent::model_providers` (1), `extensions::notification` (5), `remote::client` (9), `sampling` (1), `sampling::conversation` (1), `sampling::types` (1), `util::config` (1).

Dead units (nothing live refers to them): `auth::flow` (45), `config::reloader` (77), `session::events` (591), `session::telemetry` (174), `session::unified_list` (103), `upload` (1), `upload::turn` (17).

Check these dead units first. Each has `impl` blocks for types that other units define, and code uses an impl without naming it, so the compiler may need the unit: `session::events` (`GoalClassifierVerdictTelemetry`).

<details><summary>Live units</summary>

| unit | lines | references from live code outside |
|---|---|---|
| `util::config` | 6,706 | 93 |
| `auth::manager` | 3,754 | 0 |
| `agent::config` | 3,153 | 26 |
| `extensions::notification` | 2,195 | 9 |
| `config` | 1,397 | 18 |
| `auth::recovery` | 1,073 | 0 |
| `auth::oidc` | 927 | 0 |
| `agent::config_model_override_parse` | 860 | 0 |
| `agent::folder_trust` | 799 | 1 |
| `managed_config` | 763 | 5 |
| `remote::client` | 748 | 0 |
| `auth::refresh` | 723 | 0 |
| `auth::attribution` | 708 | 0 |
| `auth::credential_provider` | 647 | 1 |
| `auth::storage` | 623 | 0 |
| `auth::config` | 507 | 0 |
| `agent::models` | 457 | 1 |
| `agent::model_providers` | 415 | 0 |
| `auth::model` | 368 | 0 |
| `managed_config::response` | 255 | 0 |
| `util::subprocess` | 237 | 0 |
| `sampling::error` | 234 | 8 |
| `session::acp_types` | 226 | 0 |
| `heap_profile` | 202 | 4 |
| `claude_import` | 201 | 0 |
| `tools::config` | 184 | 0 |
| `auth::error` | 171 | 0 |
| `session` | 161 | 16 |
| `auth::external_auth` | 160 | 0 |
| `auth::token_output` | 155 | 0 |
| `session::title` | 140 | 3 |
| `auth::auth_provider` | 128 | 0 |
| `agent::auth_method` | 104 | 4 |
| `session::feedback` | 104 | 0 |
| `util::grok_auth_credentials` | 78 | 0 |
| `auth::meta` | 63 | 0 |
| `session::result` | 58 | 0 |
| `tier` | 57 | 1 |
| `auth::token_type` | 52 | 0 |
| `tools::todo` | 50 | 1 |
| `util::dual_clock` | 46 | 0 |
| `extensions::prompt_meta` | 43 | 1 |
| `instrumentation` | 40 | 0 |
| `auth` | 34 | 20 |
| `session::pending_interaction` | 31 | 0 |
| `<root>` | 26 | 0 |
| `auth::devbox_login_stub` | 25 | 0 |
| `util` | 16 | 63 |
| `sampling` | 12 | 0 |
| `session::image_normalize` | 12 | 0 |
| `tools` | 11 | 3 |
| `agent` | 9 | 0 |
| `remote` | 9 | 1 |
| `session::handle` | 9 | 0 |
| `session::workflow` | 8 | 0 |
| `sampling::conversation` | 4 | 0 |
| `sampling::types` | 3 | 18 |
| `extensions` | 2 | 0 |

</details>

### `pi-sampling-types`

Non-test code: 12,043 lines in 12 units.

| | units | lines | share |
|---|---|---|---|
| live | 12 | 12,043 | 100% |
| dead | 0 | 0 | 0% |

Provider stack (`pi_sampler`, `async_openai`, `pi_sampling_types`, `crate::sampling`): 1 live units, 1 files, 1 references: `<root>` (1).

Unresolved: 4 references from outside name nothing that is a unit here (an item of the root file, a glob re-export, or a macro): CompactionsRemaining::Dynamic (1), DanglingToolCallReason::HarnessHalted (1), HostedTool::WebSearch (1), HostedTool::XSearch (1).

<details><summary>Live units</summary>

| unit | lines | references from live code outside |
|---|---|---|
| `conversation` | 5,719 | 1 |
| `error` | 1,646 | 0 |
| `types` | 1,530 | 1 |
| `doom_loop` | 563 | 0 |
| `messages` | 503 | 0 |
| `tool_overrides` | 483 | 1 |
| `provider_error` | 458 | 0 |
| `conversation::messages` | 399 | 0 |
| `conversation::responses` | 374 | 0 |
| `conversation::chat_completions` | 314 | 0 |
| `<root>` | 35 | 53 |
| `serde_helpers` | 19 | 0 |

</details>

### `pi-tools`

Non-test code: 130,727 lines in 89 units.

| | units | lines | share |
|---|---|---|---|
| live | 87 | 130,407 | 100% |
| dead | 2 | 320 | 0% |

Provider stack (`pi_sampler`, `async_openai`, `pi_sampling_types`, `crate::sampling`): 1 live units, 1 files, 1 references: `implementations::web_search` (1).

Dead units (nothing live refers to them): `implementations::editor_infra` (276), `types::error` (44).

Check these dead units first. Each has `impl` blocks for types that other units define, and code uses an impl without naming it, so the compiler may need the unit: `types::error` (`ToolError`).

<details><summary>Live units</summary>

| unit | lines | references from live code outside |
|---|---|---|
| `implementations::grok_build` | 48,252 | 83 |
| `computer::local` | 9,788 | 6 |
| `implementations::grok_build_hashline` | 8,402 | 0 |
| `implementations::opencode` | 7,830 | 2 |
| `implementations::lsp` | 5,527 | 11 |
| `registry::types` | 5,050 | 46 |
| `implementations::codex` | 5,015 | 1 |
| `types::skill_discovery_tracker` | 3,675 | 2 |
| `implementations::skills` | 3,202 | 28 |
| `types::output` | 2,760 | 20 |
| `reminders::task_completion` | 2,169 | 0 |
| `implementations::use_tool` | 1,658 | 1 |
| `types::resources` | 1,652 | 8 |
| `implementations::read_file` | 1,408 | 0 |
| `implementations::web_search` | 1,066 | 3 |
| `util::image_validate` | 1,041 | 2 |
| `bridge` | 1,020 | 4 |
| `types::agents_md_tracker` | 959 | 1 |
| `versions` | 954 | 0 |
| `util::base64_images` | 849 | 0 |
| `types::template_renderer` | 844 | 2 |
| `util::truncate` | 820 | 1 |
| `implementations::search_tool` | 785 | 1 |
| `implementations::cursor_rules_on_read` | 767 | 0 |
| `notification::types` | 714 | 11 |
| `implementations::grok_build_concise` | 692 | 1 |
| `persistence` | 685 | 0 |
| `util::mcp_truncate` | 682 | 0 |
| `computer::types` | 640 | 28 |
| `types::compat` | 632 | 15 |
| `util::unicode_confusables` | 612 | 0 |
| `types::schema` | 500 | 0 |
| `media_gen_limits` | 485 | 5 |
| `tool_taxonomy` | 464 | 1 |
| `mcp_elicitation::schema` | 419 | 0 |
| `implementations::memory` | 403 | 4 |
| `util::path_suggestions` | 391 | 0 |
| `notification::handle` | 383 | 0 |
| `util::command_display` | 378 | 0 |
| `implementations::task_output` | 342 | 0 |
| `types::context` | 304 | 2 |
| `util::fs` | 288 | 0 |
| `mcp_elicitation::validate` | 286 | 0 |
| `util::image_compress` | 286 | 0 |
| `types::memory_backend` | 282 | 2 |
| `types::requirements` | 261 | 0 |
| `mcp_elicitation::types` | 255 | 0 |
| `reminders::skill_discovery` | 254 | 0 |
| `util::git_detect` | 249 | 0 |
| `types::description` | 245 | 0 |
| `util::shell_env_policy` | 237 | 0 |
| `registry::proto_convert` | 229 | 2 |
| `types::tool_metadata` | 208 | 1 |
| `types::tool_io` | 206 | 0 |
| `util::hash` | 203 | 0 |
| `reminders` | 189 | 4 |
| `util::remap` | 180 | 0 |
| `util::shared_http` | 179 | 0 |
| `util::serde_base64` | 173 | 0 |
| `util::binary` | 163 | 0 |
| `retry` | 154 | 0 |
| `normalization` | 152 | 0 |
| `types::claude_alias` | 143 | 0 |
| `types::tool` | 140 | 15 |
| `types::params_validation` | 138 | 0 |
| `util::query_tools` | 137 | 0 |
| `gitignore` | 121 | 1 |
| `util::env` | 102 | 1 |
| `types::config_source` | 98 | 3 |
| `types::tool_index` | 80 | 0 |
| `attribution` | 63 | 2 |
| `types::session_mode` | 60 | 0 |
| `computer::task_log` | 54 | 0 |
| `reminders::lsp_diagnostics` | 48 | 0 |
| `types::definition` | 45 | 3 |
| `util` | 45 | 51 |
| `<root>` | 42 | 0 |
| `types` | 37 | 24 |
| `notification` | 34 | 7 |
| `implementations` | 29 | 3 |
| `types::api_key_provider` | 26 | 0 |
| `util::spawn` | 26 | 0 |
| `mcp_elicitation` | 15 | 11 |
| `types::process_manager` | 14 | 0 |
| `computer` | 4 | 0 |
| `registry` | 4 | 0 |
| `util::grok_home` | 4 | 13 |

</details>

### `pi-agent`

Non-test code: 22,902 lines in 31 units.

| | units | lines | share |
|---|---|---|---|
| live | 30 | 22,878 | 100% |
| dead | 1 | 24 | 0% |

Provider stack (`pi_sampler`, `async_openai`, `pi_sampling_types`, `crate::sampling`): 3 live units, 3 files, 5 references: `agent` (1), `builder` (3), `config` (1).

Dead units (nothing live refers to them): `prompt::browser_verification` (24).

<details><summary>Live units</summary>

| unit | lines | references from live code outside |
|---|---|---|
| `config` | 2,699 | 8 |
| `prompt::skills` | 2,686 | 6 |
| `builder` | 2,575 | 0 |
| `plugins::discovery` | 1,865 | 3 |
| `discovery` | 1,603 | 5 |
| `plugins::git_install` | 1,596 | 0 |
| `prompt::context` | 1,436 | 2 |
| `plugins::registry` | 1,241 | 0 |
| `prompt::agents_md` | 1,114 | 2 |
| `plugins::manifest` | 869 | 0 |
| `prompt::template` | 833 | 0 |
| `plugins::local_refresh` | 667 | 0 |
| `plugins::install_registry` | 631 | 0 |
| `prompt::user_message` | 566 | 0 |
| `plugins::hooks_adapter` | 561 | 0 |
| `plugins::marketplace` | 497 | 1 |
| `plugins::trust` | 365 | 1 |
| `agent` | 297 | 0 |
| `repo` | 222 | 3 |
| `prompt::workspace_user` | 162 | 0 |
| `system_reminder` | 127 | 0 |
| `compaction` | 46 | 0 |
| `error` | 41 | 0 |
| `prompt::ignore` | 37 | 0 |
| `plugins` | 32 | 7 |
| `timing` | 31 | 0 |
| `<root>` | 29 | 0 |
| `prompt::subagent_prompts` | 26 | 0 |
| `prompt::prompt_encrypted` | 14 | 0 |
| `prompt` | 10 | 0 |

</details>

### `pi-workspace`

Non-test code: 81,282 lines in 77 units.

| | units | lines | share |
|---|---|---|---|
| live | 76 | 80,820 | 99% |
| dead | 1 | 462 | 1% |

Dead units (nothing live refers to them): `publish` (462).

<details><summary>Live units</summary>

| unit | lines | references from live code outside |
|---|---|---|
| `permission::manager` | 11,250 | 0 |
| `handle` | 4,902 | 0 |
| `session::git` | 3,693 | 4 |
| `permission::auto_mode` | 3,593 | 0 |
| `worktree` | 3,395 | 0 |
| `permission::shell_access` | 3,343 | 0 |
| `activity` | 2,617 | 0 |
| `workspace_ops` | 2,609 | 0 |
| `permission::policy` | 2,268 | 0 |
| `permission::prompter` | 2,161 | 0 |
| `hub` | 1,969 | 0 |
| `permission::bash_command_splitting` | 1,957 | 6 |
| `permission::resolution` | 1,945 | 2 |
| `session::file_state` | 1,813 | 0 |
| `file_system::index` | 1,566 | 0 |
| `status_config` | 1,566 | 0 |
| `trust` | 1,563 | 3 |
| `hub_server` | 1,320 | 0 |
| `permission::state` | 1,245 | 0 |
| `session::tool_config` | 1,244 | 0 |
| `session` | 1,175 | 0 |
| `folder_trust` | 1,171 | 14 |
| `config` | 1,135 | 0 |
| `session::checkpoint` | 1,066 | 0 |
| `permission::exec_risk` | 1,022 | 0 |
| `file_system::client_fs` | 915 | 0 |
| `recovery` | 887 | 0 |
| `permission::types` | 859 | 4 |
| `hub_auth` | 820 | 0 |
| `session::checkpoint_store` | 794 | 0 |
| `session::swap_policy` | 779 | 0 |
| `hub_auth::proactive` | 759 | 0 |
| `export_github` | 753 | 0 |
| `envrc` | 737 | 1 |
| `upload` | 673 | 0 |
| `session::git_gate` | 658 | 0 |
| `restore_fetch` | 641 | 0 |
| `discovery` | 582 | 0 |
| `permission::claude_settings` | 574 | 0 |
| `file_system::ext_fs` | 560 | 0 |
| `file_system::file_tree` | 554 | 0 |
| `path_virtualization` | 550 | 0 |
| `file_system::attach_file` | 546 | 0 |
| `permission::hub_permission` | 537 | 0 |
| `upload::environment` | 442 | 0 |
| `image_capabilities` | 414 | 0 |
| `permission::rules` | 375 | 1 |
| `file_system::codebase_index` | 358 | 0 |
| `file_system::walk` | 344 | 0 |
| `file_system` | 334 | 6 |
| `capability` | 333 | 0 |
| `file_system::content` | 301 | 0 |
| `file_system::git_status` | 294 | 0 |
| `scheduler_liveness` | 284 | 0 |
| `mcp` | 276 | 0 |
| `<root>` | 245 | 0 |
| `session::jj` | 229 | 0 |
| `rpc_envelope` | 226 | 0 |
| `permission::gate_preflight` | 199 | 0 |
| `file_system::acp_fs` | 179 | 0 |
| `file_system::fs` | 152 | 0 |
| `fs_notify` | 144 | 0 |
| `hub_channel` | 127 | 0 |
| `project_config` | 115 | 2 |
| `error` | 103 | 0 |
| `git_odb` | 97 | 0 |
| `permission` | 96 | 34 |
| `file_system::adapter` | 89 | 0 |
| `file_system::jj_status` | 85 | 0 |
| `file_system::local_fs` | 59 | 0 |
| `file_system::mock_fs` | 58 | 0 |
| `telemetry` | 32 | 0 |
| `util` | 32 | 0 |
| `channel` | 19 | 0 |
| `hub_ids` | 10 | 0 |
| `util::ripgrep` | 3 | 0 |

</details>

### `pi-hooks`

Non-test code: 9,026 lines in 13 units.

| | units | lines | share |
|---|---|---|---|
| live | 8 | 4,687 | 52% |
| dead | 5 | 4,339 | 48% |

Dead units (nothing live refers to them): `dispatcher` (1,224), `result` (72), `runner` (176), `runner::command` (1,971), `runner::http` (896).

<details><summary>Live units</summary>

| unit | lines | references from live code outside |
|---|---|---|
| `config` | 1,400 | 5 |
| `event` | 982 | 1 |
| `discovery` | 944 | 8 |
| `env_expand` | 872 | 0 |
| `matcher` | 216 | 0 |
| `trust` | 171 | 2 |
| `error` | 52 | 2 |
| `<root>` | 50 | 0 |

</details>

### `pi-mcp`

Non-test code: 8,616 lines in 11 units.

| | units | lines | share |
|---|---|---|---|
| live | 10 | 8,577 | 100% |
| dead | 1 | 39 | 0% |

Dead units (nothing live refers to them): `wire` (39).

<details><summary>Live units</summary>

| unit | lines | references from live code outside |
|---|---|---|
| `servers` | 4,927 | 14 |
| `oauth` | 758 | 0 |
| `acp_transport` | 736 | 0 |
| `credentials` | 637 | 0 |
| `elicitation` | 507 | 0 |
| `mcp_http_client` | 497 | 0 |
| `liveness` | 354 | 0 |
| `owned_clients` | 94 | 0 |
| `<root>` | 40 | 1 |
| `oauth_config` | 27 | 2 |

</details>
