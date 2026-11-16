# Context use and cost from the agent: investigation record

> Date: 2026-11-13 (investigation); 2026-11-16 (built).
> Status: **built (Plan A), not released.** Sections 1 to 3 are the investigation of 2026-11-13. They describe agent 0.9.1, and they are not changed. Section 4 says what the agent does now.
> Reason: the user asked for the context use in the status row of the VS Code extension. On 2026-11-13 the user said: if the agent must change, write down the result of the investigation, and do not make the change. On 2026-11-16 the user asked for the change, with Plan A (option A of section 4, item 1, in the first version of this record).
> Related: `docs/specs/2026-11-04-vscode-extension-design.md` section 7d (the extension side, which is built); `docs/specs/2026-11-05-acp-extension-registry.md` section 5; the entry of 2026-10-13 in `docs/PLAN/archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md` (the pager had the same question).

## 1. Question

Can the status row of the extension show how much of the context window is in use, with agent 0.9.1?

## 2. Answer

**No, for agent 0.9.1.** It sends no token count, no window size and no cost to a client. Three parts were missing (section 4 says what the agent does now):

1. **The message.** The agent never sends the ACP message `usage_update` (section 3, rows 1 and 2).
2. **The window size.** The agent does not know it. No file and no setting gives it (row 4).
3. **The cost.** Nothing in the agent fills it (row 5).

The token count itself exists in the agent: the harness already computes it for compaction (row 3).

The extension reads `usage_update` when an agent sends it (design section 7d). Until then, the Context row does not show. The extension does not count tokens and does not guess a size.

## 3. What was checked

| # | Question | Finding |
|---|----------|---------|
| 1 | Is there a standard message? | Yes. `usage_update` is in the stable ACP v1 schema. Its fields are `used` (tokens in the context now), `size` (size of the window in tokens) and `cost` (optional: `amount` and `currency`). `used` and `size` are required. The TypeScript SDK 2.4.0, which the extension uses, checks the shape and drops an update that does not match: it writes `Error handling notification` to the console and goes on. The Python SDK `agent-client-protocol` 0.12.1, which the agent uses, has the class `acp.schema.UsageUpdate` and no helper function for it (`acp.helpers` has none). In the stable schema, `PromptResponse` has no usage; the unstable schema has it. |
| 2 | Does the agent send it? | No. `usage_update` and `UsageUpdate` do not occur in `packages/pi-agent-cli/pi_agent_cli`. The prompt response has `stop_reason` only. The agent sends updates with `self._conn.session_update(...)` (`agent.py`), so it can send this one in the same way. |
| 3 | Does the agent know how many tokens are in the context? | Yes. Each assistant message has `usage` (`input`, `output`, `cacheRead`, `cacheWrite`, `totalTokens`). `input` includes the cached tokens. The harness takes the usage of the last good assistant message and adds an estimate (4 characters for 1 token) for the messages after it (`estimate_context_tokens`, `compaction/utils.py`). In 21 assistant messages of the author (the last 4 of each of 6 session files, model `deepseek-flash`), `totalTokens` is 12,768 to 112,562. The adapter sets `stream_usage=True` for the providers `openai` and `deepseek` (`langchain_stream.py`), so the stream carries the usage. A gateway can still send none. Then `usage` is 0 and the whole count is an estimate. |
| 4 | Does the agent know the size of the window? | No. `Model.context_window` has the default 128,000 (`pi_agent_core/types.py`), and `model_for_choice` (`pi_agent_cli/factory.py`) does not set it. `agent.toml` has no key for it: `ModelChoice` has `id`, `name`, `provider`, `base_url`, `api_key_env` and `supports_images`. So 128,000 is a placeholder for every model, and a percent from it is wrong for a model with another window. |
| 5 | Does the agent know the cost? | No. `Usage.cost` exists, but nothing fills it. Only the benchmark script (`benchmarks/evaluator.py`) has a price table. In the 21 messages of row 3, `cost.total` is 0 in all. |
| 6 | Where is the window used today? | Only for compaction. `should_compact` is true when the count is above `context_window - reserve_tokens` (128,000 - 16,384 = 111,616 today). `auto_compact` is off by default. `factory.py` turns it on when `max_turns >= 30`. |
| 7 | Do other clients read `usage_update`? | The pager `zypi` does not: no Rust file under `tui/` names it. The pager reads `_meta.totalTokens`, and the agent does not send it (registry section 5). The decision of 2026-10-13 (PR #19) says the same: the pager shows no context and no cost, and no number is invented. |

## 4. What the agent does now (built 2026-11-16, Plan A)

The change follows the proposal of the first version of this record. The differences are marked. The numbers are found in `packages/pi-agent-cli/pi_agent_cli/context_usage.py`. The sending is in `agent.py`.

1. **The window size.** `[model]` and each `[[models]]` entry can have `context_window`: a whole number of tokens, from 1 to 1,000,000,000. This is option A. A model with no value gets no `usage_update`. The 128,000 of the core is never sent.
   - A value that cannot be used (text, 0, a negative number, a boolean, a fraction, a number that is too large) is dropped. The agent says so at the start of a session, in the notes about settings that were read and not used.
   - The value is never inherited. A `[[models]]` entry has its own value, or none. This is also true when it has the same provider as `[model]`. A window belongs to one model.
   - `model_for_choice` sets `Model.context_window` only when the choice has a value.
2. **When the agent sends it.** At each `turn_end`, `PiAcpAgent._report_context_use` sends `UsageUpdate(used, size)`. `used` is the number of the provider for the assistant message (`calculate_context_tokens`), plus `estimate_message_tokens` for each tool result of the turn. The agent sends nothing when the provider gave no number: `usage` is 0 (a gateway), or the turn ended with `error` or `aborted`.
   - *Difference.* The proposal put this in `project_event`. That function is pure and has no state. The new code keeps the last count of each session. So it is a method of the agent, and `project_event` is not changed.
3. **Other times.**
   - *After a compaction.* The agent sends an estimate for the messages that remain (4 characters for 1 token). The kept messages still carry the `usage` of the context from before, so that `usage` is not used. The agent sends this only when it has sent a count for the session before. Otherwise there is no old number to correct.
   - *After `session/load` and `session/resume`.* The agent sends the count of the saved history after the response has gone out (the setup that follows the response; see zed#60199), and before the question about the project. It sends only a number that is still true: an assistant message with a number of the provider must come after the last compaction. A history that has none gets no update, and the next turn gives one.
   - *After a change of model* (`session/set_config_option`). The agent sends the last count again, with the window of the new model. When the new model has no window, it sends nothing. ACP has no message that clears the numbers, so the client keeps what it shows.
4. **No `cost`.** The field is left out. The agent has no price table.
5. **Compaction by the real window** (decision 3 in section 7: yes, in the same change). A model with a `context_window` has the real value in `Model.context_window`, and `should_compact` uses it. The limits of compaction fit the window (`factory.compaction_settings`): the defaults (16,384 tokens kept free, the last 20,000 tokens kept) are cut to a quarter and a third of the window when that is less. A window of 65,536 tokens or more keeps the defaults. Without this, a small window (8,192 or 32,000 tokens) would compact again after every turn. A change of model sets the limits again. A model with no window keeps the placeholder and the defaults, so nothing changes for it.
6. **Tests.** `packages/pi-agent-cli/tests/test_context_usage.py`. It tests the numbers (pure functions, with a number from before a compaction). It tests the configuration (reading, the key at the top of the file, no inheritance, bad values and their notes, `model_for_choice`, `compaction_settings`). It tests the agent in one process with a scripted model: one update for one turn, a tool turn, no window, no usage, a failed turn, a client that refuses the update, a change of model, `session/close`, `session/load`, `session/resume`, a compaction, and compaction by a declared window against the placeholder. Seven deliberate faults in the code (for example, a send of a window that is not declared) each made a test fail.
7. **Checked on the wire** (Windows, 2026-11-16). The probe started the real agent process (`python -m pi_agent_cli`) on stdio, with a fake OpenAI-format model server (`vscode/test/support/fake_model.py`), and read raw ACP JSON lines. One prompt gave `{"used": 5, "size": 128000, "sessionUpdate": "usage_update"}` once, after the answer, with no `cost`. `session/load` in a new process gave the same message after the replay. After a change to a model with no window, a prompt gave none. A second check ran the real agent with the real chat controller of the extension (`ChatController`). The test was not kept. With `context_window = 128000`, the state of the controller had `used` 5 and `size` 128000 after one prompt. With no `context_window`, it had no usage.

## 5. Risks and side effects

- **Compaction changes (on purpose).** For a model with a `context_window`, `should_compact` uses the real window, in each session that has `auto_compact` on (since 2026-11-16 the key `auto_compact` and a declared window decide, and `max_turns >= 30` still counts: see `2026-11-16-thinking-max-and-turn-limit.md`). A model with a window under 128,000 compacts earlier (this is correct). A model with a larger window compacts later. A model with no `context_window` does not change. Test: `test_compaction_uses_the_window_that_the_user_declared`.
- **The count is partly an estimate.** A tool result that the model has not read yet is an estimate. The count after a compaction is an estimate for the messages only: it has no system prompt and no tool descriptions, and 4 characters for 1 token is too low for Chinese, Japanese and Korean text. The next model call gives an exact count.
- **Gateways.** A gateway that sends no usage gives no update. The agent does not show an estimate that has no number of a provider behind it.
- **A window that is too small.** A user who writes a window under the real one can see more than 100 %. The agent sends the number as it is.
- **A change to a model with no window.** The client keeps the old numbers (section 4, item 3).
- **Clients.** Only the extension shows `usage_update`. `zypi` reads the message and ignores it. Its crate `agent-client-protocol` 0.10.4 parses `usage_update` only with the feature `unstable`, and `tui/Cargo.toml` turns that feature on. A check of 2026-11-16 used a small Rust program with the same crate versions (from `tui/Cargo.lock`) and the message of the probe: the message parses, and the same program without the feature fails with `unknown variant usage_update`. After the parse, the pager has a catch-all arm for the variants that it does not use (`acp/tracker.rs`), so it does nothing with the update. Do not remove that feature without a new check. To show the update in `zypi`, the pager needs a Rust change. A client that cannot read `usage_update` gets it only when the user wrote a `context_window`.

## 6. Release path

An agent change is not enough. The extension bundles one fixed agent binary. After the change: release the agent, then change `zycodeAgentVersion` and the size and the sha256 in `vscode/agent-assets.json`, then release the extension. A user who sets `zycode.agentPath` gets the change as soon as that program is new.

At the extension release, change the sentences that say that the bundled agent sends no `usage_update`: `vscode/README.md` (the Context row), `vscode/CHANGELOG.md` and design section 7d. They are true for agent 0.9.1 only.

## 7. Decisions

1. Change the agent? **Yes** (the user, 2026-11-16). It is built, and it is not released.
2. The source of the window size: **A** (section 4, item 1).
3. Compaction by the real window, in the same change? **Yes.** It follows from setting `Model.context_window`. It has its own test, and a guard for small windows (section 4, item 5).
4. Teach the pager to read `usage_update`? **Open.** Not decided, not built.

## 8. What the extension does today

It does what needs no agent change (design section 7d):

- It reads `usage_update` when an agent sends one: the Context row (a bar, the percent, the counts) and the cost.
- It measures the time of the turn with its own clock.
- It shows the folder of the chat.

With agent 0.9.1, which the extension bundles now, the Context row does not show. With an agent that has the change in section 4, it shows for a model that has a `context_window`. The extension reads the message in tests with a fake agent. Its chat controller ran once against the changed agent (section 4, item 7). The extension has not been run against the changed agent in a real VS Code.
