# pi-agent-harness

`pi-agent-harness` is the higher-level harness package for `pi-agent-core`.

It contains the Phase 3 runtime pieces that are intentionally kept outside the
lightweight core package:

- `AgentHarness`
- session tree storage and repos
- harness-specific custom messages
- hook, queue, and persistence orchestration
- compaction, branch summaries, tree navigation, and optional auto compact
- skills, prompt templates, system prompt injection, and `LocalExecutionEnv`

Install it alongside core:

```bash
pip install pi-agent-core-lc pi-agent-harness-lc
```

For local development from the monorepo:

```bash
uv run --extra dev --extra harness python -m pytest
```
