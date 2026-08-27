# Pelican on a bicycle — pi TUI foundation benchmark

Informal benchmark popularized by [Simon Willison](https://simonwillison.net/2025/Jun/6/six-months-in-llms/) and discussed by Andrej Karpathy: ask a text LLM to emit **SVG code** of a pelican riding a bicycle. It stress-tests spatial composition and code structure in one cheap prompt.

pi-python uses it as a **product smoke test** for the same Python agent path the Rust TUI (`pi`) spawns over ACP.

## Prompt

```text
Generate an SVG of a pelican riding a bicycle. Output only valid SVG markup with xmlns and a viewBox, no markdown fences or explanation.
```

Canonical constant: `pi_agent_cli.benchmarks.pelican.PELCAN_PROMPT`.

## What PASS means

Automated checks are **structural**, not artistic (no Elo ranking):

| Check | Meaning |
|-------|---------|
| `extracted` | Response contains parseable `<svg>...</svg>` |
| `svg_root` | Root element present |
| `xmlns` | SVG namespace declared |
| `viewBox` | viewBox attribute present |
| `geometry` | At least one `path`, `circle`, `rect`, `ellipse`, or `polygon` |
| `min_size` | SVG length ≥ 200 characters |

Open the saved file in a browser for qualitative review.

## Artifacts

Successful runs save to:

```text
~/.pi-python/benchmarks/pelican/pelican-<UTC-timestamp>.svg
```

(`PI_HOME` overrides `~/.pi-python`.)

## Run

### Headless (same agent as TUI, no Rust)

```powershell
. $env:USERPROFILE\.pi-python\local.env.ps1
d:\work\pi-python\.venv-tui\Scripts\python.exe scripts\smoke_pelican.py
```

### pytest (real LLM)

```powershell
$env:REAL_LLM_API_KEY = 'sk-...'
.venv\Scripts\python.exe -m pytest packages/pi-agent-cli/tests/test_pelican_real_llm.py -m real_llm -v
```

Mock/unit tests for extract/validate only:

```powershell
.venv\Scripts\python.exe -m pytest packages/pi-agent-cli/tests/test_pelican_benchmark.py -v
```

### Full TUI (manual)

1. Start `pi` in WSL (see `docs/WINDOWS.md`).
2. Paste the prompt above at the input.
3. Confirm streaming assistant output contains SVG (or open the latest file under `~/.pi-python/benchmarks/pelican/` if you ran headless first).

Structural PASS in headless strongly suggests the TUI path is wired; TUI-specific rendering is still worth a quick visual check.

## References

- [simonw/pelican-bicycle](https://github.com/simonw/pelican-bicycle)
- [The last six months in LLMs, illustrated by pelicans on bicycles](https://simonwillison.net/2025/Jun/6/six-months-in-llms/)
