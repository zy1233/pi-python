# ACP extensions: the register

> Scope: every item on the ACP wire that is not standard ACP, between a client (`zypi`, Zed, or another client) and `pi_agent_cli`. For each item the register gives the name, the direction, the schema, who sends it, who reads it, the tests, and the status. It also says how to add, change or remove an item.
> Decided: 2026-11-05, in round r44 of the plan `docs/PLAN/PLAN-RUST-AGENT-RUNTIME-REMOVAL.md`. After round r43 the user asked to move the register out of `packages/pi-agent-cli/AGENTS.md`. This document is the register that rule 3 of `docs/DESIGN.md` section 8.5 asks for. No item is new and no behaviour changes: each item was on the wire before this document.
> Status: **Written.** It lists eight items (X1 to X8) and points to one more spec. Section 4 says which of the five admission rules each item meets. Section 7 has one question for the owner. The owner answered it on 2026-11-05: rules 4 and 5 apply to new items only.
> Base of the facts: `origin/main` at `2235210`. Python SDK `agent-client-protocol` 0.12.1, with the ACP schema `schema-v1.19.0`. The facts were read in the tree. Nothing was run for this document. The tests that section 3 names run in CI.
> Code: `packages/pi-agent-cli/pi_agent_cli/{agent,events,permission_modes,auth_methods}.py`. In the pager: `tui/crates/codegen/pi-pager/src/app/effects/{mod,helpers}.rs` and `tui/crates/codegen/pi-pager/src/acp/meta.rs`.
> Tests: the files in `packages/pi-agent-cli/tests/` that section 3 names, and `tui/crates/codegen/pi-pager/tests/python_agent_e2e.rs`.

---

## 0. Summary

- **What is here.** Eight items. Four have a standard equivalent and stay as legacy items: X1, X4, X7 and X8. Four have none: X2, X3, X5 and X6.
- **Who reads what.** The agent reads X1, X2, X7 and X8. The pager reads X4 and X5. Zed reads X6. No code in this repository reads X3 or X6.
- **The gap.** No item is negotiated in `initialize` (rule 5). No item that has a Rust party has its type in `pi-acp-lib` (rule 4). Section 4 has the facts. The owner decided on 2026-11-05 that rules 4 and 5 apply to new items only (section 7), so the eight items stay as they are. This document changes no behaviour.
- **Not in the register.** The pager sends and reads keys that the agent does not use (section 5). They are not part of the contract.

## 1. The rules

### 1.1 What an extension is

An extension is anything on the wire that is not in standard ACP. Standard ACP is the schema `schema-v1.19.0`. An extension has one of two forms:

- A method or a notification with a name that starts with `pi/`.
- A key in the `_meta` object of a standard message. ACP reserves `_meta` for this use.

On the wire, the name of an extension method or notification has a leading underscore, for example `_pi/session/delete`. The ACP libraries add it when they send and remove it when they receive (Python SDK: `acp/router.py` and `acp/agent/connection.py`; the notification of the pager reaches the agent in `python_agent_e2e.rs`). This document uses the names without the underscore, as the code does.

### 1.2 The five admission rules

A new extension must meet all five rules (`docs/DESIGN.md` section 8.5; principle P2 of the plan):

| Rule | Text |
|------|------|
| 1 | No standard equivalent. The current ACP schema and the RFDs decide. |
| 2 | The Python side has a producer or a consumer, with a test. |
| 3 | The item is in this register: the name, the direction, the schema and the capability key. |
| 4 | The wire type is defined once, in `pi-acp-lib` or in a protocol crate next to it. It is not in a runtime crate. |
| 5 | The item is negotiated in `initialize`, and it does no harm to a third-party client such as Zed. |

For an item that is in use and fails a rule, `docs/DESIGN.md` section 8.5 says: lower it, hide it or delete it (plan ADR3). This register records each such item and the decision that keeps it. Rules 4 and 5 apply to new items only (the owner, 2026-11-05; section 7).

## 2. The register

| ID | Item | Kind | Direction | Status |
|----|------|------|-----------|--------|
| X1 | `pi/session/delete` | request | client to agent | legacy, kept (ADR4); the standard method exists, the SDK has no route |
| X2 | `_meta.sessionId` | key in the `session/new` request | client to agent | in use (decision 3) |
| X3 | `_meta["pi/cwd"]` | key in the session responses | agent to client | in use; no reader in this repository |
| X4 | `_meta["pi/currentModelId"]`, `_meta["pi/currentModelDisplayName"]`, `_meta["pi/provider"]` | keys in the session responses | agent to client | legacy, kept (ADR4); the standard `model` config option is the equivalent |
| X5 | `_meta.isReplay` | key in `session/update` | agent to client | in use |
| X6 | `_meta.terminal_info`, `_meta.terminal_output`, `_meta.terminal_exit` | keys in `tool_call` and `tool_call_update` | agent to client | in use; the convention is Zed's |
| X7 | `_meta.yoloMode`, `_meta.autoMode` | keys in the `session/new` and `session/load` requests | client to agent | legacy, kept (D5; the user, 2026-11-04) |
| X8 | `pi/yolo_mode_changed` | notification | client to agent | legacy, kept (D5; the user, 2026-11-04) |

Registered in another spec: the terminal login (`clientCapabilities.auth.terminal`, `_meta["terminal-auth"]`, the authentication method `pi-setup`). Zed defines the first two. The spec `docs/specs/2026-10-28-acp-terminal-auth-setup-design.md` (section 3.2) describes all three. They are not repeated here.

Do not reuse an ID. A new item gets the next number.

## 3. The items

Paths of the pager are relative to `tui/crates/codegen/pi-pager/src/`. Paths of Python tests are relative to `packages/pi-agent-cli/tests/`.

### X1. `pi/session/delete`

- **Wire.** Request, client to agent. Wire name `_pi/session/delete`.
- **Params.** `{"sessionId": string}`. The agent also reads `session_id`. The pager adds `cwd` and `source` (`local`, `remote`, `both` or `current`). The agent ignores both.
- **Result.** `{"sessionId": string, "deleted": true}`. The result is the same when the session does not exist, so the call is idempotent. The pager takes any success as done and ignores the body.
- **Error.** `sessionId` missing, not a string, or blank: `invalid_params` with the data `{"reason": "sessionId is required", "method": "pi/session/delete"}`.
- **Effect.** If the agent holds the session, it stops the turn and closes the harness, as `session/close` does. It forgets the state of the session and its reserved id. Then it deletes the session file.
- **Sent by.** The pager: `Effect::DeleteSession` in `app/effects/mod.rs`. Two actions start it: the delete action of the session picker (`app/dispatch/router.rs`) and the command that deletes the current session (`app/dispatch/session/lifecycle.rs`).
- **Read by.** `PiAcpAgent.ext_method` in `agent.py`. Any other extension method gets `method_not_found`.
- **Tests.** `test_acp_agent.py` (`test_ext_method_pi_session_delete_removes_repo_session`, `test_ext_method_pi_session_delete_requires_session_id`), `test_session_lifecycle.py`, `test_session_chosen_id.py`.
- **Status.** Legacy, kept. The plan keeps it and registers it (ADR4). The standard method exists: schema `schema-v1.19.0` has `session/delete` (param `sessionId`) and the capability `sessionCapabilities.delete`. SDK 0.12.1 has the types and the method name, but no route in `acp/agent/router.py`. A standard request therefore gets `method_not_found`, and the agent cannot serve it. When the SDK has the route, the agent must serve and announce the method, the pager must send it, and X1 goes away.

### X2. `_meta.sessionId` in `session/new`

- **Wire.** Key in the `_meta` of the `session/new` request, client to agent.
- **Schema.** A string. It must be a UUID in any form that Python `uuid.UUID` reads. The agent gives it back in the canonical form (lower case, with hyphens) as `sessionId` of the response.
- **Error.** A value that is not a string, or not a UUID, gets `invalid_params`, because the id becomes a file name. An id that is in use gets `invalid_params` too, in any directory, and also when the session has no file yet. Requests with the same id at the same time make one session.
- **Without the key.** The agent chooses the id.
- **Sent by.** The pager: `Effect::CreateSession` in `app/effects/mod.rs` (the flag `--session-id`). Before that, `resolve_startup` reads all pages of `session/list` and refuses an id that is listed.
- **Read by.** `_chosen_session_id` in `agent.py`.
- **Tests.** `test_session_chosen_id.py`; `test_acp_stdio_contract.py` (`test_a_client_can_choose_the_id_of_a_new_session_and_a_taken_id_is_refused`, with a live process); `python_agent_e2e.rs` (the id is used, and a taken id is refused).
- **Status.** In use. Plan decision 3: the agent uses the id and refuses a duplicate. ACP v1 has no field for it: `NewSessionRequest` has `cwd`, `additionalDirectories`, `mcpServers` and `_meta`.
- **More.** The checks that the agent makes, and what they cost: `packages/pi-agent-cli/AGENTS.md`, section "Extensions beyond ACP v1".

### X3. `_meta["pi/cwd"]` in the response that opens a session

- **Wire.** Key in the `_meta` of the `session/new`, `session/load` and `session/resume` responses, agent to client.
- **Schema.** A string: the directory in which the session works. For `load` and `resume`, the directory saved with the session wins over the `cwd` of the request. The key is absent when the agent does not know the directory.
- **Sent by.** `PiAcpAgent._session_response_meta` in `agent.py`.
- **Read by.** Nothing in this repository. The pager does not read it. A client that knows only where it was started can use it (`zypi --resume <id>` from another directory).
- **Tests.** `test_session_chosen_id.py`, `test_blank_session_reopen.py`, `test_acp_stdio_contract.py` (`test_loading_a_session_says_which_directory_it_works_in`).
- **Status.** In use, with no reader. Added in round r10 with ADR1 (PR 1). The responses have no `cwd` field in schema `schema-v1.19.0`.

### X4. The model hints `pi/currentModelId`, `pi/currentModelDisplayName` and `pi/provider`

- **Wire.** Three keys in the `_meta` of the same three responses as X3, agent to client.
- **Schema.** Strings. `pi/currentModelId` is the id of the current model choice. `pi/currentModelDisplayName` is its name, or its id when it has no name. `pi/provider` is its provider; it is absent when the choice has none. All three keys are absent when the id is empty.
- **Sent by.** `_session_response_meta` in `agent.py`.
- **Read by.** The pager, as the last fallback: `parse_session_response_models` in `app/effects/helpers.rs` uses the keys only when the response has no `models` and no `model` config option. The agent always sends the `model` option, so the pager does not use these keys with this agent.
- **Tests.** `test_acp_agent.py` (`test_session_responses_include_model_meta`), `test_blank_session_reopen.py`, `test_session_chosen_id.py`.
- **Status.** Legacy, kept for clients that do not know config options (`packages/pi-agent-cli/AGENTS.md`, section "Model selection"). The plan keeps and registers them (ADR4). The standard equivalent is the `model` config option (category `model`). No decision gives a date for their removal.

### X5. `_meta.isReplay` on replayed updates

- **Wire.** Key in the `_meta` of `session/update` notifications, agent to client.
- **Schema.** The boolean `true`. A live update does not have the key. `session/resume` does not replay.
- **Where.** The agent sets it on each update that `session/load` replays: `user_message_chunk`, `agent_thought_chunk`, `agent_message_chunk`, `tool_call` and `tool_call_update` (`project_message_replay` in `events.py`).
- **Read by.** The pager: `NotificationMeta::from_json` in `acp/meta.rs` (a missing key is `false`) and `export_cmd/replay.rs` (`zypi export`). The pager needs the flag because a replayed update can arrive after the response to `session/load` (`late_replay_until` in `app/agent_view/mod.rs`), and it must not show that update as live.
- **Tests.** `test_acp_agent.py` (`test_load_session_replays_history`, `test_resume_session_does_not_replay_history`); `python_agent_e2e.rs` (`a_session_is_listed_with_a_title_and_replayed_by_a_later_agent`).
- **Status.** In use. The plan keeps the replay of `session/load` (appendix A.1, row "历史回放"). `SessionNotification` has no replay field in schema `schema-v1.19.0`.

### X6. The terminal keys on the tool calls of `bash`

- **Wire.** Keys in the `_meta` of `tool_call` and `tool_call_update` notifications, agent to client. Only the `bash` tool has them. The convention is Zed's. It is not ACP's and not ours. Zed reads the keys in `crates/agent_servers/src/acp.rs` (repository `zed-industries/zed`) and shows a read-only terminal.
- **Schema, live.**
  - `tool_call`, status `pending`: `terminal_info = {"terminal_id": <tool call id>, "cwd": <string>}`. `cwd` is present when the agent knows it. The `content` has a `terminal` item with the same id.
  - `tool_call_update`, status `in_progress`: `terminal_output = {"terminal_id": <id>, "data": <text that is new since the last update>}`. The agent sends it only when there is new text.
  - `tool_call_update`, status `completed` or `failed`: `terminal_exit = {"terminal_id": <id>, "exit_code": <integer>}`, and `terminal_output` when some text is not sent yet.
- **Schema, replay.** The replayed `tool_call` has `terminal_info` with no `cwd`. The replayed result has `terminal_output` with the whole text, and `terminal_exit`. All of them also have `isReplay` (X5).
- **`exit_code`.** It is 0 when the call is not an error. Otherwise it is the `exit_code` that the tool gives, or else the number in the text `Command exited with code N`, or else 1. The agent never sends `signal`.
- **Text.** Each update also has the output text in `content`. A client that ignores the keys shows the same output.
- **Capability.** Zed sends `clientCapabilities._meta.terminal_output = true`. The agent does not read it. It sends the keys to every client.
- **Sent by.** `project_event` and `project_message_replay` in `events.py`.
- **Read by.** Zed, which is outside this repository. No code in this repository reads these keys. The pager does not.
- **Tests.** `test_acp_agent.py` (`test_project_event_bash_lifecycle_terminal_meta`, `test_project_message_replay_bash`).
- **Status.** In use. ACP has the `terminal/*` methods, but with them the client runs the command. Here the agent runs it, so there is no standard equivalent. No decision in the plan names this item.

### X7. `_meta.yoloMode` and `_meta.autoMode`

- **Wire.** Two keys in the `_meta` of the `session/new` and `session/load` requests, client to agent. The agent reads them in `session/resume` too.
- **Schema.** Booleans. The pager sends both keys in each `session/new` and `session/load`, and a key is `false` when its mode is off. A key counts only when its value is the JSON `true`. `yoloMode: true` means `always-approve`. `autoMode: true` means `auto`. When both are true, `yoloMode` wins. A key that is `false` or missing says nothing: the start mode of the agent (`agent.toml`) stays.
- **Sent by.** The pager: `SessionFlags::to_meta` in `app/effects/helpers.rs` and `apply_permission_mode_override` in `app/effects/mod.rs`.
- **Read by.** `mode_from_session_meta` in `permission_modes.py`, called from `agent.py`.
- **Tests.** `test_permission_mode_wire.py` (the table of values), `test_acp_stdio_tools.py` (over the wire), `python_agent_e2e.rs` (`new_session_launched_in`).
- **Status.** Legacy, kept. The Auto design registered it as a legacy item (decision D5; `docs/specs/2026-11-02-auto-mode-design.md` section 4.2). The user decided on 2026-11-04 that the pager keeps it (plan item 1.P3 is closed). The standard equivalent is the `mode` config option and `session/set_mode`, which the agent also serves.

### X8. `pi/yolo_mode_changed`

- **Wire.** Notification, client to agent. Wire name `_pi/yolo_mode_changed`. Nothing answers it.
- **Params.** `{"yolo_mode": boolean, "auto_mode": boolean, "permission_mode": string}`. The agent reads only `permission_mode` (it also reads `permissionMode`). The value must be `ask`, `auto` or `always-approve`. The agent ignores letter case and spaces at the ends. It ignores any other value. The agent takes any method whose last part is `yolo_mode_changed` or `permission_mode_changed`; only the first has a sender.
- **Effect.** The notification has no session id. The agent sets the mode of every open session. The mode is also the start mode of the sessions that open later.
- **Sent by.** The pager: `persist_permission_mode_and_notify` in `app/effects/helpers.rs`. It sends the notification when the user changes the mode, and only when a session exists. In the variant with rollback, it sends the notification only when the write of the config file succeeded.
- **Read by.** `PiAcpAgent.ext_notification` in `agent.py`, with `mode_from_notification` in `permission_modes.py`.
- **Tests.** `test_permission_mode_wire.py`, `test_acp_stdio_tools.py` (over the wire), `test_acp_agent.py` (`test_permission_mode_notification_updates_live_session_policy`, `test_invalid_permission_mode_notification_is_ignored`), `python_agent_e2e.rs` (`announce_mode`).
- **Status.** Legacy, kept, with the same decisions as X7. The standard equivalent is `session/set_config_option` (`mode`) or `session/set_mode`. The standard way is per session. This notification moves all sessions.

## 4. The admission check

Each cell says how the item stands against the five rules. "Harmless" means: a client that does not know the item ignores it, and the agent works without it.

| Item | Rule 1: no standard equivalent | Rule 2: Python side and test | Rule 3: registered | Rule 4: type in `pi-acp-lib` | Rule 5: negotiated, harmless |
|------|------|------|------|------|------|
| X1 | **fails**: `session/delete` is in the schema; the SDK has no route | yes | yes | no: the pager builds the JSON in `app/effects/mod.rs` | not negotiated; harmless |
| X2 | meets: `NewSessionRequest` has no id field | yes | yes | no: the pager builds it in `app/effects/mod.rs` | not negotiated; harmless |
| X3 | meets: the responses have no `cwd` field | yes (producer) | yes | no Rust party | not negotiated; harmless |
| X4 | **fails**: the `model` config option | yes | yes | no: the pager decodes it in `app/effects/helpers.rs` | not negotiated; harmless |
| X5 | meets: `SessionNotification` has no replay field | yes | yes | no: the pager decodes it in `acp/meta.rs` | not negotiated; harmless |
| X6 | meets: with `terminal/*` the client runs the command | yes (producer) | yes | no Rust party | not negotiated (the agent ignores the capability of Zed); harmless |
| X7 | **fails**: the `mode` config option | yes | yes | no: the pager builds it in `app/effects/helpers.rs` | not negotiated; harmless |
| X8 | **fails**: `session/set_config_option` and `session/set_mode` | yes | yes | no: the pager builds it in `app/effects/helpers.rs` | not negotiated; harmless |

Result. Four items fail rule 1 (X1, X4, X7, X8). Each of them has a decision that keeps it (section 3). Six items have a Rust party (X1, X2, X4, X5, X7, X8) and none of them has its type in `pi-acp-lib`. No item is negotiated in `initialize`: the `initialize` response of the agent has no `_meta` (plan item 1.P5). Rule 3 is met by this document. The owner decided on 2026-11-05 that rules 4 and 5 apply to new items only (section 7), so the gap in rules 4 and 5 does not change any item.

## 5. What is not in the register

The pager sends and reads more keys than the agent uses. The agent neither reads these keys nor sends them. They are not part of the contract. The plan decides each one when it comes up (ADR4: implement it, lower it, or delete its UI). This list is not complete.

| Key | Where | State |
|-----|-------|-------|
| `initialize` request `_meta`: `clientType`, `clientVersion`; also `systemPromptOverride` and `rules` when the flags are set | pager, `acp/mod.rs` | The agent reads none. Interactive mode refuses the two flags (plan decision 8). |
| `session/new` request `_meta`: `agentProfile`, `askUserQuestion` (`false`), `modelId` | pager, `app/effects/helpers.rs` and `app/effects/mod.rs` | The agent reads none. |
| `session/prompt` request `_meta`: `screenMode` (telemetry) | pager, `app/effects/mod.rs` | The agent reads none. |
| `initialize` response `_meta`: `modelState`, `grokShell`, `availableCommands`, `cancelRewind`, `sessionRecap`, `feedbackTraceOffer` | pager decoders | The response of the agent has no `_meta`, so the pager uses its defaults. The user decided to keep the decoders (plan item 1.R5). |
| `session/update` `_meta`: `totalTokens`, `agentTimestampMs`, `streamStartMs`, `turnStartMs`, `promptId`, `eventId` | pager, `acp/meta.rs` | The agent sends none. |
| Methods and notifications with a name that starts with `x.ai/` or `_x.ai/` | pager, at the entry (`docs/DESIGN.md` section 8.3) | The pager drops them. |

## 6. How to add, change or remove an item

1. Check the five rules (section 1.2). If rule 1 fails, use the standard item.
2. Put the item in this register in the same pull request as the code: a row in section 2, an entry in section 3, and a row in section 4.
3. Write the Python side with a test. If the pager is a party, write its side with a test too. `python_agent_e2e.rs` tests the wire.
4. Give a new method or notification the prefix `pi/`. Give a new `_meta` key the prefix `pi/` too, unless another client defines the key (the keys of X6 are such keys).
5. Define the Rust type once, in `pi-acp-lib`.
6. Announce the item in `initialize` with a capability key. A client that does not know the key must be able to ignore the item.
7. To change the schema of an item, change its entry in the same pull request. To remove an item, set its status to `removed` and write the date and the pull request. Do not delete its entry.

## 7. Question and facts not checked

**Question for the owner.** The owner answered on 2026-11-05 and chose the proposal. All eight items stay as they are.

| # | Question | Answer (the owner, 2026-11-05) |
|---|----------|----------|
| Q1 | Rules 4 and 5 are written for a new item. For an item that is in use, `docs/DESIGN.md` section 8.5 says: if it fails a rule, lower it, hide it or delete it. No item meets rule 5, and six items fail rule 4. Do rules 4 and 5 apply to these eight items? | No. Rules 4 and 5 apply to new items only. The eight items stay, and this register is the record of the gap (section 4). |

**Facts not checked.**

- The Rust libraries of the pager (`agent-client-protocol` 0.10.4, schema 0.11.4) were not checked for `session/delete`. If they lack it, the pager cannot send the standard request, and X1 cannot go away until they are upgraded.
- The reading of the X6 keys by Zed comes from the source of Zed, not from a run in Zed. Zed reads `exit_code` as an unsigned integer. The agent sends a negative number only if the `bash` tool reports one. That was not checked.
- It is not known whether another client reads X3 or X4.
- The list in section 5 comes from a search of the sources for the keys that the plan names. It is not a full audit of the pager.
