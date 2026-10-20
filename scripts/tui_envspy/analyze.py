"""Read the logs of envspy.c and report where two tests can see each other's environment changes.

Usage: python3 analyze.py --deps DIR [--top N] [--frames N] [--key NAME] [--detail]
                          [--readers-out FILE] [--writers-out FILE] LOG...

DIR holds the test binaries (for example tui/target/debug/deps). A log is named
<binary name>.<number>.log (runner.sh writes it so), so the binary of a log is found from its name.
Backtraces become function names with `nm -C -n` of that binary.

The report has these parts, per test binary:
  changes   per variable: set/unset events, how many were overlaps (the variable was changed by
            another thread that was still running), leaks (changed by a thread that had ended), and
            how many threads changed it
  overlaps  groups of overlap events: who changed it, and who had changed it before
  leaks     the tests that left a variable changed (changed again later, or still changed at exit)
  final     variables that are still changed when the process ends
  reads     reads of a changed variable by a thread that did not change it, per variable and per
            test that made the change; FOREIGN = the owner is still running (tests at the same
            time), LEAK = the owner has ended, GLOBAL = the main thread set it for the whole process
  cached    reads inside a OnceLock / get_or_init / lazy initializer while the variable is changed
            (the value then outlives the change)
  chdir     the places that change the working directory

Limits: a log keeps 3 samples per call site (the exit line has the full count); "owner running"
means the thread of the owner exists, which is a little longer than its test; a test that spawns a
thread (spawn_blocking, for example) is seen as two threads.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

EVENT = re.compile(
    r"^(\d+) (START|SET|UNSET|GET-FOREIGN|GET-OWN|GET-LEAK|GET-GLOBAL|CHDIR|FCHDIR|SITE|FINAL)"
    r"\b(.*)$"
)
GET_KINDS = ("GET-FOREIGN", "GET-LEAK", "GET-GLOBAL", "GET-OWN")
# Frames of the runtime, the test harness and helper crates: not the test's own code.
STD_PREFIXES = (
    "std::",
    "core::",
    "alloc::",
    "<std::",
    "<core::",
    "<alloc::",
    "test::",
    "__rust",
    "rust_",
    "_start",
    "__libc",
    "start_thread",
    "clone",
    "tokio::",
    "<tokio::",
    "once_cell::",
    "<once_cell::",
    "backtrace",
    "?",
    "libc.so",
    "libgcc_s",
    "libenvspy",
    "ld-linux",
    "libpthread",
    "dirs::",
    "dirs_sys::",
    "<dirs",
    "serial_test::",
    "<serial_test",
)
CACHE_WORDS = (
    "get_or_init",
    "OnceLock",
    "LazyLock",
    "OnceCell",
    "Lazy<",
    "lazy_static",
    "call_once_force",
)
FIELDS = (
    "key",
    "tid",
    "tname",
    "overlap",
    "leak",
    "owner",
    "owner_name",
    "owner_age_ms",
    "site",
    "n",
    "count",
    "path",
    "dirty_before",
    "dirty_after",
    "exists",
    "alive",
    "kind",
)
NO_FRAME = "(no application frame)"


@dataclass
class Event:
    ts: int
    kind: str
    rest: str
    bt: str = ""
    prev_bt: str = ""
    fields: dict[str, str] = field(default_factory=dict)


class Symbols:
    """Function names of one binary, from `nm -C -n` (the logs hold offsets into a PIE binary)."""

    def __init__(self, binary: str) -> None:
        self.base = os.path.basename(binary)
        self.addrs: list[int] = []
        self.names: list[str] = []
        out = subprocess.run(
            ["nm", "-C", "-n", binary], capture_output=True, text=True, errors="replace"
        ).stdout
        for line in out.splitlines():
            parts = line.split(" ", 2)
            is_code = len(parts) == 3 and parts[1] in "tTwW"
            if is_code and re.fullmatch(r"[0-9a-fA-F]{8,16}", parts[0]):
                self.addrs.append(int(parts[0], 16))
                self.names.append(re.sub(r"::h[0-9a-f]{16}$", "", parts[2]))

    def frames(self, bt_line: str) -> list[str]:
        if "BT:" not in bt_line:
            return []
        out = []
        for frame in bt_line.split("BT:", 1)[1].split():
            m = re.fullmatch(r"(.+)\+0x([0-9a-f]+)", frame)
            if m and m.group(1) == self.base:
                i = bisect.bisect_right(self.addrs, int(m.group(2), 16)) - 1
                if i >= 0:
                    out.append(self.names[i])
                    continue
            out.append(frame)
        return out


def app_frames(frames: list[str]) -> list[str]:
    return [f for f in frames if not f.startswith(STD_PREFIXES)]


def clean(name: str) -> str:
    return re.sub(r"(::\{\{closure\}\})+$", "", name)


def short(name: str, n: int = 110) -> str:
    return name if len(name) <= n else name[: n - 3] + "..."


def parse(path: str) -> list[Event]:
    events: list[Event] = []
    cur: Event | None = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            m = EVENT.match(line)
            if m:
                cur = Event(int(m.group(1)), m.group(2), m.group(3).strip())
                for key in FIELDS:
                    mm = re.search(rf"(?<![\w]){key}=(\S+)", cur.rest)
                    if mm:
                        cur.fields[key] = mm.group(1)
                mv = re.search(
                    r"\b(?:val|seen)=(.*?)(?= (?:tid|owner|dirty_before|exists)=)", cur.rest
                )
                if mv:
                    cur.fields["value"] = mv.group(1)
                events.append(cur)
            elif cur is not None and line.startswith("  BT:"):
                cur.bt = line.strip()
            elif cur is not None and line.startswith("  PREV-BT:"):
                cur.prev_bt = line.strip().removeprefix("PREV-")
    return events


def root_and_site(frames: list[str]) -> tuple[str, str]:
    """(outermost application frame = usually the test function, innermost application frame)."""
    app = [clean(f) for f in app_frames(frames)]
    if not app:
        return (NO_FRAME, NO_FRAME)
    return (app[-1], app[0])


def group_key(frames: list[str], depth: int) -> tuple[str, ...]:
    app = [clean(f) for f in app_frames(frames)]
    if not app:
        return (NO_FRAME,)
    inner = tuple(app[:depth])
    root = app[-1]
    return inner if root in inner else (*inner, "... root: " + root)


def _weight(events: list[Event], totals: dict[tuple[str, str], int]) -> int:
    """Reads in a group: per call site the count of its SITE line, else the logged samples."""
    samples: collections.Counter[tuple[str, str]] = collections.Counter()
    for e in events:
        samples[(e.fields.get("site", ""), e.kind)] += 1
    return sum(totals.get(site_kind, n) for site_kind, n in samples.items())


def _print_changes(by_key: dict[str, list[Event]]) -> None:
    print(
        "\n[changes] per variable: events, overlaps (owner running), leaks (owner ended), threads"
    )
    for key, evs in sorted(by_key.items(), key=lambda kv: -len(kv[1])):
        overlaps = sum(1 for e in evs if e.fields.get("overlap") == "1")
        leaks = sum(1 for e in evs if e.fields.get("leak") == "1")
        threads = {e.fields.get("tid") for e in evs}
        print(
            f"  {key:36} events={len(evs):5}  overlaps={overlaps:4}  leaks={leaks:4}"
            f"  threads={len(threads):3}"
        )


def _print_change_groups(
    sym: Symbols, changes: list[Event], top: int, depth: int, detail: bool
) -> None:
    titles = (
        ("overlap", "overlaps"),
        ("leak", "leaks (a changed variable was left behind and then changed again)"),
    )
    for flag, title in titles:
        groups: dict[tuple, list[Event]] = collections.defaultdict(list)
        for e in changes:
            if e.fields.get(flag) != "1":
                continue
            key = e.fields.get("key", "?")
            if detail:
                gk: tuple = (
                    key,
                    e.kind,
                    group_key(sym.frames(e.bt), depth),
                    group_key(sym.frames(e.prev_bt), depth),
                )
            else:
                gk = (
                    key,
                    e.kind,
                    root_and_site(sym.frames(e.bt))[0],
                    root_and_site(sym.frames(e.prev_bt))[0],
                )
            groups[gk].append(e)
        total = sum(len(v) for v in groups.values())
        print(f"\n[{title}] {total} events in {len(groups)} groups")
        for gk, evs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:top]:
            print(f"  x{len(evs)}  {gk[1]} of {gk[0]}")
            for label, part in (("now ", gk[2]), ("prev", gk[3])):
                for frame in (part,) if isinstance(part, str) else part:
                    print(f"        {label}: {short(frame)}")


def _print_finals(sym: Symbols, events: list[Event], only_key: str | None) -> None:
    finals = [
        e for e in events if e.kind == "FINAL" and (not only_key or e.fields.get("key") == only_key)
    ]
    print(f"\n[final] variables still changed at exit: {len(finals)}")
    for e in finals:
        root = root_and_site(sym.frames(e.bt))[0]
        alive = "owner still running" if e.fields.get("alive") == "1" else "owner ended"
        print(f"  {e.fields.get('key')} = {e.fields.get('value', '?')}  ({alive})")
        print(f"        last change by: {short(root)}")


def _print_reads(
    sym: Symbols,
    events: list[Event],
    only_key: str | None,
    top: int,
    depth: int,
    detail: bool,
    readers_out: str | None,
    log: str,
) -> None:
    totals: dict[tuple[str, str], int] = {}
    for e in events:
        if e.kind == "SITE":
            kind = re.search(r"kind=(\S+)", e.rest)
            site = (e.fields.get("site", ""), kind.group(1) if kind else "")
            totals[site] = int(e.fields.get("count", "0"))

    history: dict[str, list[Event]] = collections.defaultdict(list)
    summary: dict[tuple, dict] = {}
    detail_groups: dict[tuple, list[Event]] = collections.defaultdict(list)
    cached: dict[tuple, list[Event]] = collections.defaultdict(list)
    for e in events:
        key = e.fields.get("key", "?")
        if e.kind in ("SET", "UNSET"):
            history[key].append(e)
            continue
        if e.kind not in GET_KINDS or (only_key and key != only_key):
            continue
        frames = sym.frames(e.bt)
        reader_root = root_and_site(frames)[0]
        if any(w in f for f in frames for w in CACHE_WORDS):
            cached[(key, e.kind, group_key(frames, depth))].append(e)
        if e.kind == "GET-OWN":
            continue
        owner_tid = e.fields.get("owner")
        owner_ev = next(
            (h for h in reversed(history[key]) if h.fields.get("tid") == owner_tid), None
        )
        owner_root = root_and_site(sym.frames(owner_ev.bt))[0] if owner_ev else "(unknown)"
        entry = summary.setdefault(
            (key, e.kind, owner_root),
            {"events": [], "readers": collections.Counter(), "values": set(), "gone": 0},
        )
        entry["events"].append(e)
        entry["readers"][reader_root] += 1
        entry["values"].add(e.fields.get("value", "?"))
        if e.fields.get("exists") == "0":
            entry["gone"] += 1
        if detail:
            owner_g = group_key(sym.frames(owner_ev.bt), depth) if owner_ev else ("(unknown)",)
            detail_groups[(key, e.kind, group_key(frames, depth), owner_g)].append(e)

    print(
        f"\n[reads] reads of a changed variable by another thread: {len(summary)}"
        " (variable, kind, owner test) groups"
    )
    ranked = sorted(summary.items(), key=lambda kv: -_weight(kv[1]["events"], totals))
    for (key, kind, owner_root), s in ranked[:top]:
        values = ", ".join(sorted(s["values"])[:2])
        gone = ""
        if s["gone"]:
            gone = f"; {s['gone']} of {len(s['events'])} samples saw a path that does not exist"
        print(
            f"  {kind:11} {key}  ~{_weight(s['events'], totals)} reads, {len(s['readers'])}"
            f" reader tests (saw {values}{gone})"
        )
        print(f"        owner test : {short(owner_root)}")
        for reader, n in s["readers"].most_common(3):
            print(f"        reader test: {short(reader)}  (x{n})")
    if readers_out:
        with open(readers_out, "a", encoding="utf-8") as fh:
            for (key, kind, owner_root), s in summary.items():
                for reader, n in s["readers"].items():
                    fh.write(
                        f"{os.path.basename(log)}\t{key}\t{kind}\t{reader}\t{owner_root}\t{n}\n"
                    )
    if detail:
        print(f"\n[reads, by call site] {len(detail_groups)} groups")
        ranked_detail = sorted(detail_groups.items(), key=lambda kv: -_weight(kv[1], totals))
        for (key, kind, reader_g, owner_g), evs in ranked_detail[:top]:
            print(f"  ~{_weight(evs, totals)} {kind} reads of {key}")
            for frame in reader_g:
                print(f"        reader: {short(frame)}")
            for frame in owner_g:
                print(f"        owner : {short(frame)}")

    print(
        f"\n[cached] reads inside a once/lazy initializer while the variable was changed:"
        f" {len(cached)} groups"
    )
    for (key, kind, reader_g), evs in sorted(cached.items(), key=lambda kv: -len(kv[1]))[:top]:
        print(f"  x{len(evs)} {kind} {key}")
        for frame in reader_g:
            print(f"        {short(frame)}")


def _print_chdir(sym: Symbols, events: list[Event], top: int, depth: int) -> None:
    calls = [e for e in events if e.kind in ("CHDIR", "FCHDIR")]
    places: collections.Counter[tuple[str, ...]] = collections.Counter()
    for e in calls:
        places[group_key(sym.frames(e.bt), depth)] += 1
    print(f"\n[chdir] {len(calls)} calls, {len(places)} places")
    for group, n in places.most_common(top):
        print(f"  x{n}")
        for frame in group:
            print(f"        {short(frame)}")


def _write_writers(sym: Symbols, changes: list[Event], log: str, writers_out: str) -> None:
    """One line per (variable, operation, test that made the change)."""
    counts: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for e in changes:
        root = root_and_site(sym.frames(e.bt))[0]
        counts[(e.fields.get("key", "?"), e.kind, root)] += 1
    with open(writers_out, "a", encoding="utf-8") as fh:
        for (key, kind, root), n in sorted(counts.items()):
            fh.write(f"{os.path.basename(log)}\t{key}\t{kind}\t{root}\t{n}\n")


def report_log(
    sym: Symbols,
    log: str,
    top: int,
    depth: int,
    only_key: str | None,
    detail: bool,
    readers_out: str | None = None,
    writers_out: str | None = None,
) -> None:
    events = parse(log)
    print(f"\n######## {os.path.basename(log)}  ({len(events)} events)")
    changes = [
        e
        for e in events
        if e.kind in ("SET", "UNSET") and (not only_key or e.fields.get("key") == only_key)
    ]
    if not changes:
        print("no variable was changed in this process")
    by_key: dict[str, list[Event]] = collections.defaultdict(list)
    for e in changes:
        by_key[e.fields.get("key", "?")].append(e)
    if writers_out:
        _write_writers(sym, changes, log, writers_out)

    _print_changes(by_key)
    _print_change_groups(sym, changes, top, depth, detail)
    _print_finals(sym, events, only_key)
    _print_reads(sym, events, only_key, top, depth, detail, readers_out, log)
    _print_chdir(sym, events, top, depth)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("logs", nargs="+", metavar="LOG")
    ap.add_argument(
        "--deps",
        default=os.path.join(os.environ.get("CARGO_TARGET_DIR", "target"), "debug", "deps"),
        help="directory of the test binaries (default: $CARGO_TARGET_DIR/debug/deps)",
    )
    ap.add_argument("--top", type=int, default=25, help="groups to show per part")
    ap.add_argument(
        "--frames", type=int, default=3, help="frames to show per group (with --detail)"
    )
    ap.add_argument("--key", help="look at this variable only")
    ap.add_argument("--detail", action="store_true", help="group by call stack, not by test")
    ap.add_argument(
        "--readers-out",
        help="append one line per (log, variable, kind, reader test, owner test, samples)",
    )
    ap.add_argument(
        "--writers-out",
        help="append one line per (log, variable, SET or UNSET, test that made the change, count)",
    )
    args = ap.parse_args()
    cache: dict[str, Symbols] = {}
    for log in args.logs:
        name = os.path.basename(log).rsplit(".", 2)[0]
        binary = os.path.join(args.deps, name)
        if not os.path.exists(binary):
            print(f"skip {log}: no binary {binary}", file=sys.stderr)
            continue
        if binary not in cache:
            cache[binary] = Symbols(binary)
        report_log(
            cache[binary],
            log,
            args.top,
            args.frames,
            args.key,
            args.detail,
            args.readers_out,
            args.writers_out,
        )


if __name__ == "__main__":
    main()
