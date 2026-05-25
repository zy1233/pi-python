# AGENTS.md

## Cursor Cloud specific instructions

This is a Python library (`pi-agent-core`). There are no services to start — it is a package installed in editable mode and tested via `pytest`.

### Key commands

| Action | Command |
|--------|---------|
| Install (dev) | `pip install -e ".[dev]"` |
| Lint check | `ruff check .` |
| Format check | `ruff format --check .` |
| Auto-fix lint | `ruff check --fix .` |
| Auto-format | `ruff format .` |
| Run tests | `pytest` (or `pytest -v` for verbose) |
| Run example (no API key) | `PI_USE_MOCK=1 python3 examples/minimal_agent.py` |

### Notes

- **Ruff** is the linter and formatter. Config is in `pyproject.toml` under `[tool.ruff]`. Rules enabled: E, F, I, UP, B, SIM, RUF. Line length: 100. Target: Python 3.11.
- **`python` is not on PATH** — always use `python3`.
- `pytest` and other scripts install to `~/.local/bin`. Ensure `PATH` includes this directory (it should already be on PATH in most shells, but if `pytest` is not found, run `export PATH="$HOME/.local/bin:$PATH"`).
- All tests use a mock stream (`pi_agent_core/tests/mock_stream.py`) — **no API keys are needed** to run the test suite or the mock example.
- `asyncio_mode = "auto"` is set in `pyproject.toml`, so async test functions are automatically detected by `pytest-asyncio`.
