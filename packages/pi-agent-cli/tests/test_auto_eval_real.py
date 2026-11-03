"""The real-model run of ``scripts/auto_eval.py`` (section 6.4 of the design), with the network
replaced by a fake ``urlopen``. No key of a person and no connection: the key in these tests is
a made-up string, and what is asserted is what the design asks of a real run:

- the key is read from the environment and appears in the request header only,
- it is never printed, logged or written (also not when the provider echoes it back),
- the money cap is a cap: when it is used up, no more requests go out,
- every failure (HTTP error, bad body, cut-off answer, timeout) is an ASK, never an ALLOW,
- the scratch folder is outside the repository, and a recorded cassette fits the policy.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

from pi_agent_cli.auto_classifier import MAX_OUTPUT_TOKENS, POLICY

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS = Path(__file__).resolve().parent / "data" / "auto_corpus"
KEY = "sk-or-v1-FAKE-0123456789abcdef-FAKE"


def _load():
    # The mutation check sets AUTO_EVAL_SCRIPT to a changed copy of the script.
    script = Path(os.environ.get("AUTO_EVAL_SCRIPT") or REPO_ROOT / "scripts/auto_eval.py")
    spec = importlib.util.spec_from_file_location("auto_eval", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ev = _load()
CASES = ev.load_corpus(CORPUS)
SAFE_REACHING = [c for c in CASES if ev.reaches_model(c) and c["label"] == "safe"]


class _Response:
    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _answer(
    text: str | None = "ALLOW",
    *,
    finish: str = "stop",
    cost: float | None = 0.00003,
    tokens: tuple[int, int] = (500, 3),
    **extra,
) -> dict:
    usage: dict = {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}
    if cost is not None:
        usage["cost"] = cost
    return {
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": usage,
        **extra,
    }


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid", code, "x", {}, io.BytesIO(b""))


class FakeNet:
    """A replacement for ``urllib.request.urlopen``. *handler* gets the number of the request
    (from 1) and the request, and gives a payload (a dict) or an exception to raise."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.requests: list = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        result = self.handler(len(self.requests), request)
        if isinstance(result, BaseException):
            raise result
        return _Response(result)


@pytest.fixture
def net(monkeypatch):
    def install(handler) -> FakeNet:
        fake = FakeNet(handler)
        monkeypatch.setattr(ev.urllib.request, "urlopen", fake)
        return fake

    return install


@pytest.fixture
def no_sleep(monkeypatch) -> list[float]:
    waited: list[float] = []
    monkeypatch.setattr(ev.time, "sleep", waited.append)
    return waited


def _model(*, retries: int = 0, timeout: float = 5.0, cap: float = 1.0):
    budget = ev.Budget(cap)
    return ev.HttpModel("vendor/small", KEY, budget, timeout=timeout, retries=retries), budget


async def _run(backend, budget, cases=None, *, runs: int = 1) -> dict:
    with ev.quiet_classifier():
        return await ev.run_model(
            cases or SAFE_REACHING[:1], backend, runs=runs, breaker=False, budget=budget
        )


def _headers(request) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


# ------------------------------------------------------------------------- the request


async def test_the_request_has_the_policy_and_the_call_and_the_key_only_in_the_header(net) -> None:
    fake = net(lambda n, r: _answer("ALLOW"))
    backend, budget = _model()
    run = await _run(backend, budget)
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.get_method() == "POST"
    headers = _headers(request)
    assert headers["authorization"] == f"Bearer {KEY}"
    assert headers["user-agent"].startswith("pi-python-auto-eval/")
    body = json.loads(request.data)
    assert body["model"] == "vendor/small"
    assert body["messages"][0] == {"role": "system", "content": POLICY}
    assert body["messages"][1]["role"] == "user"
    assert ev.EVAL_TOKEN in body["messages"][1]["content"]
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == MAX_OUTPUT_TOKENS == 16
    assert "tools" not in body and "stream" not in body
    assert KEY not in request.data.decode("utf-8")
    assert run["stats"]["allowed"] == 1
    assert run["gate_failures"] == []


async def test_the_cost_of_the_response_is_counted_and_a_missing_cost_is_estimated(net) -> None:
    fake = net(lambda n, r: _answer("ALLOW", cost=0.00004 if n == 1 else None))
    backend, budget = _model()
    await _run(backend, budget, runs=2)
    assert len(fake.requests) == 2
    assert budget.requests == 2 and budget.estimated == 1
    estimate = 500 * ev.ESTIMATE_IN_USD + 3 * ev.ESTIMATE_OUT_USD
    assert budget.spent == pytest.approx(0.00004 + estimate)
    assert [r["cost_estimated"] for r in backend.log] == [False, True]
    assert backend.log[0]["tokens_in"] == 500 and backend.log[0]["tokens_out"] == 3


async def test_a_boolean_or_text_cost_is_not_a_cost(net) -> None:
    net(lambda n, r: _answer("ALLOW", cost=True))
    backend, budget = _model()
    await _run(backend, budget)
    assert budget.estimated == 1
    assert budget.spent == pytest.approx(500 * ev.ESTIMATE_IN_USD + 3 * ev.ESTIMATE_OUT_USD)


async def test_the_key_is_taken_out_of_an_answer_that_the_provider_echoes(net) -> None:
    net(lambda n, r: _answer(f"ALLOW {KEY}", error={"message": f"bad key {KEY}"}))
    backend, budget = _model()
    run = await _run(backend, budget)
    text = json.dumps(backend.log)
    assert KEY not in text
    assert "[key]" in text
    assert backend.errors["error in the body"] == 1
    # "ALLOW" and more is not the answer: the call is asked.
    assert run["stats"]["allowed"] == 0


# ------------------------------------------------------------------------ every failure asks


@pytest.mark.parametrize("code", [400, 401, 403, 404, 500, 502])
async def test_an_http_error_is_an_ask_and_is_counted(net, code) -> None:
    fake = net(lambda n, r: _http_error(code))
    backend, budget = _model(retries=0)
    run = await _run(backend, budget)
    assert len(fake.requests) == 1
    assert run["stats"]["allowed"] == 0 and run["stats"]["failures"] == 1
    assert run["allowed_by_model"] == 0
    assert run["false_ask_safe"] == 1.0
    assert backend.errors == {f"HTTP {code}": 1}
    assert budget.requests == 0 and budget.spent == 0.0


async def test_a_network_error_is_an_ask(net) -> None:
    net(lambda n, r: urllib.error.URLError("no route"))
    backend, budget = _model()
    run = await _run(backend, budget)
    assert run["stats"]["allowed"] == 0 and run["stats"]["failures"] == 1
    assert backend.errors == {"URLError": 1}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"error": {"message": "overloaded"}},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}, "finish_reason": "stop"}]},
        _answer(None),
        _answer(""),
        _answer("ALLOW", finish="length"),
        _answer("ALLOW", finish="content_filter"),
        _answer("ALLOW", finish=None),
        _answer("allow"),
        _answer("ALLOW\nASK UNCLEAR"),
        _answer("ASK UNCLEAR\nALLOW"),
        _answer("Sure. ALLOW"),
    ],
)
async def test_a_response_that_is_not_one_exact_answer_is_an_ask(net, payload) -> None:
    net(lambda n, r: payload)
    backend, budget = _model()
    run = await _run(backend, budget)
    assert run["stats"]["allowed"] == 0
    assert run["allowed_by_model"] == 0
    assert run["gate_failures"] == []


async def test_a_model_that_thinks_and_runs_out_of_tokens_asks_for_every_call(net) -> None:
    # A gateway model that thinks by default spends the 16 tokens on the thinking: the content is
    # empty and the finish reason is "length". The product reads that as no answer: ASK.
    net(lambda n, r: _answer("", finish="length", tokens=(500, 16)))
    backend, budget = _model()
    run = await _run(backend, budget, SAFE_REACHING[:6])
    assert run["stats"]["allowed"] == 0 and run["stats"]["failures"] == 6
    assert set(run["safe_asked"].values()) == {"model.unparseable"}


async def test_a_request_that_takes_longer_than_the_timeout_is_an_ask(net) -> None:
    release = threading.Event()

    def slow(n, request):
        release.wait(1.0)
        return _answer("ALLOW")

    net(slow)
    backend, budget = _model(timeout=0.05)
    run = await _run(backend, budget)
    release.set()
    assert run["stats"]["allowed"] == 0
    assert set(run["safe_asked"].values()) == {"model.timeout"}
    assert budget.unknown == 1


# ---------------------------------------------------------------------------- the retries


async def test_a_429_is_asked_again_after_a_wait_and_then_counted_once(net, no_sleep) -> None:
    fake = net(lambda n, r: _http_error(429) if n <= 2 else _answer("ALLOW"))
    backend, budget = _model(retries=3)
    run = await _run(backend, budget)
    assert len(fake.requests) == 3
    assert no_sleep == [0.5, 1.0]
    assert backend.errors == {"HTTP 429 (asked again)": 2}
    assert run["stats"]["allowed"] == 1 and run["stats"]["failures"] == 0
    assert budget.requests == 1


async def test_the_retries_stop_at_the_limit_and_the_call_is_asked(net, no_sleep) -> None:
    fake = net(lambda n, r: _http_error(503))
    backend, budget = _model(retries=2)
    run = await _run(backend, budget)
    assert len(fake.requests) == 3
    assert backend.errors == {"HTTP 503 (asked again)": 2, "HTTP 503": 1}
    assert run["stats"]["allowed"] == 0 and run["stats"]["failures"] == 1


async def test_an_error_that_a_second_request_cannot_cure_is_not_asked_again(net, no_sleep) -> None:
    fake = net(lambda n, r: _http_error(401))
    backend, budget = _model(retries=3)
    await _run(backend, budget)
    assert len(fake.requests) == 1
    assert no_sleep == []


def test_the_retries_are_limited_by_the_table_of_waits() -> None:
    backend = ev.HttpModel("m", KEY, ev.Budget(1.0), timeout=1.0, retries=99)
    assert backend.retries == len(ev.RETRY_WAITS)
    assert ev.HttpModel("m", KEY, ev.Budget(1.0), timeout=1.0, retries=-4).retries == 0


# ------------------------------------------------------------------------------- the cap


async def test_the_cap_stops_the_run_and_no_more_requests_go_out(net) -> None:
    fake = net(lambda n, r: _answer("ASK UNCLEAR", cost=0.01))
    backend, budget = _model(cap=0.025)
    run = await _run(backend, budget, SAFE_REACHING[:10])
    assert run["stopped_at_cap"] is True
    assert len(fake.requests) == 3
    assert budget.spent == pytest.approx(0.03)
    assert budget.exhausted


async def test_a_backend_that_finds_the_cap_used_up_sends_nothing(net) -> None:
    fake = net(lambda n, r: _answer("ALLOW"))
    backend, budget = _model(cap=0.01)
    budget.spent = 0.01
    with ev.quiet_classifier():
        run = await ev.run_model(SAFE_REACHING[:3], backend, breaker=False, budget=budget)
    assert fake.requests == []
    assert run["stopped_at_cap"] is True
    assert run["stats"]["allowed"] == 0


async def test_a_backend_that_is_used_up_sends_nothing_when_the_run_does_not_look_at_the_cap(
    net,
) -> None:
    """The cap is read by the request too: a caller that passes no budget cannot get a request
    out after the money is spent."""
    fake = net(lambda n, r: _answer("ALLOW"))
    backend, budget = _model(cap=0.01)
    budget.spent = 0.01
    with ev.quiet_classifier():
        run = await ev.run_model(SAFE_REACHING[:3], backend, breaker=False)
    assert fake.requests == []
    assert backend.calls == 0
    assert run["stats"]["allowed"] == 0
    assert run["stats"]["failures"] == 3


# ------------------------------------------------------------------------ the command line


def _run_main(argv: list[str], capsys) -> tuple[int, str]:
    code = ev.main(argv)
    out = capsys.readouterr()
    return code, out.out + out.err


def test_a_real_run_needs_the_key_and_sends_nothing(net, monkeypatch, capsys, tmp_path) -> None:
    fake = net(lambda n, r: _answer("ALLOW"))
    monkeypatch.delenv(ev.KEY_VARIABLE, raising=False)
    code, text = _run_main(["--real", "--out", str(tmp_path / "scratch")], capsys)
    assert code == 2
    assert f"{ev.KEY_VARIABLE} is not set" in text
    assert fake.requests == []


def test_a_real_run_with_an_empty_key_is_the_same_as_no_key(net, monkeypatch, capsys) -> None:
    fake = net(lambda n, r: _answer("ALLOW"))
    monkeypatch.setenv(ev.KEY_VARIABLE, "")
    code, _ = _run_main(["--real"], capsys)
    assert code == 2
    assert fake.requests == []


def test_a_scratch_folder_inside_the_repository_is_refused_and_not_made(
    net, monkeypatch, capsys
) -> None:
    fake = net(lambda n, r: _answer("ALLOW"))
    monkeypatch.setenv(ev.KEY_VARIABLE, KEY)
    inside = ev.REPO_ROOT / "scratch-of-the-eval-test"
    code, text = _run_main(["--real", "--out", str(inside)], capsys)
    assert code == 2
    assert "outside the repository" in text
    assert not inside.exists()
    assert fake.requests == []
    assert KEY not in text


def test_the_repository_itself_is_not_a_scratch_folder(net, monkeypatch, capsys) -> None:
    net(lambda n, r: _answer("ALLOW"))
    monkeypatch.setenv(ev.KEY_VARIABLE, KEY)
    code, _ = _run_main(["--real", "--out", str(ev.REPO_ROOT)], capsys)
    assert code == 2


def test_a_cassette_cannot_be_recorded_over_the_other_path(
    net, monkeypatch, capsys, tmp_path
) -> None:
    fake = net(lambda n, r: _answer("ALLOW"))
    monkeypatch.setenv(ev.KEY_VARIABLE, KEY)
    argv = ["--real", "--via", "langchain", "--out", str(tmp_path), "--record-cassette"]
    code, text = _run_main([*argv, str(tmp_path / "c.json")], capsys)
    assert code == 2
    assert "--record-cassette needs --via http" in text
    assert fake.requests == []


def _files_text(folder: Path) -> str:
    return "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in folder.rglob("*")
        if path.is_file()
    )


def test_a_real_run_never_prints_or_writes_the_key(net, monkeypatch, capsys, tmp_path) -> None:
    # The provider answers every request with the key in the text and in an error field: the
    # worst case for a leak. The key must be in no output and in no file of the run.
    fake = net(lambda n, r: _answer(f"ASK UNCLEAR {KEY}", error={"message": KEY}))
    monkeypatch.setenv(ev.KEY_VARIABLE, KEY)
    scratch = tmp_path / "scratch"
    code, text = _run_main(
        [
            "--real",
            "--split",
            "dev",
            "--runs",
            "1",
            "--model",
            "vendor/small",
            "--out",
            str(scratch),
        ],
        capsys,
    )
    assert code == 0
    assert len(fake.requests) == sum(ev.reaches_model(c) for c in CASES if c["split"] == "dev")
    assert KEY not in text
    assert "never printed or written" in text
    assert KEY not in _files_text(scratch)
    assert KEY not in _files_text(tmp_path)
    log = (scratch / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == len(fake.requests)
    assert all(json.loads(line)["model"] == "vendor/small" for line in log)
    summary = json.loads((scratch / "summary.json").read_text(encoding="utf-8"))
    assert summary[0]["name"] == "vendor/small"
    assert summary[0]["gate_failures"] == []
    assert "real spend:" in text


def test_the_real_spend_line_is_the_sum_of_the_costs_and_says_what_it_estimated(
    net, monkeypatch, capsys, tmp_path
) -> None:
    fake = net(lambda n, r: _answer("ASK UNCLEAR", cost=0.0001 if n % 2 else None))
    monkeypatch.setenv(ev.KEY_VARIABLE, KEY)
    code, text = _run_main(
        ["--real", "--split", "dev", "--runs", "1", "--out", str(tmp_path / "s")], capsys
    )
    assert code == 0
    estimate = 500 * ev.ESTIMATE_IN_USD + 3 * ev.ESTIMATE_OUT_USD
    odd = (len(fake.requests) + 1) // 2
    spent = odd * 0.0001 + (len(fake.requests) - odd) * estimate
    assert f"real spend: {spent:.6f} USD in {len(fake.requests)} request(s)" in text
    assert "gave no cost: the cap counted an estimate" in text


def test_a_run_that_hits_the_cap_says_so_and_exits_with_3(
    net, monkeypatch, capsys, tmp_path
) -> None:
    fake = net(lambda n, r: _answer("ASK UNCLEAR", cost=0.01))
    monkeypatch.setenv(ev.KEY_VARIABLE, KEY)
    argv = ["--real", "--split", "dev", "--runs", "1", "--max-usd", "0.025"]
    code, text = _run_main([*argv, "--out", str(tmp_path / "s")], capsys)
    assert code == 3
    assert len(fake.requests) == 3
    assert "real spend: 0.030000 USD in 3 request(s)" in text


def test_a_run_that_allows_a_destructive_call_exits_with_1(
    net, monkeypatch, capsys, tmp_path
) -> None:
    net(lambda n, r: _answer("ALLOW"))
    monkeypatch.setenv(ev.KEY_VARIABLE, KEY)
    code, text = _run_main(
        ["--real", "--split", "dev", "--runs", "1", "--out", str(tmp_path / "s")], capsys
    )
    assert code == 1
    assert "GATE FAILED: false-allow on destructive" in text


# -------------------------------------------------------------------------------- cassette


def test_a_cassette_is_recorded_from_the_first_run_and_plays_back_with_the_same_gate(
    net, monkeypatch, capsys, tmp_path
) -> None:
    def handler(n, request):
        # Each case gets a different recorded text (an exact answer) so a mix-up would show.
        return _answer("ASK UNCLEAR" if n % 3 else "ASK NETWORK", tokens=(500 + n, 3))

    fake = net(handler)
    monkeypatch.setenv(ev.KEY_VARIABLE, KEY)
    cassette_path = tmp_path / "cassette.json"
    code, text = _run_main(
        [
            "--real",
            "--runs",
            "2",
            "--model",
            "vendor/small",
            "--out",
            str(tmp_path / "s"),
            "--record-cassette",
            str(cassette_path),
        ],
        capsys,
    )
    assert code == 0, text
    reaching = [c for c in CASES if ev.reaches_model(c)]
    assert len(fake.requests) == 2 * len(reaching)
    assert f"cassette written: {cassette_path} ({len(reaching)} answers)" in text
    cassette = json.loads(cassette_path.read_text(encoding="utf-8"))
    assert cassette["model"] == "vendor/small"
    assert cassette["runs"] == 2
    assert sorted(cassette["answers"]) == sorted(c["id"] for c in reaching)
    assert KEY not in json.dumps(cassette)
    assert ev.cassette_problems(cassette, CASES) == []
    # Two requests for each case: the cassette keeps the first one (requests 1 and 3 here).
    one, two = (cassette["answers"][c["id"]] for c in reaching[:2])
    assert (one["text"], one["tokens_in"]) == ("ASK UNCLEAR", 501)
    assert (two["text"], two["tokens_in"]) == ("ASK NETWORK", 503)
    code, text = _run_main(["--replay", "--cassette", str(cassette_path)], capsys)
    assert code == 0, text
    assert "model vendor/small" in text
    assert "gates: ok" in text


def test_a_recorded_cassette_that_misses_an_answer_does_not_fit(
    net, monkeypatch, capsys, tmp_path
) -> None:
    net(lambda n, r: _http_error(401) if n == 5 else _answer("ASK UNCLEAR"))
    monkeypatch.setenv(ev.KEY_VARIABLE, KEY)
    cassette_path = tmp_path / "cassette.json"
    code, _ = _run_main(
        [
            "--real",
            "--runs",
            "1",
            "--retries",
            "0",
            "--out",
            str(tmp_path / "s"),
            "--record-cassette",
            str(cassette_path),
        ],
        capsys,
    )
    assert code == 0
    cassette = json.loads(cassette_path.read_text(encoding="utf-8"))
    problems = ev.cassette_problems(cassette, CASES)
    assert len(problems) == 1 and "no recorded answer" in problems[0]
    code, text = _run_main(["--replay", "--cassette", str(cassette_path)], capsys)
    assert code == 1
    assert "The cassette does not fit" in text
