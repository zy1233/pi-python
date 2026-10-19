#!/usr/bin/env bash
# Cargo runner for the env spy. Cargo calls it as: runner.sh <test binary> <test args...>
# (envspy.sh sets CARGO_TARGET_<TRIPLE>_RUNNER to this file; do not call it by hand).
#
# It starts the test binary with LD_PRELOAD=$ENVSPY_SO and writes one log per test binary and run
# in $ENVSPY_DIR. The exec keeps the pid: envspy.c logs only in the process whose pid is $ENVSPY_PID,
# so the children of a test (which inherit LD_PRELOAD) stay quiet.
bin="$1"
shift
name="$(basename "$bin")"
dir="${ENVSPY_DIR:?ENVSPY_DIR is not set}"
mkdir -p "$dir"
export ENVSPY_PID=$$
export ENVSPY_LOG="$dir/${name}.$(date +%s%N).log"
export LD_PRELOAD="${ENVSPY_SO:?ENVSPY_SO is not set}${LD_PRELOAD:+:$LD_PRELOAD}"
exec "$bin" "$@"
