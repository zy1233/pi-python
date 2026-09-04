import importlib
import math
import os
import sys
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def load_calculator():
    workspace = Path(os.environ.get("WORKSPACE_DIR", "."))
    calc_path = workspace / "calculator.py"
    assert calc_path.exists(), f"calculator.py not found in {workspace}"

    # Ensure forbidden functions are not used
    content = calc_path.read_text(encoding="utf-8")
    assert "eval(" not in content, "Forbidden function eval() detected!"
    assert "exec(" not in content, "Forbidden function exec() detected!"

    sys.path.insert(0, str(workspace.resolve()))
    try:
        if "calculator" in sys.modules:
            del sys.modules["calculator"]
        mod = importlib.import_module("calculator")
        return mod.evaluate
    finally:
        if str(workspace.resolve()) in sys.path:
            sys.path.remove(str(workspace.resolve()))


def test_basic_arithmetic(load_calculator):
    eval_fn = load_calculator
    assert eval_fn("1 + 1") == 2
    assert eval_fn("10 - 4") == 6
    assert eval_fn("3 * 5") == 15
    assert eval_fn("20 / 4") == 5


def test_precedence_and_associativity(load_calculator):
    eval_fn = load_calculator
    assert eval_fn("2 + 3 * 4") == 14
    assert eval_fn("2 * 3 + 4") == 10
    assert eval_fn("10 - 2 - 3") == 5  # left-associative
    assert eval_fn("2 ^ 3 ^ 2") == 512  # right-associative: 2^(3^2) = 2^9 = 512


def test_parentheses(load_calculator):
    eval_fn = load_calculator
    assert eval_fn("(2 + 3) * 4") == 20
    assert eval_fn("((1 + 2) * (3 + 4)) / 7") == 3


def test_unary_minus(load_calculator):
    eval_fn = load_calculator
    assert eval_fn("-5 + 3") == -2
    assert eval_fn("2 * -3") == -6
    assert eval_fn("-(2 + 3)") == -5


def test_floats(load_calculator):
    eval_fn = load_calculator
    assert math.isclose(eval_fn("3.5 + 2.5"), 6.0)
    assert math.isclose(eval_fn("1 / 2"), 0.5)


def test_division_by_zero(load_calculator):
    eval_fn = load_calculator
    with pytest.raises(ZeroDivisionError):
        eval_fn("10 / 0")


def test_syntax_errors(load_calculator):
    eval_fn = load_calculator
    with pytest.raises(ValueError):
        eval_fn("(1 + 2")
    with pytest.raises(ValueError):
        eval_fn("1 * / 2")
    with pytest.raises(ValueError):
        eval_fn("abc + 1")
