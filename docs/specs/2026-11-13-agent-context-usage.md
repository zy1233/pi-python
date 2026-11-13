# Context use and cost from the agent: investigation record

> Date: 2026-11-13.
> Status: **record only.** No agent file was changed. This record does not describe a feature that is built in the agent. It describes what was found, and what an agent change must do.
> Reason: the user asked for the context use in the status row of the VS Code extension. The user also said: if the agent must change, write down the result of the investigation, and do not make the change.
> Related: `docs/specs/2026-11-04-vscode-extension-design.md` section 7d (the extension side, which is built); `docs/specs/2026-11-05-acp-extension-registry.md` section 5; the entry of 2026-10-13 in `docs/PLAN/archive/PLAN-RUST-AGENT-RUNTIME-REMOVAL-EXECUTION-LOG.md` (the pager had the same question).

## 1. Question

Can the status row of the extension show how much of the context window is in use, with agent 0.9.1?

## 2. Answer

**No.** Agent 0.9.1 sends no token count, no window size and no cost to a client. Three parts are missing:

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

## 4. What an agent change must do (a proposal: not built, not tested)

1. **A source for the window size.** Add the key `context_window` to `[model]` and `[[models]]` (`ModelChoice`), and set `Model.context_window` from it in `model_for_choice`. For a model that has no value, there are four options:
   - **A (recommended).** Send no `usage_update` for it. No number is a guess.
   - **B.** A table of known models in the agent. Not recommended: it goes out of date, and it is wrong for gateways.
   - **C.** Keep 128,000 and send it. Not recommended: the number is invented.
   - **D.** Send `size: 0`. The extension reads it as "size not known" and shows the count only. It is not standard, and another client can divide by zero.
2. **The message.** At each `turn_end`, send `UsageUpdate(used, size)`. Build `acp.schema.UsageUpdate` directly. `project_event` (`events.py`) already turns harness events into ACP updates. A `turn_end` case can take `used` from `event.message.usage` (`calculate_context_tokens`) and add `estimate_message_tokens` for each of `event.tool_results`. No call to `build_context()` is needed. `project_event` needs the window size as an argument.
3. **After a compaction, and after `session/load`.** The count drops after a compaction, and `_maybe_auto_compact` runs after `turn_end`. Send a second update when the compaction ends. Without it, the row shows the old count until the next turn ends. After `session/load`, send one update when the replay ends.
4. **No `cost`.** Leave the field out until the agent has a price source. Do not send 0.
5. **Tests.** A test of the projection with a fake harness event. A test over ACP (a Python SDK client) that one prompt gives one `usage_update`. A test that a model with no `context_window` gives none.

## 5. Risks and side effects

- **Compaction changes.** A real window changes `should_compact` for each session that has `auto_compact` on. A model with a window under 128,000 compacts earlier (this is correct). A model with a larger window compacts later. This is a change of behaviour. It needs its own decision and its own test.
- **The count is partly an estimate.** A tool result that the model has not read yet is an estimate. The count is exact after the next model call.
- **Gateways.** A gateway that sends no usage gives a count that is only an estimate.
- **One client.** Only the extension reads `usage_update` today. To show it in `zypi`, the pager needs a Rust change.

## 6. Release path

An agent change is not enough. The extension bundles one fixed agent binary. After the change: release the agent, then change `zycodeAgentVersion` and the size and the sha256 in `vscode/agent-assets.json`, then release the extension. A user who sets `zycode.agentPath` gets the change as soon as that program is new.

## 7. Open decisions (for the owner)

1. Change the agent? When?
2. The source of the window size (section 4, item 1: A to D).
3. Compaction by the real window: yes or no, in the same change?
4. Teach the pager to read `usage_update`?

## 8. What the extension does today

It does what needs no agent change (design section 7d):

- It reads `usage_update` when an agent sends one: the Context row (a bar, the percent, the counts) and the cost.
- It measures the time of the turn with its own clock.
- It shows the folder of the chat.

With agent 0.9.1 the Context row does not show. <!-- probe of the ci.yml paths filter; do not merge -->
