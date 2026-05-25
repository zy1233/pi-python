# AGENTS.md

## Cursor Cloud specific instructions

This is a Python library (`pi-agent-core`). There are no services to start — it is a package installed in editable mode and tested via `pytest`.

### Key commands

| Action | Command |
|--------|---------|
| Install (dev) | `pip install -e ".[dev]"` |
| Run tests | `pytest` (or `pytest -v` for verbose) |
| Run example (no API key) | `PI_USE_MOCK=1 python3 examples/minimal_agent.py` |

### Notes

- **No linter is configured** in `pyproject.toml`. There are no ruff, flake8, mypy, or black settings.
- **`python` is not on PATH** — always use `python3`.
- `pytest` and other scripts install to `~/.local/bin`. Ensure `PATH` includes this directory (it should already be on PATH in most shells, but if `pytest` is not found, run `export PATH="$HOME/.local/bin:$PATH"`).
- All tests use a mock stream (`pi_agent_core/tests/mock_stream.py`) — **no API keys are needed** to run the test suite or the mock example.
- `asyncio_mode = "auto"` is set in `pyproject.toml`, so async test functions are automatically detected by `pytest-asyncio`.
