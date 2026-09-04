"""Assemble the 30 FrontierHarness Eval benchmark tasks into a unified directory."""

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TB_ROOT = REPO_ROOT / ".cache" / "terminal-bench-2-1" / "tasks"
FH_ROOT = REPO_ROOT / ".cache" / "frontier-harness-eval" / "tasks"
DEST_ROOT = REPO_ROOT / "benchmarks" / "frontier_30"

DEST_ROOT.mkdir(parents=True, exist_ok=True)

TB_TASKS = [
    "regex-log",
    "openssl-selfsigned-cert",
    "polyglot-c-py",
    "sqlite-db-truncate",
    "git-leak-recovery",
    "log-summary-date-ranges",
    "constraints-scheduling",
    "gcode-to-text",
    "dna-insert",
    "largest-eigenval",
    "merge-diff-arc-agi-task",
    "vulnerable-secret",
    "extract-elf",
    "build-cython-ext",
    "kv-store-grpc",
    "chess-best-move",
    "db-wal-recovery",
    "code-from-image",
    "modernize-scientific-stack",
    "multi-source-data-merger",
    "sanitize-git-repo",
]

DEEPSWE_TASKS = [
    "anko-typed-variable-bindings",
    "arktype-json-schema-refs-dependencies",
    "fastapi-deprecation-response-headers",
    "httpx-multipart-response-parsing",
    "expr-try-catch-errors",
    "python-statemachine-state-data-scoping",
    "katex-multicolumn-array-spans",
    "scc-bounded-memory-spilling",
    "meriyah-explicit-resource-declarations",
]


def setup_tb_tasks():
    for name in TB_TASKS:
        src = TB_ROOT / name
        dest = DEST_ROOT / f"terminal-bench_{name}"
        dest.mkdir(parents=True, exist_ok=True)

        # Copy instruction and task.toml
        if (src / "instruction.md").is_file():
            shutil.copy2(src / "instruction.md", dest / "instruction.md")
        if (src / "task.toml").is_file():
            shutil.copy2(src / "task.toml", dest / "task.toml")

        # Copy tests
        if (src / "tests").is_dir():
            dest_tests = dest / "tests"
            dest_tests.mkdir(parents=True, exist_ok=True)
            for item in (src / "tests").iterdir():
                if item.is_file():
                    shutil.copy2(item, dest_tests / item.name)

        # Copy environment fixtures
        if (src / "environment").is_dir():
            dest_env = dest / "environment"
            dest_env.mkdir(parents=True, exist_ok=True)
            for item in (src / "environment").iterdir():
                if item.name in ("Dockerfile", ".dockerignore", ".ruff_cache"):
                    continue
                dest_item = dest_env / item.name
                if item.is_dir():
                    shutil.copytree(item, dest_item, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest_item)
        print(f"  [TB] {name} -> {dest.name}")


def setup_deepswe_tasks():
    for name in DEEPSWE_TASKS:
        src = FH_ROOT / name
        dest = DEST_ROOT / f"datacurve_{name}"
        dest.mkdir(parents=True, exist_ok=True)

        if (src / "instruction.md").is_file():
            shutil.copy2(src / "instruction.md", dest / "instruction.md")
        if (src / "task.toml").is_file():
            shutil.copy2(src / "task.toml", dest / "task.toml")
        print(f"  [SWE] {name} -> {dest.name}")


if __name__ == "__main__":
    print("Assembling 30 FrontierHarness tasks into benchmarks/frontier_30...")
    setup_tb_tasks()
    setup_deepswe_tasks()
    print("Done! Total tasks:", len(list(DEST_ROOT.iterdir())))
