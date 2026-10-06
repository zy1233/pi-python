"""The process a workflow script runs in (see ``docs/specs/2026-10-01-workflow-sandbox-design.md``).

The host starts this file as ``python -I -S -B -X utf8 child.py <options>`` with a scrubbed
environment, an empty working directory and a pipe on stdin and stdout. It uses only the
standard library: it must run without this package, the site directory or anything installed.

A script computes here; everything with an effect (sub-agents, the token budget, the journal,
the log) is asked of the host over the pipe, one JSON object per line, and the host does it.
The host does not trust this process. This file is written for the day a script breaks out of
its namespace: what it can then reach is bounded by the operating system limits applied below
and by the host, not by the care taken in here. The audit hook is a speed bump, not a wall.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import functools
import json
import os
import sys
import threading

PROTOCOL = 1

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

# Errors the host reports for a call come back as these, so a script can catch them.
_ERRORS = {
    cls.__name__: cls
    for cls in (Exception, RuntimeError, ValueError, TypeError, KeyError, IndexError)
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
    if _budget is not None:
        _budget.spent_seen = max(_budget.spent_seen, int(reply.get("spent") or 0))
    if reply.get("ok"):
        return reply.get("value")
    raise _ERRORS.get(reply.get("etype"), RuntimeError)(reply.get("message", ""))


# --- what a script is given ----------------------------------------------------------------


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


def _script_line(exc: BaseException) -> int | None:
    """The innermost line of the *script* the exception passed through, if any."""
    line = exc.lineno if isinstance(exc, SyntaxError) else None
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<workflow>":
            line = tb.tb_lineno
        tb = tb.tb_next
    return line if isinstance(line, int) else None


async def _run(message: dict) -> None:
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
                "line": _script_line(exc),
            }
        )
        return
    _send({"t": "done", "meta": _extract_meta(namespace)})


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
    """Windows: drop this process to low integrity, as browsers do with their renderers.

    From then on the system refuses it, whatever the user's own permissions say, writes to
    anything at the user's usual level (their files, the registry), reading the memory of
    the processes that run at that level (the host among them) and duplicating their handles.
    It cannot raise the level again. Reading files and the network stay open to it.
    """
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
    """Leave ``ctypes`` unimported, as it was found. Setting up the limits needed it; a script
    that gets hold of the real import must not be handed it ready-made, and the audit hook
    sees (and refuses) an import only when the module has to be loaded."""
    for name in [n for n in sys.modules if n in ("ctypes", "_ctypes") or n.startswith("ctypes.")]:
        del sys.modules[name]


def _audit(event: str, args: tuple) -> None:
    """Refuse the events a script has no business causing. Raised errors abort the operation.

    This stops a script that wanders out of its namespace by the obvious routes
    (``__import__("os").system``, ``open``, a socket). It does not stop one written against
    it: the hook is Python, and Python code can be changed from Python. What actually
    confines this process is the operating system's: see the module docstring.
    """
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
