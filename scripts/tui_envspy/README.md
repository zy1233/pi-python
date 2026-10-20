# Test-isolation checks for the TUI tests (Linux)

All tests of one Rust test binary run as threads of one process. They share the environment
variables, the working directory and every `static`. A test that changes one of these can make
another test fail, and the failure shows in some runs only.

The tools in this directory find such places.

| Tool | What it finds |
|------|---------------|
| `envspy.sh` with `envspy.c`, `runner.sh` and `analyze.py` | The tests that change an environment variable (`std::env::set_var` and `remove_var`) while another test runs, the tests that leave a variable changed, the tests that read a variable that another test has changed, and the places that change the working directory. It lists the tests and does not judge them |
| `stress.sh` | The tests that fail when the order of the tests or the number of test threads changes. This finds every kind of shared state (environment, `static`, file, port), but it names only the test that failed, not the test that caused the failure. Run a suspect pair (two filters, two threads) to find the cause |

The two tools work together. `stress.sh` shows that a problem exists. `envspy.sh` shows which
tests write an environment variable. Section 3.4 of `docs/baselines/tui.md` has the numbers of the
last run (in Chinese, as the rest of that file).

## Run the spy

Linux only (it uses `LD_PRELOAD`, `tgkill`, `backtrace` and `/proc`). It needs `gcc`, `nm` and
`python3`. Run it in `tui/`, where cargo runs:

```bash
export CARGO_TARGET_DIR=...                      # only if you set it for cargo (see below)
../scripts/tui_envspy/envspy.sh selftest         # the spy sees what it must see; prints SELFTEST OK
../scripts/tui_envspy/envspy.sh run all cargo test --locked -p pi-shell -p pi-pager
../scripts/tui_envspy/envspy.sh report all > /tmp/envspy-all.txt
../scripts/tui_envspy/envspy.sh report all --key GROK_HOME --detail    # one variable, by call stack
```

`run <tag> <command...>` sets the spy as the cargo test runner (`CARGO_TARGET_<TRIPLE>_RUNNER`),
so every test binary that cargo starts writes one log in `$ENVSPY_WORK/logs/<tag>/`.
`$ENVSPY_WORK` is `~/.cache/pi-envspy` by default. `report` finds the test binaries (it needs
them for the call stacks) in `$CARGO_TARGET_DIR/debug/deps`, or in `target/debug/deps` when the
variable is not set. `ENVSPY_DEPS` sets the directory directly.

To run the tests as `scripts/tui_baseline.py test` runs them (the same environment), put the
spy in front of that command instead:

```bash
../scripts/tui_envspy/envspy.sh run baseline python ../scripts/tui_baseline.py test
```

Do not start `zypi` or a test binary with a `PI_HOME` that points at your real home. The test
runs of `tui_baseline.py` clear `PI_HOME` and the related variables; a plain `cargo test` does not.

### Read the report

`report <tag>` runs `analyze.py` on every log of the tag. It prints these parts for each test
binary:

| Part | Meaning |
|------|---------|
| `[changes]` | For each variable: the number of changes, how many were *overlaps* (the variable was changed by another thread that was still running: two tests change it at the same time), how many were *leaks* (changed by a thread that had ended: a test did not put it back), and how many threads changed it |
| `[overlaps]`, `[leaks]` | The tests that made the changes, grouped, with the test of the earlier change under `prev` |
| `[final]` | Variables that are still changed when the process ends |
| `[reads]` | Reads of a changed variable by a thread that did not change it. `GET-FOREIGN`: the owner is still running (two tests at the same time). `GET-LEAK`: the owner has ended. `GET-GLOBAL`: the main thread set it for the whole process (this is safe) |
| `[cached]` | Reads inside a `OnceLock` or `get_or_init`, while the variable is changed. The value then stays after the change is undone |
| `[chdir]` | The places that change the working directory (also process-wide) |

`--writers-out FILE` and `--readers-out FILE` append one tab-separated line per (variable, test)
to a file, for a join with other data. Delete the file before a new run.

### Chaos mode

A race that needs two tests to meet within some microseconds does not show in one run. In chaos
mode the spy makes the window wide. After a test has changed a variable, the first read of that
variable by the same test sleeps for N ms. The variable stays changed for N ms while the other
tests go on. A test that reads it without a lock sees the other test's value on every run.

```bash
../scripts/tui_envspy/envspy.sh chaos shell1 50 4 cargo test --locked -p pi-shell --lib
ENVSPY_CHAOS_KEYS=GROK_HOME,GROK_TEST_VERSION \
  ../scripts/tui_envspy/envspy.sh chaos home 100 4 cargo test --locked -p pi-pager --lib
```

The command runs 4 times with 50 ms. It prints the tests that failed and the number of runs in
which each failed. A test that fails in chaos mode and passes without it reads a variable that
another test sets. The default is all variables (`ENVSPY_CHAOS_KEYS=ALL`).

Chaos mode finds nothing when no two tests overlap. It makes a window wider only between tests
that already run at the same time. On this tree it found no failure (4 runs with 50 ms for
`pi-shell` and for `pi-pager`, before the fixes), and the stress test found the failures: with the
fixed order and 4 threads of CI, the tests that conflict do not run at the same time. Use chaos
mode after the spy report shows an overlap, to see whether the overlap breaks a test. Use the
stress test to find the failures.

## Run the stress test

```bash
# in tui/
../scripts/tui_envspy/stress.sh pi-pager 32 32 1 1     # 32 runs, 32 threads, random order, first seed 1
../scripts/tui_envspy/stress.sh pi-shell 20 4 1 101    # 20 runs, 4 threads (the CI runner), seeds 101...
```

The script builds the lib test binary of the package and runs it many times. It skips the tests
that the manifest skips (`skip` in `scripts/tui_baseline.toml`: today one test of `pi-acp-lib`
that hangs forever, so that the run never ends). With the shuffle flag it gives each run a seed
(`--shuffle-seed`), so you can repeat a failing run:

```bash
cd tui/crates/codegen/pi-pager && RUSTC_BOOTSTRAP=1 <test binary> -Z unstable-options --shuffle-seed 4 --test-threads 32
```

The summary lists each test that failed, with the number of runs in which it failed. Look at the
output files (`$ENVSPY_WORK/stress/<package>-t<threads>-run<N>.out`) for the panic. The next steps:

1. Run the test alone. If it passes, and it fails in the stress run, it shares state with
   another test.
2. Run `envspy.sh` on that package and look for the variable in the report. If the test or the
   code it calls reads an environment variable, look at the tests that change it.
3. If no variable is the cause, look for a `static` (an `AtomicBool`, a `OnceLock`, a global sink)
   that production code writes: grep for the type that the failing assertion reads.

A failure that shows with 32 threads and not with 4 is still a real defect. CI has 4 threads and a
fixed order, so it finds the problem later and less often.

To list the overlaps of these random orders also when no test fails, run the stress test through
the spy. Set `ENVSPY_TAG`; the logs go to `$ENVSPY_WORK/logs/<tag>/` (one per run), and the spy
makes each run several times slower:

```bash
ENVSPY_TAG=shuf ../scripts/tui_envspy/stress.sh pi-shell 5 32 1 1
../scripts/tui_envspy/envspy.sh report shuf
```

### Find the test that causes a failure

The stress summary names the test that failed, not the test that caused the failure. To find the
cause, run the failing test beside one suspect, many times. Words after `--` are filters: a test
runs when its name contains one of them. With two filters and two test threads, both tests start
at once:

```bash
../scripts/tui_envspy/stress.sh pi-pager 200 2 0 1 -- failing_test suspect_test    # order of the names
../scripts/tui_envspy/stress.sh pi-pager 200 2 1 1 -- failing_test suspect_test    # random order
../scripts/tui_envspy/stress.sh pi-pager 200 2 0 1 -- failing_test unrelated_test  # the control
```

The first run line must say `2 passed` or `1 passed; 1 failed`: that shows that both filters
matched one test. A test that fails in every run beside the suspect, and in no run beside the
control, is a victim of the suspect. Use both orders: when the suspect starts first, it can be
finished before the failing test reads the shared state. To find a suspect, look in the code for
the `static` (an `AtomicBool`, a `OnceLock`) that the failing assertion depends on, and grep for
the tests that write it.

A pair can miss a victim whose window is a few microseconds long. Then run the victim beside a
whole group of suspects. A filter is a substring of the full test name, so a module path selects a
module: `-- failing_test scrollback::state::selection::tests::`. Use 32 threads, a random order and
60 runs, and compare with a group of tests that cannot be the cause. On this tree, three `rtl_*`
tests failed in 52 of 60 runs beside the tests of `scrollback::state::selection::tests::` (which
call `set_appearance`), and in 0 of 60 runs alone and beside two other groups. When you have
found the writers, read the rules below: a lock is rarely the answer.

## Rules for a test that changes process-wide state

- Pass a value as a parameter when the code allows it. Do not set a variable.
- When a test must set a variable, restore the old value on every exit (a guard with `Drop`), and
  remove a temporary directory only after the variable is restored.
- **Every test that writes or reads the variable must use the same lock.** Two different locks do
  not keep each other out. A `#[serial]` test and a test that holds its own `Mutex` can run at the
  same time, even when both set the same variable. Give a variable one lock (one `serial_test` key,
  or one `static Mutex` in a shared test module) and use it everywhere.
- A test that only reads a variable and needs a fixed value must also take the lock
  (`#[parallel(KEY)]` against `#[serial(KEY)]` in `serial_test`).
- `pi_env::EnvVarGuard` takes one global lock for the life of the guard and panics when one thread
  holds two guards. Chain the keys with `and_set` and `and_remove`.
- **A lock keeps apart only the tests that take it.** When hundreds of tests read a process-wide
  `static` and take no lock (the theme, the colour level, the minimal-mode flag, the bidi latch),
  and a few tests write it, do not look for every reader. Run each writer in a process of its
  own: `crate::test_util::in_own_process(|| { … })` in `pi-pager`. The body runs in a child
  process (the same test binary, one test), so the change cannot get out. The test fails when the
  child fails, and it prints what the child wrote. `with_theme_test_env`, `with_test_env` (the
  theme) and `with_rtl_bidi` (the bidi latch) do this for the tests that use them.
- The code of the test before and after the call of `in_own_process` runs in both processes. Only
  the body is skipped in the parent. Do not change process-wide state outside the body. The child
  has the environment variables that the parent had when it started the child. It costs about
  30 ms for each test.
- Do not add `pin_theme()` to each victim that you find. It keeps apart only the tests that take
  it, and every reader that does not take it is a new victim. On this tree, each `pin_theme()`
  fixed one test, and the next population run found five more victims in `scrollback::`. When the
  writers ran in their own processes, the victims were gone (section 3.4 of
  `docs/baselines/tui.md`).
- A test that only reads such a `static` needs nothing, as long as no test of the same process
  writes it.

## What these tools do not find

- Windows and macOS. The tools are Linux only; the results do not say that the tests are safe there.
- State that is not in the environment is visible only through `stress.sh`, which names the failing
  test and not the cause.
- The spy keeps three samples for each call site (the exit line has the full count). It sees
  "the owner is running" as "the thread of the owner exists", which is a little longer than its
  test. A test that starts a thread (`spawn_blocking`, for example) counts as two threads.
- The spy cannot see a lock. It lists the tests that write a variable, not the way each test is
  protected. Join `--writers-out` with the attributes and locks in the source to find a variable
  that has writers under two different locks.
- Foreign reads (a test reads a variable that another test has changed) are listed, but most of
  them are harmless. Look at what the reader asserts.

## Why cargo-nextest solves the class

`cargo nextest run` starts each test in its own process. The environment, the `static`s and the
working directory are then private to the test, and the races above cannot happen. The tests of
this tree come from a project that runs them with nextest. Comments in the code say so:
`pi-shell/src/config/tests.rs` ("reliable only under nextest's process-per-test isolation"),
`pi-workspace/src/lib.rs` and `folder_trust.rs` (a lock for "the rare in-process `cargo test`
thread"), `pi-telemetry/src/debug_log.rs` and `pi-pager-render/src/terminal/xtversion.rs`. Only
one crate, `pi-fast-worktree` (`src/db/mod.rs`), says that its suite must also pass under
`cargo test`. The CI of this repository ran `cargo test`, where all tests share one process.
`TUI CI` runs `cargo nextest` now (`runner = "nextest"` in `scripts/tui_baseline.toml`), so a race
of this class cannot fail it. A local `cargo test` still runs all tests of a binary in one process,
and the tools of this directory are for that run.

What the experiment showed (commit `aa76df4`, WSL2): nextest ran the lib tests of `pi-pager` 11
times (32 threads 10 times, 4 threads once) and of `pi-shell` 5 times (32 threads) with no
failure. `cargo test` with a random order and 32 threads failed in 3 of 32 runs of `pi-pager`
after the first fixes of this directory (6 of 32 before them), and in none of 10 runs with the
fixed order of CI and 32 threads. The price, as wall time with 4 threads: the lib tests of
`pi-pager` took 79.5 s under nextest and 29.7 s under `cargo test`. `in_own_process` gives the
same isolation to the 57 tests that need it. With it, `cargo test` failed in 0 of 60 runs (random
order, 32 threads). With 4 threads, the time that libtest prints for the lib tests of `pi-pager`
is 27.2 to 28.0 s in 3 runs (28.8 s at `aa76df4`): the 57 child processes add no time that these
runs can show. In `TUI CI` (PR 28) the same time is 15.55 s and 15.79 s in two runs. Five earlier
runs of `TUI CI`, without the isolation, took 8.21 to 14.83 s (mean 12.1 s). So the CI runners show
about 3.5 s more, or about 60 ms for each isolated test (30 ms on the machine above). Two runs are
a small sample: the earlier runs differ from each other by up to 6.6 s.

What nextest does not change: files and ports that tests share (a test still needs its own
temporary directory), and the threads and child processes that one test starts. nextest does not
run doctests. The suites of `scripts/tui_baseline.toml` name their targets (`--lib`, `--test`,
`--bin`), so they never ran doctests, with `cargo test` either. In GitHub Actions, `tui-ci.yml`
installs a pinned version with `taiki-e/install-action@v2` (`tool: nextest@0.9.148`).

## Disk space

A debug build of the whole `tui/` tree, with its test binaries, needs tens of gigabytes in
`CARGO_TARGET_DIR` (about 30 GB in the runs for this tool). Build on the Linux file system, and
delete the target directory when you are done.

On WSL2 the disk file of the distribution (`ext4.vhdx`) grows when you write data and does not
shrink when you delete it. When the Windows disk that holds the file is full, WSL stops, and the
processes in it die with `Bus error`. To get the space back, delete the data in WSL, run
`wsl --shutdown`, and compact the file from an administrator shell:

```text
diskpart
  select vdisk file="<path of ext4.vhdx>"
  attach vdisk readonly
  compact vdisk
  detach vdisk
```