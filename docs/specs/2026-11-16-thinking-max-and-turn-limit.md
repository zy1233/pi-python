# The `max` thinking level and the turn limit: investigation record

Date: 2026-11-16. Status: built in the branch of PR #131, as separate commits. Not released.

## 1. Question

The owner asked for two small changes:

1. Add `max` to `thinking_level`.
2. Raise the default of `max_turns`, because the main models now have a context of 1M tokens.

## 2. What was checked

### 2.1 `thinking_level = "max"`

| # | Question | Answer |
|---|----------|--------|
| 1 | Does the reference have it? | Yes. `pi` (TypeScript) added a separate, opt-in level `max` above `xhigh` in v0.80.6 (2026-07-09). `pi` also decides per model which levels are open: `Model.thinkingLevelMap` (a string is sent, `null` means "not supported"), with `getSupportedThinkingLevels()` and `clampThinkingLevel()`. |
| 2 | Do providers use the name? | Yes. The `pi` changelog names `max` as a level of DeepSeek V4, OpenAI GPT-5.6 and later, Anthropic adaptive models (`effort`), Kimi K3, GLM 5.x and MiniMax M3.1. |
| 3 | What did the agent do before? | It had six levels. `provider = "openai"` sent `xhigh` as "high", so a gateway for a DeepSeek V4 model could not be asked for "max". Only DeepSeek's own API turned `xhigh` into "max". |
| 4 | Did an unknown level warn? | No. A `thinking_level` that was not one of the six was `off` with no word, so a typo looked like `off`. A list as the value stopped the loading with a `TypeError`. |
| 5 | Does the budget form of Anthropic work for new models? | **Not verified.** The documentation of Anthropic says that Claude 4.7 and later refuse `thinking: {type: enabled, budget_tokens}` (400) and want `thinking: {type: adaptive}` with `effort`. `ChatAnthropic` has `effort`, but `pyproject.toml` allows `langchain-anthropic>=0.2.0`, which is too old for it. `langchain_anthropic` is not installed in `.venv`. |

### 2.2 `max_turns`

| # | Question | Answer |
|---|----------|--------|
| 1 | What is the default? | `None` (no limit) in the core, the harness and the CLI. The `50` is only the value in `agent.example.toml`. `--setup` writes only `[model]`, so a user of `--setup` has no limit. |
| 2 | What else does `max_turns` do? | `factory.py` turned compaction on when `max_turns >= 30`. With no `max_turns` there was no compaction. |
| 3 | Which window does compaction use? | The placeholder of the core (128,000) when `context_window` is not declared. Computed with the real code for a gateway with `provider = "openai"`, `max_turns = 150` and no `context_window`: compaction starts above 111,616 tokens, which is 11.2 % of a window of 1M. With `context_window = 1000000` it starts above 983,616. |
| 4 | What do other agents do? | Claude Agent SDK: no limit. Gemini CLI: no limit (`maxSessionTurns = -1`, with loop detection). Goose: 1000. OpenHands (V0): 100. |
| 5 | How many calls does a long task need? | `docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md`: nine DeepSWE tasks all used the whole limit of 60 calls. Six were run again with a limit of 100, and four passed. Three of these finished by themselves (httpx 28, katex 78 and expr 89 calls), and `arktype` used all 100. The official `pi`, with no limit, ran `arktype` for 334 calls and $10.43. That was another model, so it is only a measure of size. |
| 6 | What does a client see at the limit? | `end_turn`, and a message of the agent `Error: Agent loop exceeded max_turns=N`. ACP has a stop reason for it: `max_turn_requests`. The extension shows "The turn ended: …" for a stop reason that is not `end_turn`, `cancelled` or `error` (`chat-controller.ts`). `zypi` takes it as "done" (`turn_completion.rs`). |

## 3. Decisions (the owner, 2026-11-16)

1. `max`: **Plan A**. Add the level and the three mappings. No table of levels per model (Plan B, not built). The change goes in the branch of PR #131, as its own commit.
2. `max_turns`: **all three** of these.
   1. The value in `agent.example.toml` is 200 (it was 50), with a comment that says it guards against a loop that does not end. The default in the code stays `None`: a finite default would put a limit that nobody can see on every user who has no `max_turns`, and the `--setup` users have none.
   2. A key `auto_compact`, so that compaction does not depend on `max_turns` (section 4).
   3. The stop reason `max_turn_requests`.

## 4. What was built

- `ThinkingLevel` has `max` (`pi_agent_core/types.py`). OpenAI format: "max", as it is. DeepSeek's own API: "max". Anthropic: the budget of `xhigh` (40000), because a budget has no step above. Each of the three tables names every level, and a test checks it: a level that a table lacks sends no parameter, and the model then silently does not think.
- A `thinking_level` that is not one of the levels is `off`, and a note says so at the start of the session (the same channel as `context_window`: `auto.notes`). The valid levels come from the type, so a level that the core learns is valid in the CLI at once.
- `auto_compact` is a key at the top of `agent.toml` (`true` or `false`; another type is ignored, with a note). `factory.auto_compact_enabled` decides. The key wins. When it is not set, compaction is on for a model with a declared `context_window` and for `max_turns >= 30`, and off otherwise. After a change of model the rule decides again. `auto_compact = true` for a model with no window assumes 128,000 tokens.
- `MAX_TURNS_MESSAGE` (`pi_agent_core/types.py`) is the one source of the text of `MaxTurnsExceededError`. `_stop_reason` answers `max_turn_requests` when the text of an error holds it. Any other error is still `end_turn`.
- `agent.example.toml`: `max_turns = 200`, the text for `auto_compact`, and `context_window = 1000000` as the example. It was 128000, which is wrong for a model with a window of 1M.

Tests: `test_thinking.py`, `test_deepseek_thinking.py`, `test_deepseek_replay.py`, `test_config.py`, `test_model_reasoning.py`, `test_agent_loop.py`, `test_acp_agent.py` and `test_context_usage.py`. Five rules were broken one at a time (the stop reason branch, the window rule, the key rule, the decision after a change of model, and the text of the error): each break made a test fail.

## 5. Risks and side effects

- **A declared window turns compaction on.** A user who has `context_window` and no `max_turns` did not compact before. This is on purpose: the agent knows when the context is full. `auto_compact = false` turns it off. `context_window` is not released yet (PR #131), so nobody depends on the old behaviour.
- **`max` is open for every model.** A model that does not take it answers with an error, and nothing is lowered. Plan B would lower it.
- **Old agents.** An agent that does not know `max` takes `thinking_level = "max"` as `off`, and it gives no note. Release the agent first. Then add `max` to the menu of the extension, only for an agent that knows it.
- **A client sees `max_turn_requests`, not `end_turn`.** The extension shows a line of notice. `zypi` takes it as "done". Another client may show it as a failure: not checked.

## 6. Not verified

- A live gateway or provider that gets `reasoning_effort = "max"` (a gateway for DeepSeek V4, and api.openai.com).
- The Anthropic path (no `langchain_anthropic` in `.venv`), and the claim about Claude 4.7 and later (it comes from the documentation).
- The VS Code extension with `thinking_level = "max"` in the file. Its menu has six levels.
- What happens when a session with compaction off reaches the limit of the model.

## 7. Open

1. The extension: add `max` to the Thinking menu, only when `agentInfo.version` is new enough. After the agent release.
2. Anthropic: adaptive thinking with `effort` for new models, and a higher floor for `langchain-anthropic`. A separate change.
3. Plan B: a table of levels per model, as in `pi`, so that a level that a model does not take is lowered, and a menu can hide it.
4. A compaction threshold of its own. With a window of 1M, compaction starts above 983,616 tokens, which can be late (cost, quality). Not investigated.
