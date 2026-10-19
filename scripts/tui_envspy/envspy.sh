#!/usr/bin/env bash
# Entry point of the env spy. See README.md in this directory.
#
#   envspy.sh build                              compile libenvspy.so
#   envspy.sh run <tag> <command...>             run a command (cargo test ...) with the spy as the
#                                                cargo test runner; logs go to $ENVSPY_WORK/logs/<tag>/
#   envspy.sh chaos <tag> <ms> <runs> <command...>
#                                                the same, <runs> times, in chaos mode (the owner of a
#                                                changed variable sleeps <ms> ms in its first own read);
#                                                lists the tests that failed
#   envspy.sh report <tag> [analyzer options]    read the logs of <tag> (analyze.py --help)
#   envspy.sh selftest                           check the spy with a small C program
#
# Run it in the directory where cargo runs (tui/). $ENVSPY_WORK (default ~/.cache/pi-envspy) holds
# libenvspy.so and the logs. Linux only.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="${ENVSPY_WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/pi-envspy}"
so="$work/libenvspy.so"

die() {
  echo "envspy.sh: $*" >&2
  exit 2
}

build() {
  mkdir -p "$work"
  gcc -shared -fPIC -O1 -Wall -Wextra -o "$so" "$here/envspy.c" -ldl -pthread
}

# The name of the environment variable with which cargo takes a runner for the host target.
runner_var() {
  local triple
  triple="$(rustc -vV | sed -n 's/^host: //p')"
  [ -n "$triple" ] || die "rustc -vV gave no host triple"
  echo "CARGO_TARGET_$(echo "$triple" | tr 'a-z-' 'A-Z_')_RUNNER"
}

# run_one <tag> <command...>
run_one() {
  local tag="$1"
  shift
  build
  mkdir -p "$work/logs/$tag"
  env "$(runner_var)=$here/runner.sh" ENVSPY_DIR="$work/logs/$tag" ENVSPY_SO="$so" "$@"
}

# cmd_chaos <tag> <ms> <runs> <command...>
cmd_chaos() {
  local tag="$1" ms="$2" runs="$3"
  shift 3
  export ENVSPY_CHAOS_MS="$ms"
  export ENVSPY_CHAOS_KEYS="${ENVSPY_CHAOS_KEYS:-ALL}"
  echo "chaos: $ms ms on $ENVSPY_CHAOS_KEYS; runs: $runs; command: $*"
  mkdir -p "$work/logs"
  local fails="$work/logs/$tag.fails"
  : >"$fails"
  local i out n summary
  for i in $(seq 1 "$runs"); do
    out="$work/logs/$tag-run$i.out"
    run_one "$tag/r$i" "$@" >"$out" 2>&1 || true
    n="$(grep -cE '^test .* FAILED$' "$out" || true)"
    summary="$(grep -E '^test result:' "$out" | tr '\n' ' ' | cut -c1-160 || true)"
    echo "run $i: failed=$n  $summary"
    grep -E '^test .* FAILED$' "$out" | sed 's/^test //; s/ \.\.\. FAILED$//' >>"$fails" || true
  done
  echo "--- tests that failed (number of runs in which each failed):"
  if [ -s "$fails" ]; then sort "$fails" | uniq -c | sort -rn; else echo "(none)"; fi
  echo "output of each run: $work/logs/$tag-run<N>.out"
}

# cmd_report <tag> [analyzer options]
cmd_report() {
  local tag="$1"
  shift
  local deps="${ENVSPY_DEPS:-${CARGO_TARGET_DIR:-$PWD/target}/debug/deps}"
  [ -d "$deps" ] || die "no directory $deps (the test binaries); set ENVSPY_DEPS"
  local logs=()
  mapfile -t logs < <(find "$work/logs/$tag" -name '*.log' | sort)
  [ "${#logs[@]}" -gt 0 ] || die "no logs in $work/logs/$tag"
  python3 "$here/analyze.py" --deps "$deps" "$@" "${logs[@]}"
}

cmd="${1:-}"
[ -n "$cmd" ] || {
  # No command: print the comment block at the top of this file.
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
  exit 2
}
shift
case "$cmd" in
  build)
    build
    echo "built $so"
    ;;
  run)
    [ "$#" -ge 2 ] || die "usage: envspy.sh run <tag> <command...>"
    run_one "$@"
    ;;
  chaos)
    [ "$#" -ge 4 ] || die "usage: envspy.sh chaos <tag> <ms> <runs> <command...>"
    cmd_chaos "$@"
    ;;
  report)
    [ "$#" -ge 1 ] || die "usage: envspy.sh report <tag> [analyzer options]"
    cmd_report "$@"
    ;;
  selftest)
    ENVSPY_WORK="$work" bash "$here/selftest.sh"
    ;;
  *)
    die "unknown command: $cmd"
    ;;
esac
