#!/usr/bin/env bash
# Usage: stress.sh <package> <runs> <threads> <shuffle 0|1> [first_seed]
#
# Run the lib tests of <package> (a package of the tui workspace) <runs> times with <threads> test
# threads. With <shuffle> = 1 every run takes a random order of the tests (libtest --shuffle-seed N,
# with N = first_seed + run number - 1, so a failing run can be repeated; <first_seed> is 1 by
# default). libtest accepts --shuffle-seed only with RUSTC_BOOTSTRAP=1 on a stable toolchain; the
# script sets it for the test binary, not for the build.
#
# Run it in tui/. It builds the test binary with cargo (nothing to do when it is current) and then
# starts the binary itself, which is faster than `cargo test` for many runs. The environment is the
# one tui_baseline.py gives the tests (`unset_env` and `[test.env]` of scripts/tui_baseline.toml).
# The output of run N is $ENVSPY_WORK/stress/<package>-t<threads>-run<N>.out.
#
# The summary lists each test that failed, with the number of runs in which it failed. A test that
# fails in some runs only, and passes with --test-threads 1, shares process-wide state with another
# test (an environment variable, a static, a file). Keep the seed of the failing run to repeat it.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="${ENVSPY_WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/pi-envspy}"

[ "$#" -ge 4 ] || {
  echo "usage: stress.sh <package> <runs> <threads> <shuffle 0|1> [first_seed]" >&2
  exit 2
}
package="$1"
runs="$2"
threads="$3"
shuffle="$4"
first_seed="${5:-1}"

out_dir="$work/stress"
mkdir -p "$out_dir"

# The lib test binary of the package, and the directory of the package.
mapfile -t found < <(cargo test --locked -p "$package" --lib --no-run --message-format=json 2>/dev/null \
  | python3 -c '
import json, sys
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    target = msg.get("target", {})
    if (
        msg.get("reason") == "compiler-artifact"
        and msg.get("profile", {}).get("test")
        and msg.get("executable")
        and "lib" in target.get("kind", [])
    ):
        print(msg["executable"])
        print(target["src_path"].rsplit("/src/", 1)[0])
')
[ "${#found[@]}" -ge 2 ] || {
  echo "stress.sh: no lib test binary for $package" >&2
  exit 3
}
bin="${found[-2]}"
crate_dir="${found[-1]}"

# The environment of the tests (scripts/tui_baseline.toml).
mapfile -t scrub < <(python3 - "$here/../tui_baseline.toml" <<'EOF'
import sys
import tomllib

with open(sys.argv[1], "rb") as fh:
    manifest = tomllib.load(fh)["test"]
for name in manifest.get("unset_env", []):
    print("-u")
    print(name)
for key, value in manifest.get("env", {}).items():
    print(f"{key}={value}")
EOF
)

# The names in the last "failures:" list of a libtest output. The "test x ... FAILED" line is not
# reliable: a test that prints to the terminal can break it.
failed_names() {
  awk '
    /^failures:$/ { count = 0; next }
    /^    [A-Za-z0-9_:]+$/ { names[++count] = $1; next }
    /^test result:/ { for (i = 1; i <= count; i++) print names[i]; exit }
  ' "$1"
}

echo "binary: $bin"
echo "package directory: $crate_dir"
echo "runs: $runs  threads: $threads  shuffle: $shuffle  first seed: $first_seed"
declare -A fails=()
bad_runs=0
cd "$crate_dir"
for i in $(seq 1 "$runs"); do
  seed=$((first_seed + i - 1))
  args=(--test-threads "$threads")
  if [ "$shuffle" = 1 ]; then
    args=(-Z unstable-options --shuffle-seed "$seed" --test-threads "$threads")
  fi
  out="$out_dir/$package-t$threads-run$i.out"
  code=0
  CARGO_MANIFEST_DIR="$crate_dir" CARGO_PKG_NAME="$package" RUSTC_BOOTSTRAP=1 \
    env "${scrub[@]}" "$bin" "${args[@]}" >"$out" 2>&1 || code=$?
  result="$(grep -E '^test result:' "$out" | tail -1 | cut -c1-120 || true)"
  mapfile -t names < <(failed_names "$out")
  echo "run $i seed=$seed exit=$code failed=${#names[@]}  $result"
  if [ "$code" != 0 ] || [ "${#names[@]}" != 0 ]; then
    bad_runs=$((bad_runs + 1))
    if [ "${#names[@]}" = 0 ]; then
      names=("(no failures list; exit $code, see $out)")
    fi
    for name in "${names[@]}"; do
      fails["$name"]=$((${fails["$name"]:-0} + 1))
    done
  fi
done
echo "--- runs with a failure: $bad_runs of $runs"
for name in "${!fails[@]}"; do
  echo "${fails[$name]} $name"
done | sort -rn
