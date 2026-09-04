from pathlib import Path

from pi_agent_cli.benchmarks.task_loader import discover_tasks, load_task_from_dir


def test_discover_tasks():
    repo_root = Path(__file__).resolve().parents[3]
    tasks_dir = repo_root / "benchmarks" / "tasks"
    tasks = discover_tasks(tasks_dir)
    assert len(tasks) >= 2

    task_names = {t.name for t in tasks}
    assert "regex-log" in task_names
    assert "calc-eval" in task_names


def test_load_regex_task():
    repo_root = Path(__file__).resolve().parents[3]
    task_dir = repo_root / "benchmarks" / "tasks" / "regex-log"
    task = load_task_from_dir(task_dir)
    assert task.name == "regex-log"
    assert task.suite == "terminal-bench"
    assert "regex" in task.instruction.lower()
    assert task.verifier_test is not None
    assert task.verifier_test.name == "test_outputs.py"
