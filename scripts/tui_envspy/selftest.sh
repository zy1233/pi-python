#!/usr/bin/env bash
# Check that envspy.c sees what it must see, with two small C programs that play "tests" (threads).
# Run it with `envspy.sh selftest`. It prints "SELFTEST OK" when every check holds.
#
# Program 1 (events). The timeline (ms):
#   0   main sets SPYTEST_G and SPYTEST_PATH (a path that is not there), then starts A and B.
#   0   A sets X.                    10  A reads X          -> GET-OWN
#   40  B reads X five times (one call site)               -> GET-FOREIGN (A is running)
#   40  B reads SPYTEST_G, SPYTEST_PATH                     -> GET-GLOBAL (the main thread set them)
#   60  B sets X                     -> SET with overlap=1 (A is running and had changed X)
#   80  B ends without putting X back
#  120  A reads X                                           -> GET-LEAK (B has ended)
#  160  A unsets X                   -> UNSET with leak=1 (B had changed X and has ended)
#  end  main reads X (not changed any more: no event), reads an untracked variable (no event),
#       sets a SECRET-like variable (the value must not be in the log), calls chdir.
#  exit FINAL lines for what is still changed: SPYTEST_G, SPYTEST_PATH, SPYTEST_API_KEY.
#
# Program 2 (chaos mode). A sets C, reads it once (its own read) and unsets it 5 ms later; B reads C
# at 60 ms. Without chaos B sees nothing. With chaos A sleeps in its own read, so B sees A's value.
set -u
d="$(cd "$(dirname "$0")" && pwd)"
work="${ENVSPY_WORK:-${XDG_CACHE_HOME:-$HOME/.cache}/pi-envspy}/selftest"
rm -rf "$work"
mkdir -p "$work"
gcc -shared -fPIC -O1 -Wall -Wextra -o "$work/libenvspy.so" "$d/envspy.c" -ldl -pthread \
  || { echo "gcc failed (spy)"; exit 2; }

cat > "$work/t.c" <<'EOF'
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static void ms(int n) { usleep(n * 1000); }

__attribute__((noinline)) static void own_reader_site(void) { (void)getenv("SPYTEST_X"); }
__attribute__((noinline)) static void foreign_reader_site(void) { (void)getenv("SPYTEST_X"); }
__attribute__((noinline)) static void leak_reader_site(void) { (void)getenv("SPYTEST_X"); }
__attribute__((noinline)) static void global_reader_site(void) {
    (void)getenv("SPYTEST_G");
    (void)getenv("SPYTEST_PATH");
}
__attribute__((noinline)) static void a_sets(void) { setenv("SPYTEST_X", "from-a", 1); }
__attribute__((noinline)) static void b_sets(void) { setenv("SPYTEST_X", "from-b", 1); }
__attribute__((noinline)) static void a_unsets(void) { unsetenv("SPYTEST_X"); }

static void *test_a(void *arg) {
    (void)arg;
    pthread_setname_np(pthread_self(), "test-a");
    a_sets();
    ms(10);
    own_reader_site();
    ms(110);
    leak_reader_site();
    ms(40);
    a_unsets();
    return NULL;
}

static void *test_b(void *arg) {
    (void)arg;
    pthread_setname_np(pthread_self(), "test-b");
    ms(40);
    for (int i = 0; i < 5; i++) foreign_reader_site();
    global_reader_site();
    ms(20);
    b_sets();
    ms(20);
    return NULL;
}

int main(void) {
    setenv("SPYTEST_G", "global", 1);
    setenv("SPYTEST_PATH", "/definitely/not/here", 1);
    pthread_t a, b;
    pthread_create(&a, NULL, test_a, NULL);
    pthread_create(&b, NULL, test_b, NULL);
    pthread_join(a, NULL);
    pthread_join(b, NULL);
    (void)getenv("SPYTEST_X");
    (void)getenv("SPYTEST_NEVER_CHANGED");
    setenv("SPYTEST_API_KEY", "sk-must-not-be-logged", 1);
    if (chdir("/tmp") != 0) return 1;
    return 0;
}
EOF
gcc -g -O0 -fno-inline -pthread -o "$work/t" "$work/t.c" || { echo "gcc failed (test 1)"; exit 2; }

export ENVSPY_DIR="$work/logs" ENVSPY_SO="$work/libenvspy.so"
bash "$d/runner.sh" "$work/t" || { echo "test program failed"; exit 3; }
log="$(ls "$work"/logs/t.*.log | head -1)"
echo "--- log: $(wc -l < "$log") lines"
cut -c1-190 "$log" | grep -v '^  BT:'
echo "--- checks"
fail=0
chk() { # chk <name> <expected> <actual>
  if [ "$2" = "$3" ]; then echo "ok   $1 = $3"; else echo "FAIL $1: expected $2, got $3"; fail=1; fi
}
chk "START lines" 1 "$(grep -c ' START ' "$log")"
chk "SET events of X" 2 "$(grep -c ' SET key=SPYTEST_X ' "$log")"
chk "UNSET events of X" 1 "$(grep -c ' UNSET key=SPYTEST_X ' "$log")"
chk "events with overlap=1 (B's SET)" 1 "$(grep -c 'overlap=1 leak=0' "$log")"
chk "events with leak=1 (A's UNSET)" 1 "$(grep -c 'overlap=0 leak=1' "$log")"
chk "GET-FOREIGN samples (5 calls, one site, 3 samples)" 3 "$(grep -c ' GET-FOREIGN key=SPYTEST_X ' "$log")"
chk "GET-OWN samples" 1 "$(grep -c ' GET-OWN key=SPYTEST_X ' "$log")"
chk "GET-LEAK samples" 1 "$(grep -c ' GET-LEAK key=SPYTEST_X ' "$log")"
chk "GET-GLOBAL key=SPYTEST_G" 1 "$(grep -c ' GET-GLOBAL key=SPYTEST_G ' "$log")"
chk "GET-GLOBAL path that does not exist (exists=0)" 1 "$(grep -c ' GET-GLOBAL key=SPYTEST_PATH .* exists=0 ' "$log")"
chk "SITE line of the foreign site (count 5)" 1 "$(grep -c ' SITE kind=GET-FOREIGN key=SPYTEST_X .* count=5' "$log")"
chk "reads of the variable that never changed" 0 "$(grep -c 'SPYTEST_NEVER_CHANGED' "$log")"
chk "secret value in the log" 0 "$(grep -c 'sk-must-not-be-logged' "$log")"
chk "redacted marker present (SET and FINAL)" 2 "$(grep -c 'val=<redacted len=21>' "$log")"
chk "CHDIR events" 1 "$(grep -c ' CHDIR path=/tmp ' "$log")"
chk "FINAL lines (G, PATH, API_KEY)" 3 "$(grep -c ' FINAL key=' "$log")"

echo "--- chaos mode"
cat > "$work/t2.c" <<'EOF'
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static void ms(int n) { usleep(n * 1000); }

static void *test_a(void *arg) {
    (void)arg;
    pthread_setname_np(pthread_self(), "test-a");
    setenv("SPYTEST_C", "from-a", 1);
    ms(5);
    (void)getenv("SPYTEST_C");
    ms(5);
    unsetenv("SPYTEST_C");
    return NULL;
}

static void *test_b(void *arg) {
    (void)arg;
    pthread_setname_np(pthread_self(), "test-b");
    ms(60);
    const char *v = getenv("SPYTEST_C");
    printf("B saw: %s\n", v ? v : "(unset)");
    return NULL;
}

int main(void) {
    pthread_t a, b;
    pthread_create(&a, NULL, test_a, NULL);
    pthread_create(&b, NULL, test_b, NULL);
    pthread_join(a, NULL);
    pthread_join(b, NULL);
    return 0;
}
EOF
gcc -g -O0 -fno-inline -pthread -o "$work/t2" "$work/t2.c" || { echo "gcc failed (test 2)"; exit 2; }
chaos_run() { # chaos_run <tag> <ms> <keys>  -> prints what B saw
  local tag="$1" msv="$2" keys="$3"
  ENVSPY_DIR="$work/logs-$tag" ENVSPY_CHAOS_MS="$msv" ENVSPY_CHAOS_KEYS="$keys" \
    bash "$d/runner.sh" "$work/t2" 2>/dev/null | sed -n 's/^B saw: //p'
}
chk "chaos off: B sees" "(unset)" "$(chaos_run off 0 "")"
chk "chaos on, key SPYTEST_C: B sees" "from-a" "$(chaos_run on 150 SPYTEST_C)"
chk "chaos on, other key: B sees" "(unset)" "$(chaos_run other 150 SOME_OTHER_KEY)"
chk "chaos on, ALL: B sees" "from-a" "$(chaos_run all 150 ALL)"
on_log="$(ls "$work"/logs-on/t2.*.log | head -1)"
chk "chaos on: GET-FOREIGN of C logged" 1 "$(grep -c ' GET-FOREIGN key=SPYTEST_C ' "$on_log")"

echo "--- analyzer"
python3 "$d/analyze.py" --deps "$work" --top 10 "$log"
[ "$fail" = 0 ] && echo "SELFTEST OK" || { echo "SELFTEST FAILED"; exit 1; }
