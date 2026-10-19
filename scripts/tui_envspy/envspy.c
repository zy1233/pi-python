/* LD_PRELOAD spy for process-wide environment variables in a Rust test binary.
 * Linux only (it uses tgkill, backtrace and /proc). Build: see README.md in this directory.
 *
 * The environment of a process is shared by all test threads. A test that calls
 * std::env::set_var / remove_var changes what every other running test reads. This spy finds the
 * places where two tests run at the same time and one of them can see the change of the other.
 *
 * A variable is "tracked" from its first change in this process (setenv / putenv / unsetenv are what
 * std::env::set_var / remove_var call). For each tracked variable the spy keeps:
 *   init   the value before the first change (or "unset"),
 *   cur    the value now,
 *   owner  the thread (tid) that made the last change,
 *   dirty  cur differs from init.
 *
 * Log lines ($ENVSPY_LOG, O_APPEND, one write() per event; a "  BT:" line follows every event):
 *   <ns> SET|UNSET key=K val=V tid=T tname=N dirty_before=0|1 dirty_after=0|1 overlap=0|1 leak=0|1 [noop=1]
 *        The variable was dirty (changed by another thread and not yet put back) when this thread
 *        changed it again:
 *          overlap=1  the other thread is still running: two tests change it at the same time; the
 *                     later change is lost or is undone by the other test.
 *          leak=1     the other thread has ended: its test did not put the variable back.
 *        A "  PREV-OWNER" line and a "  PREV-BT:" line show the other side.
 *   <ns> GET-FOREIGN|GET-LEAK|GET-GLOBAL|GET-OWN key=K seen=V [exists=0|1] owner=T owner_name=N
 *        owner_age_ms=A tid=T tname=N site=H n=C
 *        A getenv() of a dirty variable. The reader is: OWN - the thread that changed it;
 *        FOREIGN - another thread, and the thread that changed it is still running (concurrent
 *        tests); LEAK - another thread, and the thread that changed it has ended (a test left the
 *        variable changed); GLOBAL - another thread, and the change was made by the main thread
 *        (set once for the whole process, which is how isolation is done right).
 *        exists= is written when the value is an absolute path: 0 if the path is not there (a
 *        deleted temporary directory, for example).
 *        Each call site (hash of the backtrace) is logged for its first 3 calls; the exit line
 *        "SITE ..." gives the full count.
 *   <ns> CHDIR path=P tid=T tname=N     (the working directory is process-wide too)
 *   <ns> FINAL key=K val=V owner=T owner_name=N alive=0|1 op=SET|UNSET
 *        At exit: a variable that is still changed (the backtrace is the one of the last change).
 *   <ns> SITE kind=GET-FOREIGN|GET-LEAK|GET-GLOBAL|GET-OWN key=K site=H count=C
 *   <ns> START pid=P exe=E
 *
 * Only the process whose pid equals $ENVSPY_PID logs (children that inherit LD_PRELOAD stay quiet).
 * The values of variables whose name has KEY, TOKEN, SECRET, PASS, AUTH or CRED in it are not logged.
 *
 * Chaos mode ($ENVSPY_CHAOS_MS=N, optional $ENVSPY_CHAOS_KEYS=A,B,C or ALL; the default is ALL):
 * after a thread has changed a variable, its first own read of that variable sleeps N ms. The variable stays changed for N ms
 * while the other tests go on, so a test that reads it without the lock sees the other test's value
 * on every run instead of once in a thousand runs. A test that then fails is a test that depends on
 * the value (a proven reader). The sleep is in the read, not in setenv(): std::env::set_var holds
 * the environment lock of the Rust standard library while it calls setenv(), and a sleep there
 * would stop the other readers instead of letting them see the change.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <execinfo.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define MAX_KEYS 256
#define KEY_MAX 128
#define VAL_MAX 1024
#define BT_DEPTH 40
#define BT_TEXT 4096
#define MAX_SITES 16384
#define SAMPLES_PER_SITE 3

static __thread int in_spy = 0;
static volatile int enabled = 0;
static volatile int any_tracked = 0;
static int log_fd = -1;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static long chaos_ms = 0;
static char chaos_keys[512] = ""; /* ",A,B," or empty for all */

static char *(*real_getenv)(const char *);
static char *(*real_secure_getenv)(const char *);
static int (*real_setenv)(const char *, const char *, int);
static int (*real_unsetenv)(const char *);
static int (*real_putenv)(char *);
static int (*real_chdir)(const char *);
static int (*real_fchdir)(int);

struct keystate {
    int used;
    int captured;
    char name[KEY_MAX];
    int init_set;
    char init_val[VAL_MAX];
    int cur_set;
    char cur_val[VAL_MAX];
    long owner;
    long owner_ts;
    char owner_tname[32];
    char owner_op[8];
    char owner_bt[BT_TEXT];
    unsigned long changes;
    int chaos_done; /* the chaos sleep of the current change has happened */
};
static struct keystate keys[MAX_KEYS];

enum { K_OWN = 0, K_FOREIGN = 1, K_LEAK = 2, K_GLOBAL = 3 };
static const char *kind_name[] = {"OWN", "FOREIGN", "LEAK", "GLOBAL"};

struct site {
    uint64_t hash;
    int key;
    int kind;
    unsigned count;
    int used;
};
static struct site sites[MAX_SITES];

static void resolve(void) {
    if (!real_getenv) real_getenv = dlsym(RTLD_NEXT, "getenv");
    if (!real_secure_getenv) real_secure_getenv = dlsym(RTLD_NEXT, "secure_getenv");
    if (!real_setenv) real_setenv = dlsym(RTLD_NEXT, "setenv");
    if (!real_unsetenv) real_unsetenv = dlsym(RTLD_NEXT, "unsetenv");
    if (!real_putenv) real_putenv = dlsym(RTLD_NEXT, "putenv");
    if (!real_chdir) real_chdir = dlsym(RTLD_NEXT, "chdir");
    if (!real_fchdir) real_fchdir = dlsym(RTLD_NEXT, "fchdir");
}

static long now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000000000L + ts.tv_nsec;
}

static long my_tid(void) { return (long)syscall(SYS_gettid); }

static void thread_name(char *buf, size_t n) {
    buf[0] = 0;
    if (pthread_getname_np(pthread_self(), buf, n) != 0) snprintf(buf, n, "?");
}

static void emit(const char *fmt, ...) {
    char buf[16384];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof buf - 1) n = (int)sizeof buf - 1;
    if (log_fd < 0) {
        const char *p = real_getenv ? real_getenv("ENVSPY_LOG") : NULL;
        if (!p) p = "/tmp/envspy.log";
        log_fd = open(p, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0644);
        if (log_fd < 0) return;
    }
    ssize_t r = write(log_fd, buf, (size_t)n);
    (void)r;
}

static int fmt_bt(void **bt, int c, int skip, char *out, size_t outsz) {
    int off = snprintf(out, outsz, "BT:");
    for (int i = skip; i < c && off < (int)outsz - 200; i++) {
        Dl_info info;
        if (dladdr(bt[i], &info) && info.dli_fname) {
            const char *base = strrchr(info.dli_fname, '/');
            base = base ? base + 1 : info.dli_fname;
            off += snprintf(out + off, outsz - (size_t)off, " %s+0x%lx", base,
                            (unsigned long)((char *)bt[i] - (char *)info.dli_fbase));
        } else {
            off += snprintf(out + off, outsz - (size_t)off, " ?%p", bt[i]);
        }
    }
    return off;
}

static uint64_t hash_bt(void **bt, int c, int skip) {
    uint64_t h = 1469598103934665603ULL;
    for (int i = skip; i < c; i++) {
        h ^= (uint64_t)(uintptr_t)bt[i];
        h *= 1099511628211ULL;
    }
    return h;
}

static int secret_name(const char *n) {
    static const char *words[] = {"KEY", "TOKEN", "SECRET", "PASS", "AUTH", "CRED"};
    for (size_t i = 0; i < sizeof words / sizeof words[0]; i++)
        if (strcasestr(n, words[i])) return 1;
    return 0;
}

/* Shows a value for the log: "unset", "<redacted len=N>" or the text (cut at 200 bytes). */
static void show_val(const char *name, int is_set, const char *v, char *out, size_t outsz) {
    if (!is_set) snprintf(out, outsz, "unset");
    else if (secret_name(name)) snprintf(out, outsz, "<redacted len=%zu>", strlen(v));
    else snprintf(out, outsz, "%.200s", v);
}

static struct keystate *find_key(const char *name, int create) {
    for (int i = 0; i < MAX_KEYS; i++) {
        if (keys[i].used && strncmp(keys[i].name, name, KEY_MAX - 1) == 0 && strlen(name) < KEY_MAX) return &keys[i];
    }
    if (!create) return NULL;
    for (int i = 0; i < MAX_KEYS; i++) {
        if (!keys[i].used) {
            keys[i].used = 1;
            snprintf(keys[i].name, KEY_MAX, "%s", name);
            return &keys[i];
        }
    }
    return NULL;
}

static int is_dirty(const struct keystate *k) {
    if (k->cur_set != k->init_set) return 1;
    if (!k->cur_set) return 0;
    return strcmp(k->cur_val, k->init_val) != 0;
}

static struct site *find_site(uint64_t h, int key, int kind) {
    size_t i = (size_t)(h ^ (uint64_t)key * 31 ^ (uint64_t)kind * 7) % MAX_SITES;
    for (size_t probe = 0; probe < MAX_SITES; probe++, i = (i + 1) % MAX_SITES) {
        struct site *s = &sites[i];
        if (!s->used) {
            s->used = 1;
            s->hash = h;
            s->key = key;
            s->kind = kind;
            return s;
        }
        if (s->hash == h && s->key == key && s->kind == kind) return s;
    }
    return NULL;
}

/* Is the thread with this tid still running? (Test threads end when their test ends.) */
static int thread_alive(long tid) {
    return syscall(SYS_tgkill, (long)getpid(), tid, 0) == 0;
}

/* Before the real call: make the key known and take its initial value. */
static struct keystate *prepare_key(const char *name) {
    pthread_mutex_lock(&mu);
    struct keystate *k = find_key(name, 1);
    if (k && !k->captured) {
        const char *v = real_getenv(name);
        k->init_set = v != NULL;
        if (v) snprintf(k->init_val, VAL_MAX, "%s", v);
        k->cur_set = k->init_set;
        snprintf(k->cur_val, VAL_MAX, "%s", k->init_val);
        k->captured = 1;
    }
    pthread_mutex_unlock(&mu);
    return k;
}

/* After the real call: look at the real value, update the state, log the event. */
static void record_change(struct keystate *k, const char *op) {
    if (!k) return;
    const char *nowv = real_getenv(k->name);
    long tid = my_tid();
    long ts = now_ns();
    char tn[32];
    thread_name(tn, sizeof tn);
    void *bt[BT_DEPTH];
    int c = backtrace(bt, BT_DEPTH);
    char btext[BT_TEXT];
    fmt_bt(bt, c, 2, btext, sizeof btext);

    char prev_tn[32] = "";
    char prev_op[8] = "";
    static char prev_bt[BT_TEXT];  /* guarded by mu */

    pthread_mutex_lock(&mu);
    int noop = (k->cur_set == (nowv != NULL)) && (!nowv || strcmp(k->cur_val, nowv) == 0);
    int dirty_before = is_dirty(k);
    long prev_owner = k->owner;
    long prev_ts = k->owner_ts;
    int other = !noop && dirty_before && prev_owner != tid;
    int prev_alive = other && thread_alive(prev_owner);
    int overlap = other && prev_alive;
    int leak = other && !prev_alive;
    if (other) {
        snprintf(prev_tn, sizeof prev_tn, "%s", k->owner_tname);
        snprintf(prev_op, sizeof prev_op, "%s", k->owner_op);
        snprintf(prev_bt, sizeof prev_bt, "%s", k->owner_bt);
    }
    k->cur_set = nowv != NULL;
    snprintf(k->cur_val, VAL_MAX, "%s", nowv ? nowv : "");
    if (!noop) {
        k->owner = tid;
        k->owner_ts = ts;
        snprintf(k->owner_tname, sizeof k->owner_tname, "%s", tn);
        snprintf(k->owner_op, sizeof k->owner_op, "%s", op);
        snprintf(k->owner_bt, sizeof k->owner_bt, "%s", btext);
    }
    k->changes++;
    if (!noop) k->chaos_done = 0;
    int dirty_after = is_dirty(k);
    any_tracked = 1;
    char val[256];
    show_val(k->name, k->cur_set, k->cur_val, val, sizeof val);
    char name[KEY_MAX];
    snprintf(name, sizeof name, "%s", k->name);
    if (other) {
        emit("%ld %s key=%s val=%s tid=%ld tname=%s dirty_before=%d dirty_after=%d overlap=%d leak=%d\n  %s\n"
             "  PREV-OWNER tid=%ld tname=%s op=%s age_ms=%.1f alive=%d\n  PREV-%s\n",
             ts, op, name, val, tid, tn, dirty_before, dirty_after, overlap, leak, btext, prev_owner, prev_tn,
             prev_op, (double)(ts - prev_ts) / 1e6, prev_alive, prev_bt);
    } else {
        emit("%ld %s key=%s val=%s tid=%ld tname=%s dirty_before=%d dirty_after=%d overlap=0 leak=0%s\n  %s\n", ts,
             op, name, val, tid, tn, dirty_before, dirty_after, noop ? " noop=1" : "", btext);
    }
    pthread_mutex_unlock(&mu);
}

/* Chaos mode: is `tid` another thread than the owner of the change? (Then it does not sleep.) */
static int foreign_reader(const struct keystate *k, long tid) { return k->owner != tid; }

static int chaos_key_ok(const char *name) {
    if (!chaos_keys[0]) return 1;
    char needle[KEY_MAX + 3];
    snprintf(needle, sizeof needle, ",%s,", name);
    return strstr(chaos_keys, needle) != NULL;
}

static void note_get(const char *name, const char *seen) {
    long tid = my_tid();
    pthread_mutex_lock(&mu);
    struct keystate *k = find_key(name, 0);
    if (!k || !is_dirty(k)) {
        pthread_mutex_unlock(&mu);
        return;
    }
    long owner = k->owner;
    double age = (double)(now_ns() - k->owner_ts) / 1e6;
    char otn[32];
    snprintf(otn, sizeof otn, "%s", k->owner_tname);
    int kidx = (int)(k - keys);
    int do_chaos = 0;
    if (chaos_ms > 0 && !foreign_reader(k, tid) && !k->chaos_done && chaos_key_ok(name)) {
        k->chaos_done = 1;
        do_chaos = 1;
    }
    pthread_mutex_unlock(&mu);
    if (do_chaos) usleep((useconds_t)(chaos_ms * 1000));

    int kind = K_OWN;
    if (owner != tid) {
        if (owner == (long)getpid()) kind = K_GLOBAL;
        else kind = thread_alive(owner) ? K_FOREIGN : K_LEAK;
    }

    void *bt[BT_DEPTH];
    int c = backtrace(bt, BT_DEPTH);
    uint64_t h = hash_bt(bt, c, 2);
    pthread_mutex_lock(&mu);
    struct site *s = find_site(h, kidx, kind);
    unsigned n = s ? ++s->count : 0;
    pthread_mutex_unlock(&mu);
    if (n == 0 || n > SAMPLES_PER_SITE) return;
    char tn[32];
    thread_name(tn, sizeof tn);
    char btext[BT_TEXT];
    fmt_bt(bt, c, 2, btext, sizeof btext);
    char val[256];
    show_val(name, seen != NULL, seen ? seen : "", val, sizeof val);
    char ex[16] = "";
    if (seen && seen[0] == '/' && !secret_name(name)) snprintf(ex, sizeof ex, " exists=%d", access(seen, F_OK) == 0);
    emit("%ld GET-%s key=%s seen=%s%s owner=%ld owner_name=%s owner_age_ms=%.1f tid=%ld tname=%s site=%016lx n=%u\n  %s\n",
         now_ns(), kind_name[kind], name, val, ex, owner, otn, age, tid, tn, (unsigned long)h, n, btext);
}

char *getenv(const char *name) {
    resolve();
    char *v = real_getenv(name);
    if (!enabled || !any_tracked || in_spy || !name) return v;
    in_spy = 1;
    note_get(name, v);
    in_spy = 0;
    return v;
}

char *secure_getenv(const char *name) {
    resolve();
    char *v = real_secure_getenv ? real_secure_getenv(name) : real_getenv(name);
    if (!enabled || !any_tracked || in_spy || !name) return v;
    in_spy = 1;
    note_get(name, v);
    in_spy = 0;
    return v;
}

int setenv(const char *name, const char *value, int overwrite) {
    resolve();
    if (!enabled || in_spy || !name) return real_setenv(name, value, overwrite);
    in_spy = 1;
    struct keystate *k = prepare_key(name);
    int rc = real_setenv(name, value, overwrite);
    int err = errno;
    record_change(k, "SET");
    in_spy = 0;
    errno = err;
    return rc;
}

int unsetenv(const char *name) {
    resolve();
    if (!enabled || in_spy || !name) return real_unsetenv(name);
    in_spy = 1;
    struct keystate *k = prepare_key(name);
    int rc = real_unsetenv(name);
    int err = errno;
    record_change(k, "UNSET");
    in_spy = 0;
    errno = err;
    return rc;
}

int putenv(char *string) {
    resolve();
    if (!enabled || in_spy || !string) return real_putenv(string);
    in_spy = 1;
    char name[KEY_MAX];
    const char *eq = strchr(string, '=');
    size_t len = eq ? (size_t)(eq - string) : strlen(string);
    if (len >= sizeof name) len = sizeof name - 1;
    memcpy(name, string, len);
    name[len] = 0;
    struct keystate *k = prepare_key(name);
    int rc = real_putenv(string);
    int err = errno;
    record_change(k, eq ? "SET" : "UNSET");
    in_spy = 0;
    errno = err;
    return rc;
}

static void log_chdir(const char *what, const char *path) {
    char tn[32];
    thread_name(tn, sizeof tn);
    void *bt[BT_DEPTH];
    int c = backtrace(bt, BT_DEPTH);
    char btext[BT_TEXT];
    fmt_bt(bt, c, 2, btext, sizeof btext);
    emit("%ld %s path=%.300s tid=%ld tname=%s\n  %s\n", now_ns(), what, path ? path : "?", my_tid(), tn, btext);
}

int chdir(const char *path) {
    resolve();
    if (enabled && !in_spy) {
        in_spy = 1;
        log_chdir("CHDIR", path);
        in_spy = 0;
    }
    return real_chdir(path);
}

int fchdir(int fd) {
    resolve();
    if (enabled && !in_spy) {
        in_spy = 1;
        log_chdir("FCHDIR", "(fd)");
        in_spy = 0;
    }
    return real_fchdir(fd);
}

__attribute__((constructor)) static void spy_init(void) {
    resolve();
    const char *want = real_getenv ? real_getenv("ENVSPY_PID") : NULL;
    if (!want || (long)getpid() != atol(want)) return;
    /* Load libgcc (backtrace) now, not inside a getenv() call of some other library. */
    void *bt[4];
    (void)backtrace(bt, 4);
    char exe[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof exe - 1);
    exe[n > 0 ? n : 0] = 0;
    const char *cm = real_getenv("ENVSPY_CHAOS_MS");
    if (cm) chaos_ms = atol(cm);
    const char *ck = real_getenv("ENVSPY_CHAOS_KEYS");
    /* "ALL" (or nothing) means: every changed variable gets the chaos sleep. */
    if (ck && ck[0] && strcmp(ck, "ALL") != 0) snprintf(chaos_keys, sizeof chaos_keys, ",%s,", ck);
    enabled = 1;
    in_spy = 1;
    emit("%ld START pid=%ld exe=%s\n", now_ns(), (long)getpid(), exe);
    in_spy = 0;
}

__attribute__((destructor)) static void spy_fini(void) {
    if (!enabled) return;
    in_spy = 1;
    pthread_mutex_lock(&mu);
    /* Variables that are still changed at exit: the test that changed them did not put them back. */
    for (int i = 0; i < MAX_KEYS; i++) {
        struct keystate *k = &keys[i];
        if (!k->used || !k->captured || !is_dirty(k)) continue;
        char val[256];
        show_val(k->name, k->cur_set, k->cur_val, val, sizeof val);
        emit("%ld FINAL key=%s val=%s owner=%ld owner_name=%s alive=%d op=%s\n  %s\n", now_ns(), k->name, val,
             k->owner, k->owner_tname, thread_alive(k->owner), k->owner_op, k->owner_bt);
    }
    for (int i = 0; i < MAX_SITES; i++) {
        struct site *s = &sites[i];
        if (s->used && s->count > SAMPLES_PER_SITE)
            emit("%ld SITE kind=GET-%s key=%s site=%016lx count=%u\n", now_ns(), kind_name[s->kind],
                 keys[s->key].name, (unsigned long)s->hash, s->count);
    }
    pthread_mutex_unlock(&mu);
    in_spy = 0;
}
