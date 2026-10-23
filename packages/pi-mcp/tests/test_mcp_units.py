"""The parts of pi-mcp that need no server: names, results, secrets, errors, environment."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED, ErrorData
from pi_mcp import errors, settings, transport
from pi_mcp.redact import MASK, one_line, redact, secrets_of
from pi_mcp.tools import NAME_LIMIT, public_name, shape_result

from pi_agent_core.extensions import ClientMcpServer
from pi_agent_core.types import AgentToolResult


def server(**kwargs: Any) -> ClientMcpServer:
    fields: dict[str, Any] = {"name": "s", "transport": "stdio", "command": "npx"}
    fields.update(kwargs)
    return ClientMcpServer(**fields)


def text_result(*texts: str, details: Any = None) -> AgentToolResult:
    return AgentToolResult(content=[{"type": "text", "text": t} for t in texts], details=details)


# ---------------------------------------------------------------------------
# Tool names
# ---------------------------------------------------------------------------


class TestPublicName:
    def test_the_name_has_the_server_and_the_tool(self):
        assert public_name("files", "read", set()) == "mcp__files__read"

    def test_a_tool_named_like_a_builtin_cannot_take_its_place(self):
        assert public_name("evil", "bash", set()) == "mcp__evil__bash"
        assert public_name("evil", "read", set()) != "read"

    def test_characters_a_model_provider_refuses_become_underscores(self):
        assert public_name("my server", "do.it/now", set()) == "mcp__my_server__do_it_now"
        assert public_name("é", "tool", set()) == "mcp_____tool"

    def test_a_long_name_is_cut_to_the_limit_and_keeps_a_hash(self):
        taken: set[str] = set()
        name = public_name("server", "t" * 200, taken)
        other = public_name("server", "t" * 199 + "u", taken)

        assert len(name) == NAME_LIMIT
        assert len(other) == NAME_LIMIT
        assert name != other  # the hash is of the whole name

    def test_the_same_long_name_gives_the_same_result(self):
        assert public_name("s", "t" * 200, set()) == public_name("s", "t" * 200, set())

    def test_two_tools_that_come_out_the_same_get_a_number(self):
        taken: set[str] = set()

        names = [
            public_name("s", "a.b", taken),
            public_name("s", "a_b", taken),
            public_name("s", "a b", taken),
        ]

        assert names == ["mcp__s__a_b", "mcp__s__a_b_2", "mcp__s__a_b_3"]
        assert taken == set(names)

    def test_a_name_that_was_taken_is_not_given_again(self):
        taken = {"mcp__s__t"}

        assert public_name("s", "t", taken) == "mcp__s__t_2"

    def test_a_cut_name_that_collides_gets_a_unique_name_of_the_right_size(self):
        taken: set[str] = set()
        first = public_name("s", "x" * 100, taken)
        second = public_name("s", "x" * 100, taken)

        assert first != second
        assert len(second) <= NAME_LIMIT


# ---------------------------------------------------------------------------
# The shape of a result
# ---------------------------------------------------------------------------


class TestShapeResult:
    def test_text_stays_and_the_source_is_in_the_details(self):
        shaped = shape_result(text_result("hello"), server="s", tool="t")

        assert shaped.content == [{"type": "text", "text": "hello"}]
        assert shaped.details == {"mcp": {"server": "s", "tool": "t"}}

    def test_structured_content_alone_becomes_json_text(self):
        result = text_result(details={"artifact": {"structured_content": {"a": 1, "é": ["x"]}}})

        shaped = shape_result(result, server="s", tool="t")

        assert [b["type"] for b in shaped.content] == ["text"]
        assert json.loads(shaped.content[0]["text"]) == {"a": 1, "é": ["x"]}
        assert "é" in shaped.content[0]["text"]  # not escaped: the model reads it

    def test_structured_content_is_dropped_when_there_is_text(self):
        result = text_result("said", details={"artifact": {"structured_content": {"a": 1}}})

        shaped = shape_result(result, server="s", tool="t")

        assert shaped.content == [{"type": "text", "text": "said"}]
        assert shaped.details == {"mcp": {"server": "s", "tool": "t"}}

    def test_a_result_with_nothing_in_it_says_so(self):
        shaped = shape_result(text_result(), server="s", tool="t")

        assert shaped.content == [{"type": "text", "text": "(The tool returned no content.)"}]

    def test_blank_text_counts_as_nothing(self):
        shaped = shape_result(text_result("  \n"), server="s", tool="t")

        assert shaped.content == [{"type": "text", "text": "(The tool returned no content.)"}]

    def test_an_image_alone_is_left_alone(self):
        image = {"type": "image", "data": "AAAA", "mimeType": "image/png"}
        result = AgentToolResult(content=[image])

        shaped = shape_result(result, server="s", tool="t")

        assert shaped.content == [image]

    def test_a_long_text_is_cut_and_the_note_says_how_much(self):
        shaped = shape_result(text_result("a" * 5000), server="s", tool="t", limit=1000)

        *blocks, note = shaped.content
        assert "".join(b["text"] for b in blocks) == "a" * 1000
        assert note["text"].startswith("[Output cut: the first ")
        assert "are shown.]" in note["text"]

    def test_the_budget_is_shared_by_all_text_blocks(self):
        shaped = shape_result(
            text_result("a" * 600, "b" * 600, "c" * 600), server="s", tool="t", limit=1000
        )

        texts = [b["text"] for b in shaped.content if b["text"].startswith(("a", "b", "c"))]
        assert "".join(texts) == "a" * 600 + "b" * 400  # the third block gets nothing
        assert sum(len(t) for t in texts) == 1000

    def test_a_cut_does_not_split_a_character(self):
        shaped = shape_result(text_result("é" * 100), server="s", tool="t", limit=101)

        kept = shaped.content[0]["text"]
        assert kept == "é" * 50  # 100 bytes: the 51st letter would need the 102nd byte
        kept.encode("utf-8")  # does not raise

    def test_images_survive_a_cut(self):
        image = {"type": "image", "data": "AAAA", "mimeType": "image/png"}
        result = AgentToolResult(content=[{"type": "text", "text": "a" * 5000}, image])

        shaped = shape_result(result, server="s", tool="t", limit=100)

        assert image in shaped.content

    def test_terminate_is_kept(self):
        result = AgentToolResult(content=[{"type": "text", "text": "x"}], terminate=True)

        assert shape_result(result, server="s", tool="t").terminate is True


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


class TestSecrets:
    def test_every_value_of_env_and_headers_is_a_secret(self):
        found = secrets_of(
            server(
                env=(("API_KEY", "abcd-1234"), ("PLAIN", "xy")),
                headers=(("X-Api-Key", "key-value-1"),),
            )
        )

        assert "abcd-1234" in found
        assert "key-value-1" in found
        assert "xy" not in found  # too short to be a token, and it would hide ordinary text

    def test_the_token_behind_a_scheme_word_is_a_secret_too(self):
        found = secrets_of(server(headers=(("Authorization", "Bearer sk-live-123456"),)))

        assert "Bearer sk-live-123456" in found
        assert "sk-live-123456" in found

    def test_the_url_and_the_parts_of_it_that_carry_a_secret(self):
        found = secrets_of(
            server(
                transport="http",
                url="https://user:pa%24%24word@example.com/mcp/0123456789abcdef0123?token=tok-123&x=1",
            )
        )

        assert "pa%24%24word" in found
        assert "pa$$word" in found
        assert "tok-123" in found
        assert "0123456789abcdef0123" in found
        assert "mcp" not in found  # a short path segment is no token

    def test_the_longest_secret_comes_first(self):
        found = secrets_of(server(env=(("A", "abcd"), ("B", "abcdefgh"))))

        assert found[0] == "abcdefgh"

    def test_a_secret_inside_a_longer_one_is_masked_as_a_whole(self):
        text = redact(
            "header: Bearer sk-live-123456",
            secrets_of(server(headers=(("A", "Bearer sk-live-123456"),))),
        )

        assert text == f"header: {MASK}"

    def test_redact_masks_all_places(self):
        assert redact("a SECRET b SECRET", ["SECRET"]) == f"a {MASK} b {MASK}"

    def test_redact_with_no_secrets_changes_nothing(self):
        assert redact("plain", []) == "plain"
        assert redact("plain", [""]) == "plain"

    def test_the_repr_of_the_server_shows_no_secret(self):
        s = server(env=(("K", "secret-value-1"),), args=("--token", "secret-arg"))

        assert "secret" not in repr(s)

    def test_one_line_joins_lines_and_control_characters(self):
        assert one_line("a\n\n b\t\x07c ") == "a b c"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_the_first_real_error_comes_out_of_an_exception_group(self):
        group = BaseExceptionGroup(
            "g",
            [
                asyncio.CancelledError(),
                ExceptionGroup("inner", [FileNotFoundError("npx"), OSError("x")]),
            ],
        )

        assert isinstance(errors.first_leaf(group), FileNotFoundError)
        assert errors.is_command_not_found(group)

    def test_only_cancellations_give_the_first_of_them(self):
        group = BaseExceptionGroup("g", [asyncio.CancelledError(), asyncio.CancelledError()])

        assert isinstance(errors.first_leaf(group), asyncio.CancelledError)

    def test_describe_error_is_one_line_clean_and_short(self):
        exc = ValueError("line one with SECRET-TOKEN\nline two " + "x" * 600)

        text = errors.describe_error(exc, ["SECRET-TOKEN"], limit=100)

        assert "\n" not in text
        assert "SECRET-TOKEN" not in text
        assert MASK in text
        assert len(text) <= 100
        assert text.endswith("...")

    def test_a_secret_cut_by_the_limit_is_not_shown_in_part(self):
        secret = "SECRET-TOKEN-VALUE"
        exc = ValueError("x" * 90 + secret)

        text = errors.describe_error(exc, [secret], limit=100)

        assert "SECRET" not in text

    def test_describe_error_for_an_error_without_a_message_names_the_type(self):
        assert errors.describe_error(ValueError(), []) == "ValueError"

    def test_describe_error_uses_the_message_of_an_mcp_error(self):
        exc = McpError(ErrorData(code=-32602, message="Unknown tool: nope"))

        assert errors.describe_error(exc, []) == "Unknown tool: nope"

    def test_the_text_for_the_model_keeps_lines_and_masks_secrets(self):
        exc = RuntimeError("Traceback:\n  line 1\n  token=SECRET-TOKEN")

        text = errors.error_text_for_model(exc, ["SECRET-TOKEN"])

        assert text == f"Traceback:\n  line 1\n  token={MASK}"

    def test_the_text_for_the_model_is_cut_when_it_is_huge(self):
        text = errors.error_text_for_model(RuntimeError("x" * 200_000), [])

        assert text.endswith("[Error text cut.]")
        assert len(text) < 60_000

    def test_the_startup_timeout_names_the_seconds(self):
        assert str(errors.StartupTimeout(60)) == "it did not answer within 60 s"
        assert str(errors.StartupTimeout(0.5)) == "it did not answer within 0.5 s"


class TestConnectionLost:
    def test_a_closed_stream_means_the_request_was_not_sent(self):
        assert errors.connection_lost(anyio.ClosedResourceError()) == "unsent"
        assert errors.connection_lost(anyio.BrokenResourceError()) == "unsent"

    @pytest.mark.parametrize("exc", [httpx.ConnectError("x"), httpx.ConnectTimeout("x")])
    def test_no_connection_made_means_the_request_was_not_sent(self, exc: BaseException):
        assert errors.connection_lost(exc) == "unsent"

    def test_a_connection_that_closed_during_the_request_is_lost(self):
        exc = McpError(ErrorData(code=CONNECTION_CLOSED, message="Connection closed"))

        assert errors.connection_lost(exc) == "lost"

    def test_a_session_that_the_server_ended_means_the_request_was_not_taken(self):
        exc = McpError(ErrorData(code=32600, message="Session terminated"))

        assert errors.connection_lost(exc) == "unsent"

    @pytest.mark.parametrize(
        "exc",
        [
            anyio.EndOfStream(),
            ConnectionResetError(),
            httpx.RemoteProtocolError("x"),
            httpx.ReadError("x"),
            httpx.WriteError("x"),
        ],
    )
    def test_the_errors_of_a_dropped_connection_are_lost(self, exc: BaseException):
        assert errors.connection_lost(exc) == "lost"

    def test_an_error_inside_a_group_is_found(self):
        group = ExceptionGroup("g", [ValueError("x"), anyio.ClosedResourceError()])

        assert errors.connection_lost(group) == "unsent"

    @pytest.mark.parametrize(
        "exc",
        [
            ValueError("x"),
            McpError(ErrorData(code=-32602, message="Unknown tool")),
            httpx.HTTPStatusError("401", request=None, response=None),  # type: ignore[arg-type]
            TimeoutError(),
        ],
    )
    def test_other_errors_do_not_say_the_server_is_gone(self, exc: BaseException):
        assert errors.connection_lost(exc) is None


# ---------------------------------------------------------------------------
# The environment of a child process
# ---------------------------------------------------------------------------


class TestChildEnv:
    def test_the_llm_key_and_other_variables_of_this_process_are_not_passed(self):
        environ = {
            "REAL_LLM_API_KEY": "sk-llm",
            "OPENAI_API_KEY": "sk-openai",
            "PI_HOME": "/home/u/.pi-python",
            "GITHUB_TOKEN": "ghp_x",
            "HTTPS_PROXY": "http://proxy.invalid:3128",
        }

        env = transport.child_env(server(), environ)

        assert env == {"HTTPS_PROXY": "http://proxy.invalid:3128"}

    def test_proxy_and_certificate_variables_are_passed_on(self):
        environ = {
            "HTTP_PROXY": "p1",
            "https_proxy": "p2",
            "NO_PROXY": "localhost",
            "SSL_CERT_FILE": "/ca.pem",
            "NODE_EXTRA_CA_CERTS": "/node-ca.pem",
        }

        assert transport.child_env(server(), environ) == environ

    def test_the_env_of_the_client_wins_and_is_not_expanded(self):
        environ = {"HTTPS_PROXY": "from-agent", "HOME": "/home/u"}
        s = server(env=(("HTTPS_PROXY", "from-client"), ("TOKEN", "${HOME}/x"), ("A", "$HOME")))

        env = transport.child_env(s, environ)

        assert env["HTTPS_PROXY"] == "from-client"
        assert env["TOKEN"] == "${HOME}/x"
        assert env["A"] == "$HOME"

    def test_without_proxy_variables_the_env_is_just_the_one_of_the_client(self):
        s = server(env=(("A", "1"),))

        assert transport.child_env(s, {}) == {"A": "1"}


# ---------------------------------------------------------------------------
# What may be shown of a server
# ---------------------------------------------------------------------------


class TestDescribeTransport:
    def test_stdio_shows_the_program_and_nothing_else(self):
        s = server(command="C:\\Program Files\\nodejs\\npx.cmd", args=("--token", "secret"))

        assert transport.describe_transport(s) == "stdio (npx.cmd)"

    def test_stdio_with_a_unix_path(self):
        assert transport.describe_transport(server(command="/usr/bin/uvx")) == "stdio (uvx)"

    def test_http_shows_the_host_and_nothing_else(self):
        s = server(transport="http", url="https://user:pw@mcp.example.com:8443/path/tok?key=v")

        assert transport.describe_transport(s) == "http (mcp.example.com)"

    def test_a_url_that_does_not_parse_shows_a_question_mark(self):
        assert transport.describe_transport(server(transport="sse", url="http://[bad")) == "sse (?)"


class TestLogStem:
    def test_a_plain_name_is_kept(self):
        assert transport.log_stem("context7") == "context7"
        assert transport.log_stem("my-server_1.0") == "my-server_1.0"

    def test_an_unsafe_name_is_cleaned_and_the_same_name_gives_the_same_result(self):
        stem = transport.log_stem("../../etc/passwd")

        assert "/" not in stem and "\\" not in stem
        assert stem == transport.log_stem("../../etc/passwd")
        assert stem != transport.log_stem("../../etc/passwd2")

    def test_names_that_clean_to_the_same_text_stay_apart(self):
        assert transport.log_stem("a b") != transport.log_stem("a/b")

    def test_an_empty_name_has_a_file_name(self):
        assert transport.log_stem("")


class TestServerLog:
    def test_the_log_goes_to_the_home_of_the_agent(self, tmp_path: Path):
        stream, path = transport.open_server_log(tmp_path, "context7")
        stream.write("hello\n")
        stream.close()

        assert path == tmp_path / "logs" / "mcp" / "context7.log"
        assert "hello" in path.read_text(encoding="utf-8")

    def test_a_second_start_appends(self, tmp_path: Path):
        for line in ("first", "second"):
            stream, path = transport.open_server_log(tmp_path, "s")
            stream.write(line + "\n")
            stream.close()

        content = path.read_text(encoding="utf-8")  # type: ignore[union-attr]
        assert "first" in content and "second" in content

    def test_a_large_log_is_started_over(self, tmp_path: Path):
        path = tmp_path / "logs" / "mcp" / "s.log"
        path.parent.mkdir(parents=True)
        path.write_text("old\n" * (settings.MAX_LOG_BYTES // 4 + 10), encoding="utf-8")

        stream, _ = transport.open_server_log(tmp_path, "s")
        stream.close()

        assert "old" not in path.read_text(encoding="utf-8")

    def test_without_a_usable_directory_the_output_is_dropped(self, tmp_path: Path):
        blocker = tmp_path / "logs"
        blocker.write_text("a file where a directory should be")

        stream, path = transport.open_server_log(tmp_path, "s")
        stream.write("dropped")  # does not raise
        stream.close()

        assert path is None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_defaults(self):
        s = settings.Settings.from_env({})

        assert (s.startup_timeout, s.call_timeout) == (60.0, 120.0)

    def test_the_variables_apply(self):
        s = settings.Settings.from_env(
            {"PI_MCP_STARTUP_TIMEOUT": "5", "PI_MCP_CALL_TIMEOUT": "2.5"}
        )

        assert (s.startup_timeout, s.call_timeout) == (5.0, 2.5)

    @pytest.mark.parametrize("bad", ["abc", "0", "-3", "nan", "inf", "", "  "])
    def test_a_bad_value_is_ignored(self, bad: str, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="pi_mcp.settings"):
            s = settings.Settings.from_env({"PI_MCP_CALL_TIMEOUT": bad})

        assert s.call_timeout == 120.0
        if bad.strip():
            assert "PI_MCP_CALL_TIMEOUT" in caplog.text
