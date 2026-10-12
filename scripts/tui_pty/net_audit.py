"""Does the zypi process talk to the network? A connection audit for plan C.1.

    net_audit.py [--zypi PATH] [--python PATH] (--mock | --model-from FILE) [--no-network]
                 [--interval-ms N] [--turn-timeout S] [--show]

zypi is the screen. The Python agent holds the model settings and is the only program that may
talk to the model provider. This script runs zypi under a PTY, plays one turn, and all the time
lists the sockets of zypi and of every process below it (``/proc/<pid>/fd`` joined with
``/proc/<pid>/net/{tcp,tcp6,udp,udp6}``). It then checks:

* zypi has no connection to a non-loopback address, and listens on no non-loopback address;
* no other process in the tree does either, except the agent in a real-model run;
* in a real-model run, the agent does hold an established connection to a non-loopback address.
  This is the positive control: without it a clean zypi proves nothing, because the sampler (or
  the turn) may have missed the traffic.

``--mock`` uses the agent's mock LLM (no key, no network: the agent must stay quiet too).
``--model-from FILE`` copies only the ``[model]`` table of that ``agent.toml`` into the
throw-away home and sends one short prompt to the real model. The API key is read from the
environment variable that table names (``api_key_env``) and handed to the agent in the
environment; the script never reads ``local.env``. ``--no-network`` (mock only) puts the whole
run in an empty network namespace with ``bwrap --unshare-net``: the turn must still finish.

Nothing the script prints names a host, an address or a key: a remote is shown as ``remote#N``
with its port and the state of the socket. Sampling can miss a connection that opens and closes
between two samples; the mean interval is printed, and it is the limit of what a clean run
proves. The parsing and the verdicts are plain Python and tested on any platform
(``scripts/tests/test_tui_pty.py``); the run itself is Linux only (a pty and ``/proc``).
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shutil
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import zypi_env as env

# ---- /proc/net -------------------------------------------------------------------------

TCP_STATES = {
    "01": "ESTABLISHED",
    "02": "SYN_SENT",
    "03": "SYN_RECV",
    "04": "FIN_WAIT1",
    "05": "FIN_WAIT2",
    "06": "TIME_WAIT",
    "07": "CLOSE",
    "08": "CLOSE_WAIT",
    "09": "LAST_ACK",
    "0A": "LISTEN",
    "0B": "CLOSING",
}
NET_FILES = (("tcp", "tcp"), ("tcp6", "tcp"), ("udp", "udp"), ("udp6", "udp"))


@dataclass(frozen=True)
class Conn:
    proto: str  # "tcp" or "udp"
    local: tuple[str, int]
    remote: tuple[str, int]
    state: str  # TCP: a name from TCP_STATES; UDP: "CONNECTED" or "UNCONNECTED"
    inode: int


def decode_address(text: str) -> tuple[str, int]:
    """``0100007F:0050`` (IPv4) or a 32-digit IPv6 form, as ``/proc/net`` prints it.

    The kernel prints each 32-bit word of the address in host byte order, so a little-endian
    host (x86, arm64) needs the bytes of each word reversed. The port is plain hex.
    """
    host, _, port = text.partition(":")
    if len(host) not in (8, 32):  # 4 or 16 bytes, two hex digits each
        raise ValueError(f"not an address: {text!r}")
    raw = b"".join(int(host[i : i + 8], 16).to_bytes(4, "little") for i in range(0, len(host), 8))
    address: ipaddress.IPv4Address | ipaddress.IPv6Address
    address = ipaddress.IPv4Address(raw) if len(raw) == 4 else ipaddress.IPv6Address(raw)
    return str(address), int(port, 16)


def parse_net_table(text: str, proto: str) -> list[Conn]:
    """The rows of one ``/proc/net/{tcp,tcp6,udp,udp6}`` file (the header line is skipped)."""
    rows = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10:
            continue
        try:
            local, remote = decode_address(fields[1]), decode_address(fields[2])
            inode = int(fields[9])
        except ValueError:
            continue
        if proto == "tcp":
            state = TCP_STATES.get(fields[3].upper(), fields[3])
        else:
            state = "CONNECTED" if fields[3] == "01" else "UNCONNECTED"
        rows.append(Conn(proto, local, remote, state, inode))
    return rows


def _plain(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """The address, with an IPv4-mapped IPv6 address (``::ffff:127.0.0.1``) as plain IPv4."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_loopback(address: str) -> bool:
    """127.0.0.0/8 or ::1: only this machine can reach it."""
    return _plain(address).is_loopback


def is_local(address: str) -> bool:
    """Loopback, or unspecified (0.0.0.0 and ::, what a socket with no peer shows as its peer)."""
    ip = _plain(address)
    return ip.is_loopback or ip.is_unspecified


def is_outbound(conn: Conn) -> bool:
    """A socket that is talking to (or trying to reach) a non-loopback address."""
    if conn.state in ("LISTEN", "UNCONNECTED"):
        return False
    return not is_local(conn.remote[0])


def is_exposed(conn: Conn) -> bool:
    """A socket that accepts traffic on a non-loopback address.

    A socket bound to the wildcard (0.0.0.0, ::) counts: the wildcard includes every interface.
    """
    if conn.proto == "tcp":
        return conn.state == "LISTEN" and not is_loopback(conn.local[0])
    return conn.state == "UNCONNECTED" and not is_loopback(conn.local[0]) and conn.local[1] != 0


def read_net_tables(pid: int) -> list[Conn]:
    """The socket tables of the network namespace of ``pid``."""
    conns: list[Conn] = []
    for name, proto in NET_FILES:
        try:
            conns += parse_net_table(Path(f"/proc/{pid}/net/{name}").read_text(), proto)
        except OSError:
            continue
    return conns


# ---- /proc/<pid> -----------------------------------------------------------------------


def socket_inodes(pid: int) -> set[int]:
    inodes = set()
    try:
        entries = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return inodes
    for entry in entries:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{entry}")
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            inodes.add(int(target[len("socket:[") : -1]))
    return inodes


def process_tree(root: int) -> dict[int, str]:
    """``{pid: cmdline}`` of ``root`` and everything below it, from one pass over ``/proc``."""
    parents: dict[int, int] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as fh:
                parents[int(entry)] = int(fh.read().rsplit(b")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
    tree, todo = {root}, [root]
    while todo:
        parent = todo.pop()
        for pid, ppid in parents.items():
            if ppid == parent and pid not in tree:
                tree.add(pid)
                todo.append(pid)
    out = {}
    for pid in tree:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                out[pid] = fh.read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
    return out


def role_of(cmdline: str, zypi: str) -> str:
    """``zypi``, ``agent`` (the Python agent), ``wrapper`` (bwrap) or ``other`` (a tool)."""
    first = cmdline.split(" ", 1)[0]
    if "pi_agent_cli" in cmdline:
        return "agent"
    if first == zypi or Path(first).name == "zypi":
        return "zypi"
    if Path(first).name == "bwrap":
        return "wrapper"
    return "other"


# ---- what was seen ---------------------------------------------------------------------


@dataclass
class Seen:
    role: str
    proto: str
    remote: str  # "remote#N": a stand-in for the address, which is never printed
    port: int
    kind: str  # "outbound" or "exposed"
    states: set[str] = field(default_factory=set)
    first: float = 0.0  # seconds after the log was made


class Log:
    """What the sampler has seen, by (role, kind, proto, remote). Addresses stay in here only."""

    def __init__(self, started: float | None = None) -> None:
        self.started = time.monotonic() if started is None else started
        self.seen: dict[tuple[str, str, str, str, int], Seen] = {}
        self._names: dict[str, str] = {}
        self.roles: dict[str, set[str]] = {}  # role -> the programs seen under it
        self.ticks = 0
        self.last = self.started

    def name(self, address: str) -> str:
        if address not in self._names:
            self._names[address] = f"remote#{len(self._names) + 1}"
        return self._names[address]

    def record(self, role: str, conn: Conn, now: float) -> None:
        if is_outbound(conn):
            kind, address, port = "outbound", conn.remote[0], conn.remote[1]
        elif is_exposed(conn):
            kind, address, port = "exposed", conn.local[0], conn.local[1]
        else:
            return
        label = self.name(address)
        key = (role, kind, conn.proto, label, port)
        item = self.seen.get(key)
        if item is None:
            item = self.seen[key] = Seen(
                role, conn.proto, label, port, kind, first=now - self.started
            )
        item.states.add(conn.state)

    def sample(self, zypi: str, root: int, now: float) -> None:
        """One look at every socket of every process in the tree."""
        tree = process_tree(root)
        tables: dict[str, dict[int, list[Conn]]] = {}  # network namespace -> inode -> rows
        self.ticks += 1
        self.last = now
        for pid, cmdline in tree.items():
            role = role_of(cmdline, zypi)
            program = Path(cmdline.split(" ", 1)[0]).name  # empty for a process that is exiting
            self.roles.setdefault(role, set())
            if program:
                self.roles[role].add(program)
            try:
                namespace = os.readlink(f"/proc/{pid}/ns/net")
            except OSError:
                namespace = ""
            if namespace not in tables:
                by_inode: dict[int, list[Conn]] = {}
                for conn in read_net_tables(pid):
                    by_inode.setdefault(conn.inode, []).append(conn)
                tables[namespace] = by_inode
            for inode in socket_inodes(pid):
                for conn in tables[namespace].get(inode, []):
                    self.record(role, conn, now)

    def mean_interval_ms(self) -> float:
        return 1000.0 * (self.last - self.started) / self.ticks if self.ticks else 0.0

    def of(self, role: str, kind: str = "outbound") -> list[Seen]:
        return [s for s in self.seen.values() if s.role == role and s.kind == kind]


# ---- verdicts --------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def describe_roles(roles: dict[str, set[str]]) -> str:
    """``agent, other (git, sh), zypi``: the roles seen, and the programs behind the vague ones."""
    parts = []
    for role in sorted(roles):
        programs = ", ".join(sorted(roles[role]))
        parts.append(f"{role} ({programs})" if role in ("other", "wrapper") else role)
    return ", ".join(parts) or "none"


def describe(items: list[Seen]) -> str:
    if not items:
        return "none"
    items = sorted(items, key=lambda s: (s.first, s.remote, s.port))
    return ", ".join(
        f"{s.remote} {s.proto}/{s.port} {'+'.join(sorted(s.states))} first @+{s.first:.1f}s"
        for s in items
    )


def judge(log: Log, *, real: bool) -> list[Check]:
    """The checks of a run. Pure: ``Log`` in, verdicts out."""
    checks = [
        Check(
            "the sampler saw zypi itself",
            "zypi" in log.roles,
            f"{log.ticks} samples, mean interval {log.mean_interval_ms():.1f} ms; "
            f"roles seen: {describe_roles(log.roles)}",
        )
    ]
    zypi_out = log.of("zypi")
    checks.append(
        Check(
            "zypi has no connection to a non-loopback address",
            not zypi_out,
            describe(zypi_out),
        )
    )
    for role in ("wrapper", "other"):
        out = log.of(role)
        checks.append(
            Check(
                f"no `{role}` process has a connection to a non-loopback address",
                not out,
                describe(out),
            )
        )
    agent_out = log.of("agent")
    if real:
        established = [s for s in agent_out if "ESTABLISHED" in s.states]
        checks.append(
            Check(
                "positive control: the agent holds an established connection to a "
                "non-loopback address",
                bool(established),
                describe(agent_out),
            )
        )
    else:
        checks.append(
            Check(
                "the agent has no connection to a non-loopback address (mock LLM)",
                not agent_out,
                describe(agent_out),
            )
        )
    for role in ("zypi", "agent", "wrapper", "other"):
        exposed = log.of(role, "exposed")
        checks.append(
            Check(
                f"`{role}` listens on no non-loopback address",
                not exposed,
                describe(exposed),
            )
        )
    return checks


# ---- the run ---------------------------------------------------------------------------

BWRAP_NO_NETWORK = ["bwrap", "--dev-bind", "/", "/", "--unshare-net", "--die-with-parent"]


ERROR_LINE = re.compile(r"(?m)^\s*Error:")


def real_reply(screen: str, word: str = "pong") -> tuple[bool, str]:
    """Did the real model answer? ``screen`` after the prompt "Reply with the single word pong.".

    The footer "Worked for" also shows when the turn failed (a wrong key gives a 401), so look
    for the reply itself: no ``Error:`` line, and the word on screen twice (the prompt and the
    reply). The error text is not quoted: it can name the host.
    """
    errored = bool(ERROR_LINE.search(screen))
    count = screen.lower().count(word)
    detail = (
        f"the word is on screen {count}x (2 = prompt + reply); "
        f"an error line is {'shown' if errored else 'not shown'}"
    )
    return (not errored and count >= 2), detail


def model_table(path: Path) -> dict[str, object]:
    """The ``[model]`` table of an ``agent.toml``: all that a run takes from the user's file."""
    with open(path, "rb") as fh:
        table = tomllib.load(fh).get("model")
    if not isinstance(table, dict) or not table:
        raise SystemExit(f"{path} has no [model] table")
    return table


def toml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


SECRET_KEY = re.compile(r"key|token|secret|password", re.IGNORECASE)


def agent_toml(model: dict[str, object]) -> str:
    """A minimal ``agent.toml`` that carries the model and nothing else of the user's.

    A key that holds a secret itself stays behind. The name of the variable that holds one
    (``api_key_env``) does not: the run needs it, and it is not a secret.
    """
    lines = ["[model]"]
    for key, value in model.items():
        if not isinstance(value, str | int | float | bool):
            continue
        if SECRET_KEY.search(key) and not key.endswith("_env"):
            continue
        lines.append(f"{key} = {toml_scalar(value)}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zypi", help="the zypi binary (default: $ZYPI, then tui/target/*/zypi)")
    ap.add_argument(
        "--python", help="interpreter with pi_agent_cli (default: $PI_PYTHON, then this)"
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="the agent's mock LLM: no network")
    mode.add_argument(
        "--model-from",
        metavar="FILE",
        type=Path,
        help="copy [model] of this agent.toml and play a real turn",
    )
    ap.add_argument("--no-network", action="store_true", help="--mock only: bwrap --unshare-net")
    ap.add_argument("--interval-ms", type=float, default=5.0, help="time between samples")
    ap.add_argument("--turn-timeout", type=float, default=90.0, help="seconds to wait for a reply")
    ap.add_argument("--show", action="store_true", help="print the screen at the end of each step")
    args = ap.parse_args()
    if args.no_network and not args.mock:
        ap.error("--no-network goes with --mock: a real turn needs the network")
    if not sys.platform.startswith("linux"):
        raise SystemExit("net_audit.py needs Linux (a pty and /proc)")
    if args.no_network and not shutil.which("bwrap"):
        raise SystemExit("--no-network needs bwrap")

    import pty_term as pt  # imported late: it needs pyte and a Linux pty

    zypi = env.find_zypi(args.zypi)
    python = env.agent_python(args.python)
    real = not args.mock
    root = env.scratch("pi-netaudit-")
    extra = {"PI_PYTHON": python}
    if real:
        model = model_table(args.model_from)
        key_name = str(model.get("api_key_env") or "")
        if not key_name or not os.environ.get(key_name):
            raise SystemExit("the variable that [model].api_key_env names is not set here")
        (root / "home" / "agent.toml").write_text(agent_toml(model), encoding="utf-8")
        extra[key_name] = os.environ[key_name]
        prompt, done = "Reply with the single word pong.", r"Worked for"
        print(f"real model: provider={model.get('provider')} (no host, key or file text shown)")
    else:
        (root / "home" / "agent.toml").write_text(
            '[model]\nprovider = "mock"\nid = "mock-a"\n', encoding="utf-8"
        )
        extra["PI_USE_MOCK"] = "1"
        prompt, done = "hello", r"Hello from mock"
    run_env = env.base_env(root, **extra)
    argv = (list(BWRAP_NO_NETWORK) if args.no_network else []) + [zypi]
    marker = env.marker(root)

    log = Log()
    results: list[Check] = []
    interval = max(args.interval_ms, 1.0) / 1000.0
    term = pt.Term(argv, run_env, cwd=str(root / "work"))

    def watch(seconds: float, until: str | None = None) -> bool:
        """Pump the terminal and sample the sockets every ``interval`` until ``until`` shows."""
        regex = re.compile(until) if until else None
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            term.pump(interval)
            log.sample(zypi, term.proc.pid, time.monotonic())
            if regex and regex.search(term.text()):
                return True
            if term.eof and term.proc.poll() is not None:
                break
        return bool(regex and regex.search(term.text()))

    def show(title: str) -> None:
        if args.show:
            print(f"----- {title}\n{term.text()}\n-----")

    try:
        results.append(Check("the welcome page renders", watch(25, r"Thanks for trying zypi")))
        show("welcome")
        watch(2.0)  # idle: start-up work that does not answer to the user
        term.type(prompt + "\r")
        answered = watch(args.turn_timeout, done)
        detail = f"waited up to {args.turn_timeout:.0f}s"
        if real:
            replied, more = real_reply(term.text())
            answered, detail = answered and replied, f"{detail}; {more}"
        results.append(Check("the turn is answered", answered, detail))
        show("after the turn")
        watch(1.5)  # the connection a client keeps open after the answer
        term.type("/exit")
        term.pump(0.5)
        term.send("\r")
        end = time.monotonic() + 20
        while time.monotonic() < end and term.proc.poll() is None:
            term.pump(interval)
            log.sample(zypi, term.proc.pid, time.monotonic())
        code = term.proc.poll()
        results.append(Check("/exit ends zypi with status 0", code == 0, f"status={code}"))
        left = pt.processes_with_env(marker)
        results.append(Check("nothing is left running", not left, str(left)))
    finally:
        term.kill_group()
        pt.kill_marked(marker)
        env.remove(root)

    results += judge(log, real=real)
    for check in results:
        detail = f"  -- {check.detail}" if check.detail else ""
        print(f"[{'PASS' if check.ok else 'FAIL'}] {check.name}{detail}")
    if real:
        remotes = {s.remote for s in log.of("agent")}
        print(f"(the agent talked to {len(remotes)} distinct non-loopback address(es))")
    failed = [c for c in results if not c.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    t0 = time.monotonic()
    rc = main()
    print(f"({time.monotonic() - t0:.0f}s)")
    sys.exit(rc)
