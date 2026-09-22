# pi-dynamic-workflows-py

Dynamic workflow orchestration extension for [pi-python](https://github.com/zy1233/pi-python).

Python port of [@quintinshaw/pi-dynamic-workflows](https://github.com/QuintinShaw/pi-dynamic-workflows).

## Install

```bash
pip install pi-dynamic-workflows-py
```

## Overview

The `workflow` tool lets the LLM write a Python orchestration script that
fans work out across isolated subagents via `agent()`, `parallel()`,
`pipeline()`, and `phase()`.

## Built-in Patterns

- `/deep-research` — Research a question across the web with cross-checked sources
- `/adversarial-review` — Investigate then cross-check findings with skeptical reviewers
- `/code-review` — Multi-angle parallel code review
- `/multi-perspective` — Analyze a topic from several perspectives, then synthesize
- `/codebase-audit` — Run parallel checks against a codebase scope
