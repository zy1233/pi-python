"""A workflow script runs in a process of its own, with little to reach and little to use up.

Scripts used to be ``exec``'d in the host's own interpreter in a "restricted namespace" that
the audit showed two ways out of; once out, a script had the host's environment (API keys),
its files, its network and its event loop. These tests stand in for a script that does get
out of the namespace (``PRELUDE`` in ``sandbox_support``) and check what it finds: another
process, a scrubbed environment, an audit hook that refuses the obvious routes and, beneath
that hook, limits the operating system holds the process to whatever the script does.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from pi_dynamic_workflows.sandbox import (
    SandboxCrashed,
    SandboxLimitExceeded,
    SandboxLimits,
)
from sandbox_support import SPIN, run

WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")
ROOT_USER = hasattr(os, "geteuid") and os.geteuid() == 0

# What the audit hook refuses, spelled out here rather than read from the code under test.
REFUSED_EVENT_FAMILIES = [
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
REFUSED_IMPORTS = [
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


class TestTheScriptRunsElsewhere:
    async def test_it_runs_in_another_process(self) -> None:
        out = await run('result(_imp("os").getpid())')
        assert isinstance(out.result, int)
        assert out.result != os.getpid()

    async def test_it_sees_none_of_the_hosts_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PI_SANDBOX_TEST_SECRET", "hunter2")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-scripts")

        out = await run('result(dict(_imp("os").environ))')

        assert "hunter2" not in repr(out.result)
        assert "sk-not-for-scripts" not in repr(out.result)
        # What is left is what the interpreter itself needs (Windows) or sets (the C locale).
        assert set(out.result) <= {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "LC_CTYPE"}

    async def test_it_works_in_an_empty_scratch_directory_that_is_removed_afterwards(
        self, tmp_path: Path
    ) -> None:
        out = await run('result([_imp("os").getcwd(), cwd])')

        scratch, given_cwd = out.result
        assert os.path.basename(scratch).startswith("pi-workflow-")
        assert not os.path.exists(scratch)  # gone with the run
        assert given_cwd == "."  # the project directory is a string it was told, not where it is

    async def test_what_it_prints_cannot_reach_the_protocol(self) -> None:
        """Stdout is the protocol's pipe; fd 1 in the script process is the null device."""
        out = await run(
            r"""
            _imp("os").write(1, b'{"t":"error","etype":"X","message":"forged"}\n')
            _imp("sys").stdout.write('{"t": "error", "etype": "X", "message": "forged"}\n')
            result("fine")
            """
        )
        assert out.result == "fine"

    async def test_the_interpreter_starts_isolated(self) -> None:
        """No PYTHON* variables or user site (-I), nothing installed (-S), no bytecode written
        (-B), UTF-8 whatever the locale (-X utf8)."""
        out = await run(
            """
            flags = _imp("sys").flags
            result([flags.isolated, flags.no_site, flags.dont_write_bytecode, flags.utf8_mode])
            """
        )

        assert out.result == [1, 1, 1, 1]

    async def test_stdin_is_the_null_device_so_the_hosts_messages_cannot_be_read_off_it(
        self,
    ) -> None:
        out = await run('result(_imp("os").read(0, 100))')

        assert out.result == "b''"  # end of input at once, not a wait for the host

    async def test_the_layers_in_force_are_reported(self) -> None:
        out = await run("result(1)")

        assert {"process", "audit-hook"} <= set(out.layers)
        if WINDOWS:
            assert {"job-object", "low-integrity"} <= set(out.layers)
        else:
            assert {"rlimit-as", "rlimit-cpu", "rlimit-nofile", "rlimit-core"} <= set(out.layers)
        if LINUX:
            assert {"no-new-privs", "rlimit-nproc"} <= set(out.layers)


class TestTheAuditHookRefusesTheObviousRoutes:
    """Each of these is something a script that got out of its namespace would try first."""

    async def test_reading_a_file(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET", encoding="utf-8")

        out = await run(
            'result(attempt(lambda: _imp("builtins").open(args["path"]).read()))',
            args={"path": str(secret)},
        )

        assert out.result.startswith("PermissionError")
        assert "TOP-SECRET" not in out.result

    async def test_writing_a_file(self, tmp_path: Path) -> None:
        target = tmp_path / "new.txt"

        out = await run(
            'result(attempt(lambda: _imp("builtins").open(args["path"], "w").write("x")))',
            args={"path": str(target)},
        )

        assert out.result.startswith("PermissionError")
        assert not target.exists()

    async def test_deleting_a_file(self, tmp_path: Path) -> None:
        victim = tmp_path / "keep.txt"
        victim.write_text("keep", encoding="utf-8")

        out = await run(
            'result(attempt(lambda: _imp("os").remove(args["path"])))', args={"path": str(victim)}
        )

        assert out.result.startswith("PermissionError")
        assert victim.exists()

    async def test_making_a_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "newdir"

        out = await run(
            'result(attempt(lambda: _imp("os").mkdir(args["path"])))', args={"path": str(target)}
        )

        assert out.result.startswith("PermissionError")
        assert not target.exists()

    async def test_listing_a_directory(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")

        out = await run(
            'result(attempt(lambda: _imp("os").listdir(args["path"])))',
            args={"path": str(tmp_path)},
        )

        assert out.result.startswith("PermissionError")

    async def test_running_a_shell_command(self, tmp_path: Path) -> None:
        marker = tmp_path / "ran.txt"

        out = await run(
            'result(attempt(lambda: _imp("os").system(args["cmd"])))',
            args={"cmd": f'echo x> "{marker}"'},
        )

        assert out.result.startswith("PermissionError")
        assert not marker.exists()

    async def test_starting_a_program(self, tmp_path: Path) -> None:
        marker = tmp_path / "ran.txt"
        code = "import sys; open(sys.argv[1], 'w').close()"

        out = await run(
            'result(attempt(lambda: _imp("subprocess").run(args["argv"])))',
            args={"argv": [sys.executable, "-c", code, str(marker)]},
        )

        assert out.result.startswith("PermissionError")
        assert not marker.exists()

    async def test_connecting_to_a_server(self) -> None:
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            server.setblocking(False)

            # Not ``create_connection``: that one trips over the ``open`` of a codec on its way
            # and would be refused for that, whether or not the socket itself is.
            out = await run(
                'result(attempt(lambda: _imp("socket").socket().connect(("127.0.0.1", args))))',
                args=server.getsockname()[1],
            )

            assert out.result.startswith("PermissionError: workflow scripts may not use socket.")
            with pytest.raises(BlockingIOError):  # nobody knocked
                server.accept()

    async def test_loading_native_code(self) -> None:
        """The process set its own limits with ctypes, then forgot it: the import must be
        loaded again, and that is what the hook sees."""
        out = await run('result(attempt(lambda: _imp("ctypes")))')

        assert out.result == "ImportError: workflow scripts may not import ctypes"

    async def test_the_second_known_way_out_leads_to_the_same_wall(self, tmp_path: Path) -> None:
        """``().__class__.__base__.__subclasses__()`` reaches ``warnings``, hence ``sys``."""
        marker = tmp_path / "ran.txt"

        out = await run(
            """
            def find():
                for c in ().__class__.__base__.__subclasses__():
                    if c.__name__ == "catch_warnings":
                        return c.__init__.__globals__
            os = find()["sys"].modules["os"]
            result(attempt(lambda: os.system(args)))
            """,
            args=f'echo x> "{marker}"',
        )

        assert out.result.startswith("PermissionError")
        assert not marker.exists()

    @pytest.mark.parametrize("family", REFUSED_EVENT_FAMILIES)
    async def test_every_event_of_a_refused_family_is_refused(self, family: str) -> None:
        """The policy itself, event by event: some of these have a second wall behind them
        (``shutil`` ends up opening files, ``subprocess`` creating a process) and would go
        unnoticed if they dropped off the list."""
        out = await run(f'result(attempt(lambda: _imp("sys").audit("{family}.probe")))')

        assert out.result == f"PermissionError: workflow scripts may not use {family}.probe"

    async def test_an_event_of_another_family_goes_through(self) -> None:
        out = await run('result(attempt(lambda: _imp("sys").audit("probe.allowed")))')

        assert out.result == "ok: None"

    @pytest.mark.parametrize("module", REFUSED_IMPORTS)
    async def test_the_modules_that_may_not_be_loaded_are_refused(self, module: str) -> None:
        out = await run(f'result(attempt(lambda: _imp("sys").audit("import", "{module}")))')

        assert out.result == f"ImportError: workflow scripts may not import {module}"

    async def test_importing_another_module_goes_through(self) -> None:
        out = await run('result(attempt(lambda: _imp("sys").audit("import", "json")))')

        assert out.result == "ok: None"


class TestTheKernelHoldsWithoutTheHook:
    """The hook is Python, so a script written against it can undo it. These run with it
    off: what is left is what the operating system enforces on the process."""

    @pytest.mark.skipif(ROOT_USER, reason="RLIMIT_NPROC does not bind root")
    async def test_it_cannot_start_another_process(self, tmp_path: Path) -> None:
        marker = tmp_path / "ran.txt"
        out = await run(
            """
            sp = _imp("subprocess")
            code = "import sys; open(sys.argv[1], 'w').close()"
            def go():
                p = sp.Popen([_imp("sys").executable, "-c", code, args])
                p.wait()
                return p.returncode
            result(attempt(go))
            """,
            args=str(marker),
            audit_hook=False,
        )

        assert not out.result.startswith("ok")  # no process, or one that never got to run
        assert not marker.exists()

    @pytest.mark.skipif(not WINDOWS, reason="integrity levels (Windows)")
    async def test_it_cannot_write_where_the_user_can(self, tmp_path: Path) -> None:
        planted = tmp_path / "planted.txt"

        out = await run(
            'result(attempt(lambda: _imp("builtins").open(args, "w").write("x")))',
            args=str(planted),
            audit_hook=False,
        )

        assert out.result.startswith("PermissionError")
        assert not planted.exists()

    @pytest.mark.skipif(not WINDOWS, reason="integrity levels (Windows)")
    async def test_it_cannot_read_the_hosts_memory_or_take_its_handles(self) -> None:
        """Same user, same machine: without a lower integrity level both would be allowed."""
        out = await run(
            """
            ct = _imp("ctypes")
            kernel32 = ct.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = ct.c_void_p

            def can_open(access):
                return bool(kernel32.OpenProcess(access, False, _imp("os").getppid()))

            VM_READ, DUP_HANDLE, QUERY_LIMITED = 0x10, 0x40, 0x1000
            result([can_open(VM_READ), can_open(DUP_HANDLE), can_open(QUERY_LIMITED)])
            """,
            audit_hook=False,
        )

        # Looking at the host is not refused (that is how a process list works); reading it is.
        assert out.result == [False, False, True]

    @pytest.mark.skipif(not LINUX or ROOT_USER, reason="RLIMIT_NPROC (Linux, not root)")
    async def test_it_cannot_fork(self) -> None:
        out = await run('result(attempt(lambda: _imp("os").fork()))', audit_hook=False)

        assert out.result.startswith("BlockingIOError")

    @pytest.mark.skipif(not LINUX, reason="resource limits as Linux sets them")
    async def test_the_limits_are_hard_ones_the_script_cannot_raise_again(self) -> None:
        out = await run(
            """
            r = _imp("resource")
            names = ("CORE", "AS", "CPU", "NOFILE", "FSIZE", "NPROC")
            result({name: r.getrlimit(getattr(r, "RLIMIT_" + name)) for name in names})
            """,
            limits=SandboxLimits(memory_bytes=256 << 20, cpu_seconds=30, open_files=40),
            audit_hook=False,
        )

        assert out.result == {
            "CORE": [0, 0],
            "AS": [256 << 20, 256 << 20],
            "CPU": [30, 30],
            "NOFILE": [40, 40],
            "FSIZE": [0, 0],
            "NPROC": [0, 0],
        }

    @pytest.mark.skipif(not LINUX, reason="prctl (Linux)")
    async def test_no_new_privileges_is_set(self) -> None:
        out = await run(
            """
            libc = _imp("ctypes").CDLL(None)
            result(libc.prctl(39, 0, 0, 0, 0))  # PR_GET_NO_NEW_PRIVS
            """,
            audit_hook=False,
        )

        assert out.result == 1

    async def test_it_cannot_use_more_memory_than_it_is_given(self) -> None:
        out = await run(
            'result(attempt(lambda: len("a" * (1 << 30))))',
            limits=SandboxLimits(memory_bytes=256 * 1024 * 1024),  # room to start; far below 1 GiB
            audit_hook=False,
        )

        assert out.result.startswith("MemoryError")

    @pytest.mark.skipif(WINDOWS, reason="file descriptor limit (POSIX)")
    async def test_it_cannot_open_more_files_than_it_is_given(self) -> None:
        out = await run(
            """
            def many():
                for i in range(500):
                    _imp("os").pipe()
            result(attempt(many))
            """,
            audit_hook=False,
        )

        assert out.result.startswith("OSError")
        assert "Too many open files" in out.result

    async def test_a_script_that_never_stops_computing_is_stopped_at_the_cpu_limit(self) -> None:
        started = time.monotonic()

        with pytest.raises(SandboxCrashed, match="ended unexpectedly"):
            await run(SPIN, limits=SandboxLimits(cpu_seconds=1, wall_seconds=60))

        assert time.monotonic() - started < 40  # Windows checks job CPU time only now and then

    async def test_a_script_that_never_stops_is_stopped_at_the_time_limit(self) -> None:
        started = time.monotonic()

        with pytest.raises(SandboxLimitExceeded, match="time limit of 1 s"):
            await run(SPIN, limits=SandboxLimits(wall_seconds=1.0))

        assert time.monotonic() - started < 15

    async def test_a_script_that_spins_does_not_stall_the_host(self) -> None:
        """In-process, a ``while True`` in a script froze the host's event loop for good."""
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        task = asyncio.ensure_future(ticker())
        try:
            with pytest.raises(SandboxLimitExceeded):
                await run(SPIN, limits=SandboxLimits(wall_seconds=1.0))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert ticks >= 20  # ~50 expected over the second the script spun


@pytest.mark.skipif(not WINDOWS, reason="Windows job objects")
class TestTheWindowsJob:
    """The job on its own, with a process that does nothing but wait: the host's pipe going
    away also ends a script process, so only these show that the job does its part."""

    @staticmethod
    def _sleeper() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [getattr(sys, "_base_executable", sys.executable), "-c", "import time; time.sleep(120)"]
        )

    def test_closing_the_job_kills_what_is_in_it(self) -> None:
        from pi_dynamic_workflows.sandbox.winjob import JobObject

        proc = self._sleeper()
        try:
            job = JobObject(memory_bytes=256 << 20, cpu_seconds=60)
            job.assign(proc.pid)

            job.close()

            assert proc.wait(timeout=15) is not None
        finally:
            proc.kill()
            proc.wait()

    def test_terminating_the_job_kills_what_is_in_it(self) -> None:
        from pi_dynamic_workflows.sandbox.winjob import JobObject

        proc = self._sleeper()
        job = JobObject(memory_bytes=256 << 20, cpu_seconds=60)
        try:
            job.assign(proc.pid)

            job.terminate()

            assert proc.wait(timeout=15) == 1
        finally:
            job.close()
            proc.kill()
            proc.wait()

    def test_a_process_in_the_job_cannot_start_another(self) -> None:
        from pi_dynamic_workflows.sandbox.winjob import JobObject

        proc = subprocess.Popen(
            [
                getattr(sys, "_base_executable", sys.executable),
                "-c",
                "import subprocess, sys, time; time.sleep(1)\n"
                "try:\n"
                "    subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
                "except OSError as e:\n"
                "    sys.exit(41)\n",
            ]
        )
        job = JobObject(memory_bytes=256 << 20, cpu_seconds=60)
        try:
            job.assign(proc.pid)

            assert proc.wait(timeout=30) == 41
        finally:
            job.close()
            proc.kill()
            proc.wait()


@pytest.mark.skipif(not WINDOWS, reason="integrity levels (Windows)")
class TestTheIntegrityLevel:
    def test_it_is_reported_only_when_the_system_took_it(self) -> None:
        """Lowering the level can be refused; what the run reports is what is really in force.

        Done in a throwaway interpreter: it is the process's own token that gets lowered.
        """
        program = textwrap.dedent(
            """
            import ctypes
            from pi_dynamic_workflows.sandbox import child

            refuse = lambda *args: 0

            class Dll:
                def __init__(self, real):
                    self.real = real

                def __getattr__(self, name):
                    return refuse if name == "SetTokenInformation" else getattr(self.real, name)

            real = ctypes.WinDLL
            ctypes.WinDLL = lambda name, **kwargs: Dll(real(name, **kwargs))
            print(child._lower_integrity())
            ctypes.WinDLL = real
            print(child._lower_integrity())
            """
        )

        done = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, timeout=60
        )

        assert done.stdout.split("\n")[:2] == ["[]", "['low-integrity']"], done.stderr
