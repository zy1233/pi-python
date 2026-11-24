"""The process a sandboxed script runs in (workflow & codemode).

The host starts this file as ``python -I -S -B -X utf8 child.py <options>`` with a scrubbed
environment, an empty working directory and a pipe on stdin and stdout. It uses only the
standard library: it must run without any workspace package, site-packages or installed deps.

See ``docs/specs/2026-10-01-workflow-sandbox-design.md`` and
``docs/specs/2026-11-24-code-mode-design.md`` for the threat model and protocol v2.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import binascii
import builtins
import cmath
import collections
import collections.abc
import contextlib
import copy
import csv
import dataclasses
import datetime
import decimal
import difflib
import enum
import fnmatch
import fractions
import functools
import hashlib
import hmac
import inspect
import io
import itertools
import json
import math
import operator
import os
import pathlib
import random
import re
import statistics
import string
import sys
import textwrap
import threading
import time
import types
import typing
import unicodedata
import urllib.parse

PROTOCOL = 2

# The namespace the script sees. It steers a script towards orchestration; it is not a boundary.
_SCRIPT_BUILTINS = [
    "len",
    "range",
    "enumerate",
    "zip",
    "map",
    "filter",
    "list",
    "dict",
    "set",
    "tuple",
    "str",
    "int",
    "float",
    "bool",
    "isinstance",
    "sorted",
    "reversed",
    "min",
    "max",
    "sum",
    "any",
    "all",
    "abs",
    "round",
    "repr",
    "type",
    "hasattr",
    "getattr",
    "setattr",
    "Exception",
    "RuntimeError",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
]

_CODEMODE_EXTRA_BUILTINS = [
    "aiter",
    "anext",
    "ascii",
    "AssertionError",
    "AttributeError",
    "bin",
    "bytearray",
    "bytes",
    "callable",
    "chr",
    "classmethod",
    "complex",
    "divmod",
    "format",
    "frozenset",
    "hash",
    "hex",
    "id",
    "ImportError",
    "issubclass",
    "iter",
    "LookupError",
    "next",
    "object",
    "oct",
    "ord",
    "OverflowError",
    "PermissionError",
    "pow",
    "property",
    "slice",
    "staticmethod",
    "StopAsyncIteration",
    "StopIteration",
    "ZeroDivisionError",
]

_CODEMODE_FORBIDDEN_TOOLS = frozenset({"codemode", "workflow", "tool_search"})
_CODEMODE_OUTPUT_CALLS = frozenset({"print", "text", "image"})


class ToolError(RuntimeError):
    """Error raised inside a codemode script when a tool call fails."""

    def __init__(self, message: str, tool_name: str = "") -> None:
        super().__init__(message)
        self.tool_name = tool_name


class ToolPermissionError(ToolError):
    """Error raised inside a codemode script when permission for a tool call is denied."""


class ToolLimitError(ToolError):
    """Error raised inside a codemode script when the tool-call budget is exceeded."""


class ToolText(str):
    """String return value of ``tools.*`` / ``call_tool(...)`` carrying structured metadata."""

    details: dict
    images: tuple[dict, ...]

    def __new__(
        cls,
        text: str,
        *,
        details: dict | None = None,
        images: tuple[dict, ...] = (),
    ) -> ToolText:
        obj = super().__new__(cls, text)
        obj.details = dict(details) if isinstance(details, dict) else {}
        obj.images = tuple(images)
        return obj

    @property
    def text(self) -> str:
        return str(self)


# Errors the host reports for a call come back as these, so a script can catch them.
_ERRORS = {
    cls.__name__: cls
    for cls in (
        Exception,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        PermissionError,
        ToolError,
        ToolPermissionError,
        ToolLimitError,
    )
}

# Audit-hook policy, applied once the script is about to run. Event families (the part of the
# event name before the first dot) that are denied outright, and modules that may not be
# imported for the first time. See ``_audit``.
_DENIED_FAMILIES = frozenset(
    [
        "os",
        "shutil",
        "socket",
        "subprocess",
        "ctypes",
        "glob",
        "tempfile",
        "urllib",
        "http",
        "ftplib",
        "smtplib",
        "telnetlib",
        "webbrowser",
        "winreg",
        "msvcrt",
        "fcntl",
        "resource",
        "syslog",
        "sqlite3",
        "mmap",
        "pty",
        "ensurepip",
        "zipimport",
        "_winapi",
        "posix",
        "nt",
        "gc",
    ]
)
_DENIED_IMPORTS = frozenset(
    [
        "ctypes",
        "_ctypes",
        "cffi",
        "_cffi_backend",
        "multiprocessing",
        "_multiprocessing",
        "pty",
        "webbrowser",
        "winreg",
        "mmap",
    ]
)

# Longest phase title; the host cuts at the same length (a title is shown, and is a journal key).
_MAX_PHASE_TITLE = 500

_in_fd = -1
_out_fd = -1
_max_message = 4 * 1024 * 1024
_send_lock = threading.Lock()
_pending: dict[int, asyncio.Future] = {}
_next_id = 0
_budget: _Budget | None = None
_run_message: asyncio.Future


# --- safe module proxies for codemode ------------------------------------------------------


def _make_restricted_io() -> types.ModuleType:
    mod = types.ModuleType("io")
    mod.StringIO = io.StringIO
    mod.BytesIO = io.BytesIO
    mod.__all__ = ["StringIO", "BytesIO"]
    return mod


def _make_restricted_pathlib() -> types.ModuleType:
    mod = types.ModuleType("pathlib")
    mod.PurePath = pathlib.PurePath
    mod.PurePosixPath = pathlib.PurePosixPath
    mod.PureWindowsPath = pathlib.PureWindowsPath
    mod.__all__ = ["PurePath", "PurePosixPath", "PureWindowsPath"]
    return mod


def _make_restricted_asyncio() -> types.ModuleType:
    mod = types.ModuleType("asyncio")
    allowed = (
        "ALL_COMPLETED",
        "BoundedSemaphore",
        "CancelledError",
        "Condition",
        "Event",
        "FIRST_COMPLETED",
        "FIRST_EXCEPTION",
        "LifoQueue",
        "Lock",
        "PriorityQueue",
        "Queue",
        "QueueEmpty",
        "QueueFull",
        "Semaphore",
        "TimeoutError",
        "as_completed",
        "create_task",
        "ensure_future",
        "gather",
        "shield",
        "sleep",
        "timeout",
        "timeout_at",
        "wait",
        "wait_for",
    )
    for attr in allowed:
        if hasattr(asyncio, attr):
            setattr(mod, attr, getattr(asyncio, attr))
    mod.__all__ = [attr for attr in allowed if hasattr(mod, attr)]
    return mod


def _make_restricted_time() -> types.ModuleType:
    mod = types.ModuleType("time")
    allowed = (
        "asctime",
        "ctime",
        "gmtime",
        "localtime",
        "mktime",
        "monotonic",
        "monotonic_ns",
        "perf_counter",
        "perf_counter_ns",
        "strftime",
        "strptime",
        "time",
        "time_ns",
        "timezone",
        "tzname",
    )
    for attr in allowed:
        if hasattr(time, attr):
            setattr(mod, attr, getattr(time, attr))
    mod.__all__ = [attr for attr in allowed if hasattr(mod, attr)]
    return mod


def _make_restricted_urllib() -> types.ModuleType:
    mod = types.ModuleType("urllib")
    mod.parse = urllib.parse
    mod.__all__ = ["parse"]
    return mod


_RESTRICTED_ASYNCIO = _make_restricted_asyncio()

_SAFE_MODULES: dict[str, types.ModuleType] = {
    "ast": ast,
    "asyncio": _RESTRICTED_ASYNCIO,
    "base64": base64,
    "binascii": binascii,
    "cmath": cmath,
    "collections": collections,
    "collections.abc": collections.abc,
    "copy": copy,
    "csv": csv,
    "dataclasses": dataclasses,
    "datetime": datetime,
    "decimal": decimal,
    "difflib": difflib,
    "enum": enum,
    "fnmatch": fnmatch,
    "fractions": fractions,
    "functools": functools,
    "hashlib": hashlib,
    "hmac": hmac,
    "io": _make_restricted_io(),
    "itertools": itertools,
    "json": json,
    "math": math,
    "operator": operator,
    "pathlib": _make_restricted_pathlib(),
    "random": random,
    "re": re,
    "statistics": statistics,
    "string": string,
    "textwrap": textwrap,
    "time": _make_restricted_time(),
    "types": types,
    "typing": typing,
    "unicodedata": unicodedata,
    "urllib": _make_restricted_urllib(),
    "urllib.parse": urllib.parse,
}


def _safe_import(
    name: str,
    globals: dict | None = None,
    locals: dict | None = None,
    fromlist: tuple | list = (),
    level: int = 0,
):
    if level != 0 or not isinstance(name, str) or not name:
        raise ImportError(
            "Relative imports are disabled in codemode sandbox; use await tools.* instead."
        )
    mod = _SAFE_MODULES.get(name)
    if mod is None:
        raise ImportError(
            f"Module {name!r} is disabled in codemode sandbox; use await tools.* instead."
        )
    if fromlist:
        return mod
    top_name = name.partition(".")[0]
    return _SAFE_MODULES.get(top_name, mod)


# --- the wire ------------------------------------------------------------------------------


def _send(message: dict) -> None:
    """Write one message to the host. Raises ``ValueError`` if it would be too large."""
    data = json.dumps(message, ensure_ascii=True, separators=(",", ":"), default=str).encode()
    data += b"\n"
    if len(data) > _max_message:
        raise ValueError(f"message too large ({len(data)} bytes; the limit is {_max_message})")
    with _send_lock:
        view = memoryview(data)
        while view:
            view = view[os.write(_out_fd, view) :]


def _read_loop(loop: asyncio.AbstractEventLoop, stream) -> None:
    """Hand every line the host sends to the event loop; leave when the host is gone."""
    with contextlib.suppress(BaseException):  # whatever went wrong, the host is no longer one
        while True:
            line = stream.readline(_max_message + 1)
            if not line.endswith(b"\n"):  # end of input, or a line longer than any we expect
                break
            loop.call_soon_threadsafe(_dispatch, json.loads(line))
    os._exit(3)


def _dispatch(message: dict) -> None:
    kind = message.get("t")
    if kind == "reply":
        future = _pending.pop(message.get("id"), None)
        if future is not None and not future.done():
            future.set_result(message)
    elif kind == "run" and not _run_message.done():
        _run_message.set_result(message)


async def _call(function: str, payload: dict):
    """Ask the host to do something and wait for its answer."""
    global _next_id
    _next_id += 1
    call_id = _next_id
    future = asyncio.get_running_loop().create_future()
    _pending[call_id] = future
    try:
        _send({"t": "call", "id": call_id, "fn": function, **payload})
        reply = await future
    except BaseException:
        if _pending.pop(call_id, None) is not None:  # unanswered: the host can stop working on it
            with contextlib.suppress(BaseException):
                _send({"t": "cancel", "id": call_id})
        raise
    if _budget is not None and "spent" in reply:
        _budget.spent_seen = max(_budget.spent_seen, int(reply.get("spent") or 0))
    if reply.get("ok"):
        return reply.get("value")
    err_cls = _ERRORS.get(reply.get("etype"), RuntimeError)
    msg = reply.get("message", "")
    if issubclass(err_cls, ToolError):
        raise err_cls(msg, tool_name=str(reply.get("tool_name") or ""))
    raise err_cls(msg)


# --- workflow mode -------------------------------------------------------------------------


class _Budget:
    """``{ total, spent(), remaining() }`` as the script sees it; the host keeps the books."""

    def __init__(self, total: int | None, spent: int) -> None:
        self.total = total
        self.spent_seen = spent

    def spent(self) -> int:
        return self.spent_seen

    def remaining(self) -> int | None:
        if self.total is None:
            return None
        return max(0, self.total - self.spent_seen)

    def exceeded(self) -> bool:
        return self.total is not None and self.spent_seen >= self.total

    def to_dict(self) -> dict:
        return {"total": self.total, "spent": self.spent(), "remaining": self.remaining()}


async def _settle(starts: list) -> list:
    """Run everything *starts* create at the same time; a failure is that one's result.

    Nothing started here outlives the call. If it is cancelled, every task is cancelled and
    awaited before the cancel goes on; the same when one of *starts* cannot be started.
    """
    tasks: list[asyncio.Future] = []
    try:
        for start in starts:
            tasks.append(asyncio.ensure_future(start()))
        return list(await asyncio.gather(*tasks, return_exceptions=True))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _namespace(args, cwd: str, budget: _Budget) -> dict:
    # The phase is part of what a call is. The host answers calls as tasks of its own, so by
    # the time it gets to one the script may be in the next phase: the call says which it was
    # made in, and that is what a resume (the journal) must see too.
    phase_now: str | None = None

    async def agent(prompt, **opts):
        if not isinstance(prompt, str):
            raise TypeError(f"agent() prompt must be a string, not {type(prompt).__name__}")
        if phase_now is not None and not opts.get("phase"):
            opts["phase"] = phase_now
        return await _call("agent", {"prompt": prompt, "opts": opts})

    async def parallel(thunks):
        return await _settle(list(thunks))

    async def pipeline(items, *stages):
        async def run_item(item, index):
            value = item
            for stage in stages:
                value = await stage(value, item, index)
            return value

        return await _settle([functools.partial(run_item, item, i) for i, item in enumerate(items)])

    def phase(title, **opts):
        nonlocal phase_now
        phase_now = str(title)[:_MAX_PHASE_TITLE]
        _send({"t": "phase", "title": phase_now})

    def log(message):
        _send({"t": "log", "msg": str(message)})

    def result(value):
        _send({"t": "result", "value": value})  # fails here, in the script, if it cannot be sent

    allowed = {name: getattr(builtins, name) for name in _SCRIPT_BUILTINS}
    allowed.update({"print": log, "None": None, "True": True, "False": False})
    return {
        "agent": agent,
        "parallel": parallel,
        "pipeline": pipeline,
        "phase": phase,
        "log": log,
        "args": args,
        "cwd": cwd,
        "budget": budget,
        "result": result,
        "meta": {},
        "__builtins__": allowed,
    }


def _extract_meta(namespace: dict):
    raw = namespace.get("meta", {})
    if not isinstance(raw, dict):
        return None
    return {
        "name": raw.get("name", "unnamed"),
        "description": raw.get("description", ""),
        "phases": raw.get("phases", []),
    }


def _script_line(exc: BaseException, filename: str = "<workflow>") -> int | None:
    """The innermost line of the *script* the exception passed through, if any."""
    line = exc.lineno if isinstance(exc, SyntaxError) else None
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == filename:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line if isinstance(line, int) else None


async def _run_workflow(message: dict) -> None:
    global _budget
    budget_info = message.get("budget") or {}
    _budget = _Budget(budget_info.get("total"), int(budget_info.get("spent") or 0))
    namespace = _namespace(message.get("args"), message.get("cwd", "."), _budget)
    try:
        code = compile(message["script"], "<workflow>", "exec")
        exec(code, namespace)
        main = namespace.get("main")
        if main is not None and getattr(getattr(main, "__code__", None), "co_flags", 0) & 0x80:
            await main()
    except BaseException as exc:
        try:
            text = str(exc)
        except BaseException:
            text = "<error message could not be rendered>"
        _send(
            {
                "t": "error",
                "etype": type(exc).__name__,
                "message": text[:20000],
                "line": _script_line(exc, "<workflow>"),
            }
        )
        return
    _send({"t": "done", "meta": _extract_meta(namespace)})


# --- codemode ------------------------------------------------------------------------------


def _strip_markdown_fence(script: str) -> str:
    """Strip outer symmetric markdown fence lines while preserving inner line numbers."""
    lines = script.splitlines(keepends=True)
    if len(lines) < 2:
        return script
    first_idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    last_idx = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
    if first_idx is None or last_idx is None or first_idx >= last_idx:
        return script
    first = lines[first_idx].strip()
    last = lines[last_idx].strip()
    if first.startswith("```") and last == "```":
        lines[first_idx] = "\n" if lines[first_idx].endswith("\n") else ""
        lines[last_idx] = ""
        return "".join(lines)
    return script


def _compile_codemode(script: str):
    cleaned = _strip_markdown_fence(script)
    tree = ast.parse(cleaned, filename="<codemode>", mode="exec")
    body = list(tree.body)
    if body and isinstance(body[-1], ast.Expr):
        expr = body[-1].value
        is_output_call = (
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Name)
            and expr.func.id in _CODEMODE_OUTPUT_CALLS
        )
        if not is_output_call:
            body[-1] = ast.copy_location(ast.Return(value=expr), body[-1])
    if not body:
        body = [ast.Pass(lineno=1, col_offset=0)]
    fn = ast.AsyncFunctionDef(
        name="__codemode_main__",
        args=ast.arguments(
            posonlyargs=[],
            args=[],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        ),
        body=body,
        decorator_list=[],
        returns=None,
        type_comment=None,
        lineno=1,
        col_offset=0,
    )
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    return compile(module, "<codemode>", "exec")


def _freeze_mapping(raw: dict) -> types.MappingProxyType:
    frozen = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            frozen[key] = _freeze_mapping(value)
        elif isinstance(value, list):
            frozen[key] = tuple(
                _freeze_mapping(item) if isinstance(item, dict) else item for item in value
            )
        else:
            frozen[key] = value
    return types.MappingProxyType(frozen)


def _unfreeze_value(value):
    if isinstance(value, types.MappingProxyType):
        return {k: _unfreeze_value(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_unfreeze_value(item) for item in value]
    return value


def _merge_tool_args(args_tuple: tuple, kwargs: dict) -> dict:
    if not args_tuple:
        return dict(kwargs)
    if len(args_tuple) == 1 and isinstance(args_tuple[0], dict):
        merged = dict(args_tuple[0])
        overlap = set(merged) & set(kwargs)
        if overlap:
            raise TypeError(f"Got duplicate argument(s): {', '.join(sorted(overlap))}")
        merged.update(kwargs)
        return merged
    raise TypeError(
        "Tool calls expect keyword arguments (e.g. tools.read(path='...')) or a single dict "
        "(e.g. tools.read({'path': '...'}))."
    )


def _codemode_safe_getattr(obj, name: str, *default):
    if isinstance(name, str) and name.startswith("__"):
        raise AttributeError(f"Access to dunder attribute {name!r} is disabled in codemode")
    return builtins.getattr(obj, name, *default)


def _codemode_namespace(tools_meta: dict) -> dict:
    frozen_all = _freeze_mapping(tools_meta)
    by_call_name: dict[str, str] = {}
    for raw_name, entry in tools_meta.items():
        if isinstance(entry, dict):
            cname = entry.get("call_name")
            if isinstance(cname, str) and cname:
                by_call_name[cname] = raw_name

    def resolve_tool_name(name: str) -> str:
        if not isinstance(name, str) or not name:
            raise KeyError("Tool name must be a non-empty string")
        if name in _CODEMODE_FORBIDDEN_TOOLS:
            raise PermissionError(f"Tool {name!r} cannot be called from inside codemode")
        if name in tools_meta:
            return name
        if name in by_call_name:
            return by_call_name[name]
        candidates = sorted(set(tools_meta) | set(by_call_name))
        matches = difflib.get_close_matches(name, candidates, n=3, cutoff=0.4)
        hint = f" Did you mean: {', '.join(matches)}?" if matches else ""
        raise KeyError(f"Unknown tool {name!r}.{hint}")

    async def invoke(raw_name: str, args_dict: dict) -> ToolText:
        reply = await _call("tool", {"tool": raw_name, "args": args_dict})
        if isinstance(reply, dict):
            text_val = str(reply.get("text") or "")
            details_val = reply.get("details")
            raw_images = reply.get("images") or ()
            images_val = tuple(dict(img) for img in raw_images if isinstance(img, dict))
            return ToolText(
                text_val,
                details=details_val if isinstance(details_val, dict) else {},
                images=images_val,
            )
        return ToolText("" if reply is None else str(reply))

    async def call_tool(name: str, *args, **kwargs) -> ToolText:
        raw_name = resolve_tool_name(name)
        merged = _merge_tool_args(args, kwargs)
        return await invoke(raw_name, merged)

    class _ToolsProxy:
        def __getattr__(self, attr: str):
            if attr.startswith("_"):
                raise AttributeError(attr)
            if attr in _CODEMODE_FORBIDDEN_TOOLS:
                raise PermissionError(f"Tool {attr!r} cannot be called from inside codemode")
            raw_name = by_call_name.get(attr) or (attr if attr in tools_meta else None)
            if raw_name is None:
                matches = difflib.get_close_matches(
                    attr, sorted(set(by_call_name) | set(tools_meta)), n=3, cutoff=0.4
                )
                hint = f" Did you mean: {', '.join(matches)}?" if matches else ""
                raise AttributeError(f"tools has no tool named {attr!r}.{hint}")

            async def _runner(*args, **kwargs) -> ToolText:
                merged = _merge_tool_args(args, kwargs)
                return await invoke(raw_name, merged)

            _runner.__name__ = attr
            return _runner

        def __dir__(self) -> list[str]:
            return sorted(by_call_name)

        def __repr__(self) -> str:
            return f"<CodemodeTools ({len(tools_meta)} tools)>"

    def search_tools(query: str = "", *, limit: int = 20) -> list[dict]:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        lim = max(1, min(int(limit), 100))
        q = query.strip()
        items = []
        pattern = None
        if len(q) >= 2 and q.startswith("/") and q.endswith("/"):
            pattern = re.compile(q[1:-1], re.IGNORECASE)
        tokens = [] if (not q or q == "*" or pattern is not None) else q.lower().split()

        for raw_name, entry in tools_meta.items():
            if not isinstance(entry, dict):
                continue
            call_name = str(entry.get("call_name") or raw_name)
            desc = str(entry.get("description") or "")
            sig = str(entry.get("signature") or "")
            haystack = f"{raw_name} {call_name} {desc} {sig}"
            matched = False
            if not q or q == "*":
                matched = True
            elif pattern is not None:
                matched = bool(pattern.search(haystack))
            else:
                low = haystack.lower()
                matched = all(tok in low for tok in tokens)
            if matched:
                items.append(
                    {
                        "name": raw_name,
                        "call_name": call_name,
                        "signature": sig,
                        "description": desc,
                        "deferred": bool(entry.get("deferred", False)),
                        "read_only": bool(entry.get("read_only", False)),
                    }
                )
            if len(items) >= lim:
                break
        return items

    def describe_tool(name: str) -> dict:
        raw_name = resolve_tool_name(name)
        entry = _unfreeze_value(frozen_all[raw_name])
        entry["name"] = raw_name
        return entry

    async def parallel(*items, concurrency: int = 8, return_exceptions: bool = False) -> list:
        if (
            len(items) == 1
            and isinstance(items[0], (list, tuple, types.GeneratorType))
            and not inspect.isawaitable(items[0])
            and not callable(items[0])
        ):
            seq = list(items[0])
        else:
            seq = list(items)
        sem = asyncio.Semaphore(max(1, int(concurrency)))
        running: list[asyncio.Task] = []

        async def _run_one(item):
            async with sem:
                target = item() if callable(item) else item
                if inspect.isawaitable(target):
                    return await target
                return target

        try:
            for item in seq:
                running.append(asyncio.ensure_future(_run_one(item)))
            if return_exceptions:
                return list(await asyncio.gather(*running, return_exceptions=True))
            return list(await asyncio.gather(*running))
        except BaseException:
            for task in running:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            for item in seq:
                if inspect.iscoroutine(item) and inspect.getcoroutinestate(item) == "CORO_CREATED":
                    item.close()
            raise

    def text(*values, sep: str = " ") -> None:
        _send({"t": "output", "kind": "text", "data": sep.join(str(v) for v in values)})

    def image(item, mime_type: str = "image/png") -> None:
        if isinstance(item, dict):
            data = item.get("data")
            mt = item.get("mimeType") or item.get("mime_type") or mime_type
        elif isinstance(item, str):
            data = item
            mt = mime_type
        else:
            raise TypeError("image() expects a base64 string or {'data': ..., 'mimeType': ...}")
        if not isinstance(data, str) or not data:
            raise ValueError("image() requires non-empty base64 image data")
        _send({"t": "output", "kind": "image", "data": {"data": data, "mimeType": str(mt)}})

    def _cm_print(*values, sep: str = " ", end: str = "\n") -> None:
        msg = sep.join(str(v) for v in values) + end
        _send({"t": "log", "msg": msg.rstrip("\n")})

    allowed = {
        name: getattr(builtins, name) for name in _SCRIPT_BUILTINS + _CODEMODE_EXTRA_BUILTINS
    }
    allowed.update(
        {
            "getattr": _codemode_safe_getattr,
            "print": _cm_print,
            "None": None,
            "True": True,
            "False": False,
            "__import__": _safe_import,
        }
    )
    return {
        "tools": _ToolsProxy(),
        "call_tool": call_tool,
        "ALL_TOOLS": frozen_all,
        "search_tools": search_tools,
        "describe_tool": describe_tool,
        "parallel": parallel,
        "text": text,
        "image": image,
        "ToolError": ToolError,
        "ToolPermissionError": ToolPermissionError,
        "ToolLimitError": ToolLimitError,
        "ToolText": ToolText,
        "json": json,
        "re": re,
        "math": math,
        "asyncio": _RESTRICTED_ASYNCIO,
        "__builtins__": allowed,
    }


async def _run_codemode(message: dict) -> None:
    tools_meta = message.get("tools_meta")
    if not isinstance(tools_meta, dict):
        tools_meta = {}
    namespace = _codemode_namespace(tools_meta)
    try:
        code_obj = _compile_codemode(message["script"])
        exec(code_obj, namespace)
        main_coro = namespace["__codemode_main__"]()
        ret = await main_coro
        if ret is not None:
            _send({"t": "result", "value": ret})
    except BaseException as exc:
        try:
            err_text = str(exc)
        except BaseException:
            err_text = "<error message could not be rendered>"
        _send(
            {
                "t": "error",
                "etype": type(exc).__name__,
                "message": err_text[:20000],
                "line": _script_line(exc, "<codemode>"),
            }
        )
        return
    _send({"t": "done", "meta": None})


async def _run(message: dict) -> None:
    mode = message.get("mode", "workflow")
    if mode == "codemode":
        await _run_codemode(message)
        return
    await _run_workflow(message)


# --- limits and hardening ------------------------------------------------------------------


def _apply_posix_limits(opts: dict) -> list[str]:
    import resource

    layers: list[str] = []

    def cap(name: str, resource_id: int, value: int) -> None:
        try:
            _, hard = resource.getrlimit(resource_id)
            if hard != resource.RLIM_INFINITY:
                value = min(value, hard)
            # Soft and hard alike: a script that gets out must not be able to raise it again.
            resource.setrlimit(resource_id, (value, value))
        except (ValueError, OSError):
            return  # this platform does not have, or does not enforce, the limit
        layers.append(name)

    cap("rlimit-core", resource.RLIMIT_CORE, 0)
    cap("rlimit-as", resource.RLIMIT_AS, opts["memory"])
    cap("rlimit-cpu", resource.RLIMIT_CPU, opts["cpu"])
    cap("rlimit-nofile", resource.RLIMIT_NOFILE, opts["nofile"])
    cap("rlimit-fsize", resource.RLIMIT_FSIZE, 0)
    if sys.platform.startswith("linux"):
        with contextlib.suppress(Exception):
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(38, 1, 0, 0, 0) == 0:  # PR_SET_NO_NEW_PRIVS
                layers.append("no-new-privs")
    return layers


def _apply_late_posix_limits() -> list[str]:
    """Limits that stop this process starting threads: only after the ones it needs exist."""
    if not sys.platform.startswith("linux"):
        return []
    import resource

    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    except (ValueError, OSError):
        return []
    return ["rlimit-nproc"]


def _lower_integrity() -> list[str]:
    """Windows: drop this process to low integrity, as browsers do with their renderers."""
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class SidAndAttributes(ctypes.Structure):  # also what TOKEN_MANDATORY_LABEL holds
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.ConvertStringSidToSidW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        advapi32.SetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]

        token = wintypes.HANDLE()
        # TOKEN_ADJUST_DEFAULT | TOKEN_QUERY
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), 0x0080 | 0x0008, ctypes.byref(token)
        ):
            return []
        try:
            sid = ctypes.c_void_p()
            if not advapi32.ConvertStringSidToSidW("S-1-16-4096", ctypes.byref(sid)):  # low
                return []
            try:
                label = SidAndAttributes(sid, 0x20)  # SE_GROUP_INTEGRITY
                lowered = advapi32.SetTokenInformation(  # TokenIntegrityLevel
                    token, 25, ctypes.byref(label), ctypes.sizeof(label)
                )
            finally:
                kernel32.LocalFree(sid)
        finally:
            kernel32.CloseHandle(token)
    except Exception:
        return []
    return ["low-integrity"] if lowered else []


def _forget_ctypes() -> None:
    """Leave ``ctypes`` unimported, as it was found."""
    for name in [n for n in sys.modules if n in ("ctypes", "_ctypes") or n.startswith("ctypes.")]:
        del sys.modules[name]


def _audit(event: str, args: tuple) -> None:
    """Refuse the events a script has no business causing. Raised errors abort the operation."""
    if event == "open" or event.partition(".")[0] in _DENIED_FAMILIES:
        raise PermissionError(f"workflow scripts may not use {event}")
    if event == "import" and args[0] in _DENIED_IMPORTS:
        raise ImportError(f"workflow scripts may not import {args[0]}")


# --- main ----------------------------------------------------------------------------------


def _options(argv: list[str]) -> dict:
    opts = {"memory": 512 << 20, "cpu": 60, "nofile": 64, "max_message": 4 << 20, "audit": True}
    it = iter(argv)
    for name in it:
        if name == "--no-audit":
            opts["audit"] = False
        elif name in ("--memory", "--cpu", "--nofile", "--max-message"):
            opts[name[2:].replace("-", "_")] = int(next(it))
    return opts


async def _serve(layers: list[str], opts: dict) -> None:
    _send({"t": "ready", "protocol": PROTOCOL})
    message = await _run_message
    if opts["audit"]:
        sys.addaudithook(_audit)
        layers.append("audit-hook")
    _send({"t": "started", "layers": layers})
    await _run(message)


def main(argv: list[str]) -> None:
    global _in_fd, _out_fd, _max_message, _run_message
    opts = _options(argv)
    _max_message = opts["max_message"]
    # Take the pipe for ourselves, then point 0 and 1 at the null device: whatever a script
    # (or a library on its behalf) prints cannot end up in the protocol.
    _in_fd, _out_fd = os.dup(0), os.dup(1)
    null = os.open(os.devnull, os.O_RDWR)
    os.dup2(null, 0)
    os.dup2(null, 1)
    os.close(null)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _run_message = loop.create_future()
    reader = threading.Thread(
        target=_read_loop, args=(loop, os.fdopen(_in_fd, "rb", buffering=65536)), daemon=True
    )
    reader.start()

    layers = ["process"]
    if os.name == "posix":
        layers += _apply_posix_limits(opts)
        layers += _apply_late_posix_limits()
    elif sys.platform == "win32":
        layers += _lower_integrity()
    _forget_ctypes()
    code = 0
    try:
        loop.run_until_complete(_serve(layers, opts))
    except BaseException as exc:
        code = 1
        with contextlib.suppress(BaseException):
            sys.stderr.write(f"workflow sandbox: {type(exc).__name__}: {exc}\n")
            sys.stderr.flush()
    finally:
        os._exit(code)


if __name__ == "__main__":
    main(sys.argv[1:])
