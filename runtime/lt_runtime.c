#define _POSIX_C_SOURCE 200809L
#include "lt_runtime.h"
#include "lt_json.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <math.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define LT_RANGE_LIMIT 1000000U
#define LT_EQ_DEPTH_LIMIT 128U

typedef struct { char *key; LtValue value; } LtEntry;
struct LtList { LtValue *items; size_t count, capacity; };
struct LtMap { LtEntry *items; size_t count, capacity; };
typedef struct { char *name; LtValue value; } LtSlot;
struct LtEnv { LtEnv *parent; LtSlot *slots; size_t count; };
struct LtFunction { LtFunctionSpec spec; char *name; char **params; char **kwonly; char *rest; char *extra; LtEnv *closure; };
struct LtClass { char *name; LtClass *parent; LtFunction **methods; size_t count; };
struct LtObject { LtClass *klass; LtMap *fields; };
typedef struct { LtObject *object; LtFunction *function; } LtBound;

static LtHandler *lt_handlers;
static const char *lt_current_path;
static const char *lt_current_function;
static int lt_current_line;
static const char *lt_current_error_type = "RuntimeError";
static const char *lt_last_error_message = "uncaught exception";
static char *lt_internal_repr(LtValue v, int depth);
static char *lt_host_repr(LtValue v);
static int lt_host_truthy(LtValue v);
static LtValue lt_host_len(LtValue v);
static LtValue lt_host_getattr(LtValue v, const char *name);
static LtValue lt_host_call_method(LtValue v, const char *name,
                                   const LtArg *args, size_t count);
static LtValue lt_host_call_value(LtValue v, const LtArg *args, size_t count);
static LtValue lt_host_setattr(LtValue v, const char *name, LtValue value);
static LtValue lt_host_index(LtValue v, LtValue key);
static LtValue lt_host_setindex(LtValue v, LtValue key, LtValue value);
static LtValue lt_host_iter_values(LtValue v);
static int lt_host_python_operand(LtValue v);
static LtValue lt_host_binop(const char *name, const LtValue *args, size_t count);

static void *lt_alloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) { fputs("Latent runtime: out of memory\n", stderr); exit(EXIT_FAILURE); }
    return p;
}
static void *lt_realloc(void *p, size_t n) {
    void *q = realloc(p, n ? n : 1);
    if (!q) { fputs("Latent runtime: out of memory\n", stderr); exit(EXIT_FAILURE); }
    return q;
}
static char *lt_strdup(const char *s) {
    if (!s) s = "";
    size_t n = strlen(s) + 1;
    char *r = lt_alloc(n);
    memcpy(r, s, n);
    return r;
}
static char *lt_fmt(const char *fmt, ...) {
    va_list ap, cp;
    va_start(ap, fmt); va_copy(cp, ap);
    int n = vsnprintf(NULL, 0, fmt, cp); va_end(cp);
    if (n < 0) { va_end(ap); return lt_strdup("format error"); }
    char *s = lt_alloc((size_t)n + 1);
    (void)vsnprintf(s, (size_t)n + 1, fmt, ap); va_end(ap);
    return s;
}
static LtValue value(LtTag tag) { LtValue v; memset(&v, 0, sizeof(v)); v.tag = tag; return v; }
LtValue lt_nil(void) { return value(LT_NIL); }
LtValue lt_bool(int b) { LtValue v = value(LT_BOOL); v.as.boolean = !!b; return v; }
LtValue lt_int(int64_t i) { LtValue v = value(LT_INT); v.as.integer = i; return v; }
LtValue lt_float(double d) { LtValue v = value(LT_FLOAT); v.as.real = d; return v; }
LtValue lt_str(const char *s) { LtValue v = value(LT_STR); v.as.string = lt_strdup(s ? s : ""); return v; }
static LtValue missing(void) { return value(LT_MISSING); }
int lt_is_missing(LtValue v) { return v.tag == LT_MISSING; }

static const char *host_type_name(LtValue v) {
    const LtHostHandle *h = (const LtHostHandle *)v.as.pointer;
    if (!h) return "host value";
    switch (h->kind) {
        case LT_HOST_PY_MODULE: return "Python module";
        case LT_HOST_PY_OBJECT: return "Python object";
        case LT_HOST_PY_MODULE_FUNCTION: return "Python function";
        case LT_HOST_PY_BOUND_METHOD: return "Python bound method";
        case LT_HOST_JAVA_CLASS: return "Java class";
        case LT_HOST_JAVA_OBJECT: return "Java object";
        case LT_HOST_JAVA_STATIC_METHOD: return "Java static method";
        case LT_HOST_JAVA_BOUND_METHOD: return "Java bound method";
    }
    return "host value";
}
static const char *type_name(LtValue v) {
    switch (v.tag) {
        case LT_NIL: return "nil"; case LT_BOOL: return "bool"; case LT_INT: return "int";
        case LT_FLOAT: return "num"; case LT_STR: return "str"; case LT_LIST: return "list";
        case LT_MAP: return "map"; case LT_FUNCTION: case LT_BOUND_METHOD: return "function";
        case LT_CLASS: return "class"; case LT_OBJECT: return "object";
        case LT_HOST: return host_type_name(v); case LT_MISSING: return "missing";
    }
    return "value";
}
static void throw_message(const char *type, const char *message) {
    LtHandler *h;
    lt_current_error_type = type ? type : "RuntimeError";
    lt_last_error_message = lt_strdup(message ? message : "");
    if ((h = lt_handlers) != NULL) {
        h->message = lt_strdup(message ? message : "");
        h->error_type = lt_current_error_type;
        longjmp(h->buf, 1);
    }
    fprintf(stderr, "%s%s%s: Latent runtime error: %s: %s\n",
            lt_current_path ? lt_current_path : "<latent>",
            lt_current_path ? ":" : "", lt_current_path ? lt_fmt("%d", lt_current_line) : "",
            lt_current_error_type, message ? message : "");
    fflush(stderr);
    lt_runtime_shutdown();
    exit(EXIT_FAILURE);
}
static void fail(const char *type, const char *fmt, ...) {
    va_list ap, cp; va_start(ap, fmt); va_copy(cp, ap);
    int n = vsnprintf(NULL, 0, fmt, cp); va_end(cp);
    if (n < 0) { va_end(ap); throw_message(type, "runtime error"); }
    char *s = lt_alloc((size_t)n + 1); (void)vsnprintf(s, (size_t)n + 1, fmt, ap); va_end(ap);
    throw_message(type, s);
}
void lt_handler_push(LtHandler *h) { if (!h) fail("RuntimeError", "null exception handler"); h->previous = lt_handlers; h->message = NULL; h->error_type = NULL; lt_handlers = h; }
void lt_handler_pop(LtHandler *h) {
    if (lt_handlers != h) fail("RuntimeError", "exception handler stack mismatch");
    lt_handlers = h->previous;
}
const char *lt_handler_message(const LtHandler *h) { return h && h->message ? h->message : ""; }
void lt_throw(LtValue v) {
    char *s;
    if (v.tag == LT_STR) s = (char *)v.as.string;
    else {
        /* Implemented below; declaration keeps user throw values consistent with str(). */
        s = lt_internal_repr(v, 0);
    }
    throw_message("Exception", s);
}
void lt_report_uncaught(void) {
    fprintf(stderr, "%s%s%s: Latent runtime error: %s: %s\n",
            lt_current_path ? lt_current_path : "<latent>",
            lt_current_path ? ":" : "", lt_current_path ? lt_fmt("%d", lt_current_line) : "",
            lt_current_error_type, lt_last_error_message ? lt_last_error_message : "uncaught exception");
    fflush(stderr);
    lt_runtime_shutdown();
    exit(EXIT_FAILURE);
}
void lt_set_location(const char *path, int line, const char *function) {
    lt_current_path = path ? lt_strdup(path) : NULL;
    lt_current_line = line;
    lt_current_function = function ? lt_strdup(function) : NULL;
}

static size_t map_find(const LtMap *m, const char *key) {
    size_t i; if (!m) return SIZE_MAX;
    for (i = 0; i < m->count; i++) if (strcmp(m->items[i].key, key) == 0) return i;
    return SIZE_MAX;
}
static void map_set(LtMap *m, const char *key, LtValue v) {
    size_t i = map_find(m, key);
    if (!key) fail("TypeError", "map key must not be null");
    if (i != SIZE_MAX) { m->items[i].value = v; return; }
    if (m->count == m->capacity) { if (m->capacity > SIZE_MAX / 2) fail("OverflowError", "map too large"); m->capacity = m->capacity ? m->capacity * 2 : 8; if (m->capacity > SIZE_MAX / sizeof(*m->items)) fail("OverflowError", "map too large"); m->items = lt_realloc(m->items, m->capacity * sizeof(*m->items)); }
    m->items[m->count].key = lt_strdup(key); m->items[m->count].value = v; m->count++;
}
LtValue lt_list(const LtValue *xs, size_t n) {
    if (n && !xs) fail("TypeError", "list values must not be null");
    if (n > SIZE_MAX / sizeof(*xs)) fail("OverflowError", "list too large");
    LtValue v = value(LT_LIST); LtList *l = lt_alloc(sizeof(*l));
    l->count = n; l->capacity = n; l->items = n ? lt_alloc(n * sizeof(*xs)) : NULL;
    if (n) memcpy(l->items, xs, n * sizeof(*xs));
    v.as.list = l; return v;
}
LtValue lt_map(const char *const *keys, const LtValue *vals, size_t n) {
    if (n && (!keys || !vals)) fail("TypeError", "map entries must not be null");
    LtValue v = value(LT_MAP); LtMap *m = lt_alloc(sizeof(*m)); memset(m, 0, sizeof(*m));
    for (size_t i = 0; i < n; i++) { if (!keys[i]) fail("TypeError", "map keys must be strings"); map_set(m, keys[i], vals[i]); }
    v.as.map = m; return v;
}

LtEnv *lt_env_new(LtEnv *parent, const char *const *names, size_t n) {
    if (n && !names) fail("TypeError", "environment names must not be null");
    if (n > SIZE_MAX / sizeof(LtSlot)) fail("OverflowError", "environment too large");
    LtEnv *e = lt_alloc(sizeof(*e)); e->parent = parent; e->count = n; e->slots = n ? lt_alloc(n * sizeof(*e->slots)) : NULL;
    for (size_t i = 0; i < n; i++) { if (!names[i]) fail("TypeError", "environment name must not be null"); e->slots[i].name = lt_strdup(names[i]); e->slots[i].value = lt_nil(); }
    return e;
}
static size_t env_find(LtEnv *e, const char *name) {
    if (!e || !name) return SIZE_MAX;
    for (size_t i = 0; i < e->count; i++) if (strcmp(e->slots[i].name, name) == 0) return i;
    return SIZE_MAX;
}
LtValue lt_env_get(LtEnv *e, const char *name) {
    for (; e; e = e->parent) { size_t i = env_find(e, name); if (i != SIZE_MAX) return e->slots[i].value; }
    fail("RuntimeError", "unknown lexical binding '%s'", name ? name : ""); return lt_nil();
}
void lt_env_set_local(LtEnv *e, const char *name, LtValue v) {
    size_t i = env_find(e, name); if (i == SIZE_MAX) fail("RuntimeError", "unknown local binding '%s'", name ? name : ""); e->slots[i].value = v;
}
void lt_env_set_enclosing(LtEnv *e, const char *name, LtValue v) {
    if (e) e = e->parent;
    for (; e; e = e->parent) { size_t i = env_find(e, name); if (i != SIZE_MAX) { e->slots[i].value = v; return; } }
    fail("RuntimeError", "unknown enclosing binding '%s'", name ? name : "");
}

static LtValue *list_append(LtList *l, LtValue v) {
    if (l->count == l->capacity) { if (l->capacity > SIZE_MAX / 2) fail("OverflowError", "list too large"); size_t cap = l->capacity ? l->capacity * 2 : 8; if (cap > SIZE_MAX / sizeof(*l->items)) fail("OverflowError", "list too large"); l->items = lt_realloc(l->items, cap * sizeof(*l->items)); l->capacity = cap; }
    l->items[l->count++] = v; return &l->items[l->count - 1];
}
static LtValue list_value_empty(void) { LtValue v = value(LT_LIST); LtList *l = lt_alloc(sizeof(*l)); memset(l, 0, sizeof(*l)); v.as.list = l; return v; }
static LtValue map_value_empty(void) { LtValue v = value(LT_MAP); LtMap *m = lt_alloc(sizeof(*m)); memset(m, 0, sizeof(*m)); v.as.map = m; return v; }

/* UTF-8 decoder rejects malformed, overlong, surrogate and out-of-range sequences. */
static size_t utf8_next(const unsigned char *s, size_t n, size_t i, uint32_t *cp) {
    unsigned char a; size_t need; uint32_t x;
    if (i >= n) return 0;
    a = s[i];
    if (a < 0x80) { *cp = a; return 1; }
    if (a >= 0xC2 && a <= 0xDF) { need = 2; x = a & 0x1FU; }
    else if (a >= 0xE0 && a <= 0xEF) { need = 3; x = a & 0x0FU; }
    else if (a >= 0xF0 && a <= 0xF4) { need = 4; x = a & 0x07U; }
    else fail("UnicodeError", "invalid UTF-8 string");
    if (i + need > n) fail("UnicodeError", "invalid UTF-8 string");
    for (size_t j = 1; j < need; j++) { unsigned char b = s[i + j]; if ((b & 0xC0U) != 0x80U) fail("UnicodeError", "invalid UTF-8 string"); x = (x << 6) | (b & 0x3FU); }
    if ((need == 3 && x < 0x800U) || (need == 4 && x < 0x10000U) || x > 0x10FFFFU || (x >= 0xD800U && x <= 0xDFFFU)) fail("UnicodeError", "invalid UTF-8 string");
    *cp = x; return need;
}
static size_t utf8_count(const char *s) {
    size_t n = strlen(s), i = 0, count = 0; uint32_t cp;
    while (i < n) { i += utf8_next((const unsigned char *)s, n, i, &cp); count++; }
    return count;
}
static LtValue utf8_at(const char *s, size_t idx) {
    size_t n = strlen(s), i = 0, count = 0; uint32_t cp;
    while (i < n) { size_t bytes = utf8_next((const unsigned char *)s, n, i, &cp); if (count++ == idx) { char *r = lt_alloc(bytes + 1); memcpy(r, s + i, bytes); r[bytes] = '\0'; LtValue v = value(LT_STR); v.as.string = r; return v; } i += bytes; }
    fail("IndexError", "index out of range: %zu", idx); return lt_nil();
}
static char *repr_q(LtValue v, int depth);
static char *lt_internal_repr(LtValue v, int depth) {
    if (depth > (int)LT_EQ_DEPTH_LIMIT) return lt_strdup("<recursive>");
    switch (v.tag) {
        case LT_NIL: return lt_strdup("nil");
        case LT_BOOL: return lt_strdup(v.as.boolean ? "true" : "false");
        case LT_INT: return lt_fmt("%" PRId64, v.as.integer);
        case LT_FLOAT: {
            if (isnan(v.as.real)) return lt_strdup("nan");
            if (isinf(v.as.real)) return lt_strdup(signbit(v.as.real) ? "-inf" : "inf");
            char b[64];
            for (int precision = 1; precision <= 17; precision++) {
                (void)snprintf(b, sizeof(b), "%.*g", precision, v.as.real);
                char *end = NULL; double parsed = strtod(b, &end);
                if (end && *end == 0 && parsed == v.as.real && signbit(parsed) == signbit(v.as.real)) break;
            }
            return lt_strdup(b);
        }
        case LT_STR: return lt_strdup(v.as.string ? v.as.string : "");
        case LT_FUNCTION: return lt_fmt("<function %s>", v.as.function->name);
        case LT_BOUND_METHOD: { LtBound *b = v.as.pointer; return lt_fmt("<bound method %s.%s>", b->object->klass->name, b->function->name); }
        case LT_CLASS: return lt_fmt("<class %s>", v.as.klass->name);
        case LT_OBJECT: return lt_fmt("<%s object>", v.as.object->klass->name);
        case LT_HOST: return lt_host_repr(v);
        case LT_MISSING: return lt_strdup("<missing>");
        case LT_LIST: {
            size_t cap = 2, len = 0; char *s = lt_alloc(cap); s[len++] = '[';
            for (size_t i = 0; i < v.as.list->count; i++) { char *x = repr_q(v.as.list->items[i], depth + 1); size_t z = strlen(x) + (i ? 2 : 0); if (len + z + 2 > cap) { while (len + z + 2 > cap) cap *= 2; s = lt_realloc(s, cap); } if (i) { s[len++] = ','; s[len++] = ' '; } memcpy(s + len, x, strlen(x)); len += strlen(x); }
            if (len + 2 > cap) s = lt_realloc(s, len + 2);
            s[len++] = ']'; s[len] = 0; return s;
        }
        case LT_MAP: {
            size_t cap = 2, len = 0; char *s = lt_alloc(cap); s[len++] = '{';
            for (size_t i = 0; i < v.as.map->count; i++) { LtValue k = lt_str(v.as.map->items[i].key); char *a = repr_q(k, depth + 1), *b = repr_q(v.as.map->items[i].value, depth + 1); size_t need = strlen(a) + strlen(b) + (i ? 4 : 2); while (len + need > cap) cap *= 2; s = lt_realloc(s, cap); if (i) { s[len++] = ','; s[len++] = ' '; } size_t al = strlen(a), bl = strlen(b); memcpy(s + len, a, al); len += al; s[len++] = ':'; s[len++] = ' '; memcpy(s + len, b, bl); len += bl; }
            if (len + 2 > cap) s = lt_realloc(s, len + 2);
            s[len++] = '}'; s[len] = 0; return s;
        }
    }
    return lt_strdup("<value>");
}
static char *repr_q(LtValue v, int depth) {
    if (v.tag != LT_STR) return lt_internal_repr(v, depth);
    const unsigned char *p = (const unsigned char *)(v.as.string ? v.as.string : "");
    size_t n = strlen((const char *)p), cap = n * 6 + 3, len = 0; char *r = lt_alloc(cap); r[len++] = '"';
    for (size_t i = 0; i < n; i++) {
        unsigned char c = p[i];
        if (c == '"' || c == '\\') { r[len++] = '\\'; r[len++] = (char)c; }
        else if (c == '\n') { r[len++] = '\\'; r[len++] = 'n'; }
        else if (c == '\r') { r[len++] = '\\'; r[len++] = 'r'; }
        else if (c == '\t') { r[len++] = '\\'; r[len++] = 't'; }
        else if (c < 0x20) { int z = snprintf(r + len, cap - len, "\\u%04x", c); if (z > 0) len += (size_t)z; }
        else r[len++] = (char)c;
    }
    r[len++] = '"'; r[len] = 0; return r;
}

static int numeric(LtValue v) { return v.tag == LT_INT || v.tag == LT_FLOAT; }
static double as_double(LtValue v, const char *op) {
    if (v.tag == LT_FLOAT) return v.as.real;
    if (v.tag == LT_INT) {
        double d = (double)v.as.integer;
        if (d < 0x1p63 && d >= -0x1p63 && (int64_t)d == v.as.integer) return d;
        fail("TypeError", "cannot mix int and float without precision loss");
    }
    fail("TypeError", "bad %s operand: %s", op, type_name(v)); return 0;
}
static int64_t checked_add(int64_t a, int64_t b) { if ((b > 0 && a > INT64_MAX - b) || (b < 0 && a < INT64_MIN - b)) fail("OverflowError", "int64 overflow"); return a + b; }
static int64_t checked_sub(int64_t a, int64_t b) { if ((b < 0 && a > INT64_MAX + b) || (b > 0 && a < INT64_MIN + b)) fail("OverflowError", "int64 overflow"); return a - b; }
static int64_t checked_mul(int64_t a, int64_t b) {
    if (!a || !b) return 0;
    if ((a == -1 && b == INT64_MIN) || (b == -1 && a == INT64_MIN)) fail("OverflowError", "int64 overflow");
    if (a > 0 ? (b > 0 ? a > INT64_MAX / b : b < INT64_MIN / a) : (b > 0 ? a < INT64_MIN / b : a < INT64_MAX / b)) fail("OverflowError", "int64 overflow");
    return a * b;
}
static int compare_numeric(LtValue a, LtValue b) {
    if (a.tag == LT_INT && b.tag == LT_INT) return (a.as.integer > b.as.integer) - (a.as.integer < b.as.integer);
    if (a.tag == LT_FLOAT && b.tag == LT_FLOAT) return (a.as.real > b.as.real) - (a.as.real < b.as.real);
    /* Exact cross-type comparison avoids rounding the integer to binary64. */
    LtValue f = a.tag == LT_FLOAT ? a : b, i = a.tag == LT_INT ? a : b;
    double d = f.as.real; int c;
    if (isnan(d)) return 2;
    if (d >= 0x1p63) c = 1;
    else if (d < -0x1p63) c = -1;
    else { int64_t t = (int64_t)d; if (t < i.as.integer) c = -1; else if (t > i.as.integer) c = 1; else { double td = (double)t; c = td < d ? 1 : td > d ? -1 : 0; } }
    return a.tag == LT_FLOAT ? c : -c;
}
static LtValue numeric_bin(LtValue a, LtValue b, const char *op, char code) {
    if (!numeric(a)) fail("TypeError", "bad %s operand: %s", op, type_name(a));
    if (!numeric(b)) fail("TypeError", "bad %s operand: %s", op, type_name(b));
    if (a.tag == LT_INT && b.tag == LT_INT) {
        if (code == '+') return lt_int(checked_add(a.as.integer, b.as.integer));
        if (code == '-') return lt_int(checked_sub(a.as.integer, b.as.integer));
        if (code == '*') return lt_int(checked_mul(a.as.integer, b.as.integer));
    }
    return lt_float(code == '+' ? as_double(a, op) + as_double(b, op) : code == '-' ? as_double(a, op) - as_double(b, op) : as_double(a, op) * as_double(b, op));
}
int lt_truthy(LtValue v) {
    switch (v.tag) {
        case LT_NIL: case LT_MISSING: return 0; case LT_BOOL: return v.as.boolean != 0;
        case LT_INT: return v.as.integer != 0; case LT_FLOAT: return v.as.real != 0.0;
        case LT_STR: return v.as.string && v.as.string[0] != 0;
        case LT_LIST: return v.as.list->count != 0; case LT_MAP: return v.as.map->count != 0;
        case LT_HOST: return lt_host_truthy(v);
        default: return 1;
    }
}
LtValue lt_add(LtValue a, LtValue b) {
    if (lt_host_python_operand(a) || lt_host_python_operand(b)) {
        LtValue args[] = {a, b};
        return lt_host_binop("__add__", args, 2);
    }
    if (a.tag == LT_STR && b.tag == LT_STR) { size_t x = strlen(a.as.string), y = strlen(b.as.string); char *s = lt_alloc(x + y + 1); memcpy(s, a.as.string, x); memcpy(s + x, b.as.string, y + 1); LtValue v = value(LT_STR); v.as.string = s; return v; }
    if (a.tag == LT_LIST && b.tag == LT_LIST) { LtValue r = list_value_empty(); for (size_t i = 0; i < a.as.list->count; i++) list_append(r.as.list, a.as.list->items[i]); for (size_t i = 0; i < b.as.list->count; i++) list_append(r.as.list, b.as.list->items[i]); return r; }
    return numeric_bin(a, b, "+", '+');
}
LtValue lt_sub(LtValue a, LtValue b) {
    if (lt_host_python_operand(a) || lt_host_python_operand(b)) {
        LtValue args[] = {a, b};
        return lt_host_binop("__sub__", args, 2);
    }
    return numeric_bin(a, b, "-", '-');
}
LtValue lt_mul(LtValue a, LtValue b) {
    if (lt_host_python_operand(a) || lt_host_python_operand(b)) {
        LtValue args[] = {a, b};
        return lt_host_binop("__mul__", args, 2);
    }
    return numeric_bin(a, b, "*", '*');
}
LtValue lt_div(LtValue a, LtValue b) {
    if (lt_host_python_operand(a) || lt_host_python_operand(b)) {
        LtValue args[] = {a, b};
        return lt_host_binop("__truediv__", args, 2);
    }
    if (!numeric(a)) fail("TypeError", "bad / operand: %s", type_name(a));
    if (!numeric(b)) fail("TypeError", "bad / operand: %s", type_name(b));
    double x = a.tag == LT_INT && b.tag == LT_INT ? (double)a.as.integer : as_double(a, "/");
    double y = a.tag == LT_INT && b.tag == LT_INT ? (double)b.as.integer : as_double(b, "/");
    if (y == 0.0) fail("ZeroDivisionError", "division by zero");
    return lt_float(x / y);
}
LtValue lt_mod(LtValue a, LtValue b) {
    if (lt_host_python_operand(a) || lt_host_python_operand(b)) {
        LtValue args[] = {a, b};
        return lt_host_binop("__mod__", args, 2);
    }
    if (!numeric(a)) fail("TypeError", "bad % operand: %s", type_name(a));
    if (!numeric(b)) fail("TypeError", "bad % operand: %s", type_name(b));
    if (a.tag == LT_INT && b.tag == LT_INT) { int64_t x = a.as.integer, y = b.as.integer; if (!y) fail("ZeroDivisionError", "division by zero"); if (x == INT64_MIN && y == -1) return lt_int(0); int64_t r = x % y; if (r && ((r < 0) != (y < 0))) r += y; return lt_int(r); }
    double x = as_double(a, "%"), y = as_double(b, "%"); if (y == 0) fail("ZeroDivisionError", "division by zero");
    if (isnan(x) || isnan(y) || isinf(x)) return lt_float(NAN);
    if (isinf(y)) return lt_float(copysign(fabs(y), y));
    double r = fmod(x, y); if (r != 0 && signbit(r) != signbit(y)) r += y; if (r == 0) r = copysign(0.0, y); return lt_float(r);
}
LtValue lt_pow(LtValue a, LtValue b) {
    if (lt_host_python_operand(a) || lt_host_python_operand(b)) {
        LtValue args[] = {a, b};
        return lt_host_binop("__pow__", args, 2);
    }
    if (!numeric(a)) fail("TypeError", "bad ** operand: %s", type_name(a));
    if (!numeric(b)) fail("TypeError", "bad ** operand: %s", type_name(b));
    if (a.tag == LT_INT && b.tag == LT_INT && b.as.integer >= 0) { int64_t r = 1, base = a.as.integer; uint64_t e = (uint64_t)b.as.integer; while (e) { if (e & 1U) r = checked_mul(r, base); e >>= 1; if (e) base = checked_mul(base, base); } return lt_int(r); }
    double x = as_double(a, "**"), y = as_double(b, "**");
    if (isnan(y) || (x < 0 && isfinite(y) && y != trunc(y))) return lt_float(NAN);
    return lt_float(pow(x, y));
}
LtValue lt_neg(LtValue v) {
    if (lt_host_python_operand(v)) return lt_host_binop("__neg__", &v, 1);
    if (v.tag == LT_INT) { if (v.as.integer == INT64_MIN) fail("OverflowError", "int64 overflow"); return lt_int(-v.as.integer); }
    if (v.tag == LT_FLOAT) return lt_float(-v.as.real);
    fail("TypeError", "bad unary - operand: %s", type_name(v)); return lt_nil();
}

static int equal_rec(LtValue a, LtValue b, unsigned depth) {
    if (depth > (int)LT_EQ_DEPTH_LIMIT) fail("RuntimeError", "container equality nesting exceeds 128 levels");
    if (numeric(a) && numeric(b)) { if ((a.tag == LT_FLOAT && isnan(a.as.real)) || (b.tag == LT_FLOAT && isnan(b.as.real))) return 0; return compare_numeric(a,b) == 0; }
    if (a.tag != b.tag) return 0;
    switch (a.tag) {
        case LT_NIL: case LT_MISSING: return 1;
        case LT_INT: case LT_FLOAT: return 0;
        case LT_BOOL: return a.as.boolean == b.as.boolean;
        case LT_STR: return strcmp(a.as.string, b.as.string) == 0;
        case LT_LIST:
            if (a.as.list == b.as.list) { /* Still compare contents: NaN makes a container unequal to itself. */ }
            if (a.as.list->count != b.as.list->count) return 0;
            for (size_t i = 0; i < a.as.list->count; i++) if (!equal_rec(a.as.list->items[i], b.as.list->items[i], depth + 1)) return 0;
            return 1;
        case LT_MAP:
            if (a.as.map->count != b.as.map->count) return 0;
            for (size_t i = 0; i < a.as.map->count; i++) { size_t j = map_find(b.as.map, a.as.map->items[i].key); if (j == SIZE_MAX || !equal_rec(a.as.map->items[i].value, b.as.map->items[j].value, depth + 1)) return 0; }
            return 1;
        case LT_FUNCTION: return a.as.function == b.as.function;
        case LT_CLASS: return a.as.klass == b.as.klass;
        case LT_OBJECT: return a.as.object == b.as.object;
        case LT_HOST: {
            const LtHostHandle *x = (const LtHostHandle *)a.as.pointer;
            const LtHostHandle *y = (const LtHostHandle *)b.as.pointer;
            if (x == y) return 1;
            if (!x || !y || x->kind != y->kind) return 0;
            if (x->kind == LT_HOST_PY_MODULE || x->kind == LT_HOST_JAVA_CLASS)
                return x->name && y->name && strcmp(x->name, y->name) == 0;
            return x->id == y->id;
        }
        case LT_BOUND_METHOD: return a.as.pointer == b.as.pointer;
    }
    return 0;
}
LtValue lt_eq(LtValue a, LtValue b) { return lt_bool(equal_rec(a,b,0)); }
LtValue lt_ne(LtValue a, LtValue b) { return lt_bool(!equal_rec(a,b,0)); }
static int codepoint_cmp(const char *a, const char *b) {
    size_t an = strlen(a), bn = strlen(b), ai = 0, bi = 0; uint32_t x, y;
    while (ai < an && bi < bn) { ai += utf8_next((const unsigned char *)a, an, ai, &x); bi += utf8_next((const unsigned char *)b, bn, bi, &y); if (x != y) return x < y ? -1 : 1; }
    return ai == an ? (bi == bn ? 0 : -1) : 1;
}
static int compare_values(LtValue a, LtValue b, const char *op) {
    if (numeric(a) && numeric(b)) return compare_numeric(a,b);
    if (a.tag == LT_STR && b.tag == LT_STR) return codepoint_cmp(a.as.string,b.as.string);
    fail("TypeError", "bad %s operands: %s, %s", op, type_name(a), type_name(b)); return 0;
}
static int unordered(LtValue a, LtValue b) { return (a.tag == LT_FLOAT && isnan(a.as.real)) || (b.tag == LT_FLOAT && isnan(b.as.real)); }
LtValue lt_lt(LtValue a,LtValue b){return lt_bool(!unordered(a,b)&&compare_values(a,b,"<")<0);}
LtValue lt_lte(LtValue a,LtValue b){return lt_bool(!unordered(a,b)&&compare_values(a,b,"<=")<=0);}
LtValue lt_gt(LtValue a,LtValue b){return lt_bool(!unordered(a,b)&&compare_values(a,b,">")>0);}
LtValue lt_gte(LtValue a,LtValue b){return lt_bool(!unordered(a,b)&&compare_values(a,b,">=")>=0);}
LtValue lt_str_of(LtValue v) { LtValue r = value(LT_STR); r.as.string = lt_internal_repr(v,0); return r; }
LtValue lt_say(LtValue v) { char *s = lt_internal_repr(v,0); puts(s); return lt_nil(); }

static int64_t to_index(LtValue v, size_t n) {
    if (v.tag != LT_INT) fail("TypeError", "index must be an integer");
    int64_t raw = v.as.integer;
    if (n > (size_t)INT64_MAX) fail("IndexError", "index out of range: %" PRId64, raw);
    int64_t count = (int64_t)n, idx = raw;
    if (idx < 0) { if (idx < -count) fail("IndexError", "index out of range: %" PRId64, raw); idx += count; }
    if (idx < 0 || idx >= count) fail("IndexError", "index out of range: %" PRId64, raw);
    return idx;
}
LtValue lt_len(LtValue v) {
    if (v.tag == LT_LIST) { if (v.as.list->count > INT64_MAX) fail("OverflowError", "int64 overflow"); return lt_int((int64_t)v.as.list->count); }
    if (v.tag == LT_MAP) { if (v.as.map->count > INT64_MAX) fail("OverflowError", "int64 overflow"); return lt_int((int64_t)v.as.map->count); }
    if (v.tag == LT_STR) { size_t n = utf8_count(v.as.string); if (n > INT64_MAX) fail("OverflowError", "int64 overflow"); return lt_int((int64_t)n); }
    if (v.tag == LT_HOST) return lt_host_len(v);
    fail("RuntimeError", "len() of %s", type_name(v)); return lt_nil();
}
LtValue lt_index(LtValue o, LtValue k) {
    if (o.tag == LT_HOST) return lt_host_index(o, k);
    if (o.tag == LT_LIST) return o.as.list->items[to_index(k,o.as.list->count)];
    if (o.tag == LT_STR) return utf8_at(o.as.string,(size_t)to_index(k,utf8_count(o.as.string)));
    if (o.tag == LT_MAP) { if (k.tag != LT_STR) fail("TypeError", "map key must be a string"); size_t i = map_find(o.as.map,k.as.string); if (i == SIZE_MAX) fail("RuntimeError", "key not found: %s", lt_internal_repr(k,0)); return o.as.map->items[i].value; }
    fail("RuntimeError", "cannot index %s", type_name(o)); return lt_nil();
}
LtValue lt_wsetindex(LtValue o, LtValue k, LtValue v) {
    if (o.tag == LT_HOST) return lt_host_setindex(o, k, v);
    if (o.tag == LT_LIST) { o.as.list->items[to_index(k,o.as.list->count)] = v; return lt_nil(); }
    if (o.tag == LT_MAP) { if (k.tag != LT_STR) fail("TypeError", "map key must be a string"); map_set(o.as.map,k.as.string,v); return lt_nil(); }
    fail("RuntimeError", "cannot index-assign %s", type_name(o)); return lt_nil();
}
LtValue lt_push(LtValue l, LtValue v) { if (l.tag != LT_LIST) fail("RuntimeError", "push() target must be a list"); (void)list_append(l.as.list,v); return lt_nil(); }
LtValue lt_keys(LtValue v) { if (v.tag != LT_MAP) fail("RuntimeError", "keys() of non-map"); LtValue r = list_value_empty(); for (size_t i=0;i<v.as.map->count;i++) list_append(r.as.list,lt_str(v.as.map->items[i].key)); return r; }
static int64_t range_endpoint(LtValue v) {
    if (v.tag == LT_INT) return v.as.integer;
    if (v.tag != LT_FLOAT) fail("TypeError", "number expected, got %s", lt_internal_repr(v,0));
    if (!isfinite(v.as.real)) fail("ArgumentError", "range() arguments must be finite");
    if (v.as.real < -0x1p63 || v.as.real >= 0x1p63) fail("ArgumentError", "range() arguments outside signed 64-bit range");
    return (int64_t)v.as.real;
}
LtValue lt_range2(LtValue a, LtValue b) {
    int64_t start=range_endpoint(a), stop=range_endpoint(b); LtValue r=list_value_empty();
    if (stop <= start) return r;
    uint64_t n=(uint64_t)stop-(uint64_t)start;
    if (n > LT_RANGE_LIMIT) fail("ArgumentError", "range() result too large (maximum 1000000 items)");
    for (uint64_t i=0;i<n;i++) list_append(r.as.list,lt_int(start+(int64_t)i));
    return r;
}
LtValue lt_range1(LtValue n) { return lt_range2(lt_int(0),n); }
LtValue lt_to_int(LtValue v) {
    if (v.tag == LT_BOOL) return lt_int(v.as.boolean ? 1 : 0);
    if (v.tag == LT_INT) return v;
    if (v.tag == LT_FLOAT) { if (!isfinite(v.as.real)) fail("ArgumentError", "int() of %s", lt_internal_repr(v,0)); if (v.as.real < -0x1p63 || v.as.real >= 0x1p63) fail("OverflowError", "int64 overflow"); return lt_int((int64_t)v.as.real); }
    if (v.tag == LT_STR) {
        const char *s=v.as.string; while (*s && isspace((unsigned char)*s)) s++; const char *end=s+strlen(s); while (end>s&&isspace((unsigned char)end[-1])) end--;
        if (s==end) fail("ArgumentError", "invalid literal for int()");
        size_t n=(size_t)(end-s); char *tmp=lt_alloc(n+1); memcpy(tmp,s,n); tmp[n]=0;
        char *p=tmp; if (*p=='+'||*p=='-') p++; if (!*p) fail("ArgumentError", "invalid literal for int()"); for (char *q=p;*q;q++) if (*q<'0'||*q>'9') fail("ArgumentError", "invalid literal for int()");
        errno=0; char *tail=NULL; long long x=strtoll(tmp,&tail,10); if (errno==ERANGE) fail("OverflowError", "int64 overflow"); if (!tail||*tail) fail("ArgumentError", "invalid literal for int()"); return lt_int((int64_t)x);
    }
    fail("TypeError", "int() of %s", type_name(v)); return lt_nil();
}

LtIterator lt_iter(LtValue v) {
    LtIterator it; memset(&it,0,sizeof(it)); it.source=v;
    if (v.tag == LT_HOST) v = lt_host_iter_values(v);
    if (v.tag==LT_LIST) { it.count=v.as.list->count; it.snapshot=it.count?lt_alloc(it.count*sizeof(*it.snapshot)):NULL; if(it.count)memcpy(it.snapshot,v.as.list->items,it.count*sizeof(*it.snapshot)); return it; }
    if (v.tag==LT_MAP) { it.count=v.as.map->count; it.snapshot=it.count?lt_alloc(it.count*sizeof(*it.snapshot)):NULL; for(size_t i=0;i<it.count;i++)it.snapshot[i]=lt_str(v.as.map->items[i].key); return it; }
    if (v.tag==LT_STR) { it.count=utf8_count(v.as.string); it.snapshot=it.count?lt_alloc(it.count*sizeof(*it.snapshot)):NULL; for(size_t i=0;i<it.count;i++)it.snapshot[i]=utf8_at(v.as.string,i); return it; }
    fail("RuntimeError", "cannot iterate %s", type_name(v)); return it;
}
int lt_iter_next(LtIterator *it,LtValue *out) { if(!it||!out) return 0; if(it->index>=it->count)return 0; *out=it->snapshot[it->index++]; return 1; }

static LtFunction *function_copy(const LtFunctionSpec *s, LtEnv *closure) {
    if (!s || !s->body || !s->name) fail("TypeError", "invalid function specification");
    if (s->param_count && !s->params) fail("TypeError", "function parameters must not be null");
    if (s->required_keyword_only_count && !s->required_keyword_only) fail("TypeError", "keyword-only parameters must not be null");
    if (s->required_count>s->positional_count || s->positional_count>s->param_count) fail("TypeError", "invalid function signature");
    LtFunction *f=lt_alloc(sizeof(*f)); memset(f,0,sizeof(*f)); f->spec=*s; f->name=lt_strdup(s->name); f->closure=closure;
    size_t n=s->param_count; if(n>SIZE_MAX/sizeof(*f->params))fail("OverflowError","too many parameters"); f->params=n?lt_alloc(n*sizeof(*f->params)):NULL;
    for(size_t i=0;i<n;i++) { if(!s->params[i]) fail("TypeError", "parameter name must not be null"); f->params[i]=lt_strdup(s->params[i]); }
    size_t kw=s->required_keyword_only_count; if(kw>SIZE_MAX/sizeof(*f->kwonly))fail("OverflowError","too many keyword-only parameters"); f->kwonly=kw?lt_alloc(kw*sizeof(*f->kwonly)):NULL;
    for(size_t i=0;i<kw;i++) { if(!s->required_keyword_only[i]) fail("TypeError", "keyword-only parameter name must not be null"); f->kwonly[i]=lt_strdup(s->required_keyword_only[i]); }
    f->rest=s->rest_name?lt_strdup(s->rest_name):NULL; f->extra=s->extra_name?lt_strdup(s->extra_name):NULL;
    f->spec.name=f->name; f->spec.source_path=s->source_path?lt_strdup(s->source_path):NULL; f->spec.params=(const char *const *)f->params; f->spec.required_keyword_only=(const char *const *)f->kwonly; f->spec.rest_name=f->rest; f->spec.extra_name=f->extra;
    return f;
}
LtValue lt_function(const LtFunctionSpec *s,LtEnv *closure) { LtValue v=value(LT_FUNCTION); v.as.function=function_copy(s,closure); return v; }
LtArg lt_pos(LtValue v){LtArg a={LT_ARG_POSITIONAL,NULL,v};return a;}
LtArg lt_named(const char*n,LtValue v){LtArg a={LT_ARG_NAMED,n,v};return a;}
LtArg lt_star(LtValue v){LtArg a={LT_ARG_STAR,NULL,v};return a;}
LtArg lt_starstar(LtValue v){LtArg a={LT_ARG_STARSTAR,NULL,v};return a;}
typedef struct { LtArgKind kind; const char *name; LtValue value; } FlatArg;
static void flat_add(FlatArg **a,size_t *n,size_t *cap,FlatArg x){if(*n==*cap){if(*cap>SIZE_MAX/2)fail("OverflowError","too many arguments");size_t c=*cap?*cap*2:8;if(c>SIZE_MAX/sizeof(**a))fail("OverflowError","too many arguments");*a=lt_realloc(*a,c*sizeof(**a));*cap=c;}(*a)[(*n)++]=x;}
static FlatArg *expand_args(const LtArg *args,size_t count,size_t *outn){if(count&&!args)fail("ArgumentError","argument array must not be null");FlatArg *a=NULL;size_t n=0,cap=0;for(size_t i=0;i<count;i++){
    if(args[i].kind==LT_ARG_STAR){if(args[i].value.tag!=LT_LIST)fail("ArgumentError","* unpacking requires a Latent list");for(size_t j=0;j<args[i].value.as.list->count;j++){FlatArg x={LT_ARG_POSITIONAL,NULL,args[i].value.as.list->items[j]};flat_add(&a,&n,&cap,x);}}
    else if(args[i].kind==LT_ARG_STARSTAR){if(args[i].value.tag!=LT_MAP)fail("ArgumentError","** unpacking requires a Latent map");for(size_t j=0;j<args[i].value.as.map->count;j++){FlatArg x={LT_ARG_NAMED,args[i].value.as.map->items[j].key,args[i].value.as.map->items[j].value};flat_add(&a,&n,&cap,x);}}
    else if(args[i].kind==LT_ARG_POSITIONAL||args[i].kind==LT_ARG_NAMED){FlatArg x={args[i].kind,args[i].name,args[i].value};flat_add(&a,&n,&cap,x);}
    else fail("ArgumentError","invalid argument kind");}
    *outn=n;return a;}
static size_t param_index(LtFunction*f,const char*n){for(size_t i=0;i<f->spec.param_count;i++)if(strcmp(f->params[i],n)==0)return i;return SIZE_MAX;}
static LtValue *bind_args(LtFunction*f,const LtArg*raw,size_t raw_n,size_t *bound_n){
    size_t n=0;FlatArg *a=expand_args(raw,raw_n,&n);size_t pc=f->spec.param_count, extra=(f->rest?1U:0U)+(f->extra?1U:0U), total=pc+extra;LtValue *vals=total?lt_alloc(total*sizeof(*vals)):NULL;unsigned char *sup=pc?lt_alloc(pc):NULL;for(size_t i=0;i<pc;i++){vals[i]=missing();sup[i]=0;}
    int named=0;for(size_t i=0;i<n;i++)if(a[i].kind==LT_ARG_NAMED)named=1;
    LtValue rest=list_value_empty(), extras=map_value_empty();
    if(!named){
        if(n>f->spec.positional_count&&!f->rest)fail("ArgumentError","%s() takes %zu args, got %zu",f->name,f->spec.positional_count,n);
        if(n<f->spec.required_count){if(f->spec.required_count==f->spec.positional_count)fail("ArgumentError","%s() takes %zu args, got %zu",f->name,f->spec.positional_count,n);fail("ArgumentError","%s() missing required argument '%s'",f->name,f->params[n]);}
        size_t used=n<f->spec.positional_count?n:f->spec.positional_count;for(size_t i=0;i<used;i++){vals[i]=a[i].value;sup[i]=1;}
        for(size_t i=0;i<f->spec.required_keyword_only_count;i++)fail("ArgumentError","%s() missing required keyword-only argument '%s'",f->name,f->kwonly[i]);
        if(f->rest)for(size_t i=f->spec.positional_count;i<n;i++)list_append(rest.as.list,a[i].value);
    }else{
        unsigned char *seen_named=pc?lt_alloc(pc):NULL; if(pc)memset(seen_named,0,pc);
        /* For unknown keyword duplicate detection, retain ordered spellings. */
        const char **seen=NULL;size_t sn=0,sc=0,pos=0;
        for(size_t i=0;i<n;i++){
            if(a[i].kind==LT_ARG_NAMED){const char *name=a[i].name?a[i].name:"";for(size_t j=0;j<sn;j++)if(strcmp(seen[j],name)==0)fail("ArgumentError","%s() got duplicate named argument '%s'",f->name,name);if(sn==sc){sc=sc?sc*2:8;seen=lt_realloc((void*)seen,sc*sizeof(*seen));}seen[sn++]=name;size_t ix=param_index(f,name);if(ix==SIZE_MAX){if(!f->extra)fail("ArgumentError","%s() got unexpected named argument '%s'",f->name,name);map_set(extras.as.map,name,a[i].value);}else{if(seen_named[ix])fail("ArgumentError","%s() got multiple values for argument '%s'",f->name,name);vals[ix]=a[i].value;sup[ix]=1;seen_named[ix]=1;}}
            else {if(pos>=f->spec.positional_count){if(!f->rest)fail("ArgumentError","%s() takes %zu args, got %zu",f->name,f->spec.positional_count,pos+1);list_append(rest.as.list,a[i].value);}else{if(sup[pos])fail("ArgumentError","%s() got multiple values for argument '%s'",f->name,f->params[pos]);vals[pos]=a[i].value;sup[pos]=1;}pos++;}
        }
    }
    for(size_t i=0;i<f->spec.required_count;i++)if(!sup[i])fail("ArgumentError","%s() missing required argument '%s'",f->name,f->params[i]);
    for(size_t i=0;i<f->spec.required_keyword_only_count;i++){size_t ix=param_index(f,f->kwonly[i]);if(ix==SIZE_MAX)fail("TypeError","invalid required keyword-only parameter");if(!sup[ix])fail("ArgumentError","%s() missing required keyword-only argument '%s'",f->name,f->kwonly[i]);}
    size_t k=pc;if(f->rest)vals[k++]=rest;if(f->extra)vals[k]=extras;*bound_n=total;return vals;
}
static int has_param(LtFunction*f,const char*n){return param_index(f,n)!=SIZE_MAX;}
static LtValue invoke_function(LtFunction*f,LtObject*receiver,const LtArg*args,size_t count){
    size_t n=0;LtValue *bound=bind_args(f,args,count,&n);size_t ec=f->spec.param_count+(f->rest?1:0)+(f->extra?1:0);size_t all=ec+(receiver&&!has_param(f,"self")?1:0);const char **names=all?lt_alloc(all*sizeof(*names)):NULL;
    for(size_t i=0;i<f->spec.param_count;i++) names[i]=f->params[i];
    size_t k=f->spec.param_count;if(f->rest)names[k++]=f->rest;if(f->extra)names[k++]=f->extra;if(receiver&&!has_param(f,"self"))names[k++]="self";
    LtEnv *env=lt_env_new(f->closure,names,all);for(size_t i=0;i<ec;i++)lt_env_set_local(env,names[i],bound[i]);if(receiver&&!has_param(f,"self"))lt_env_set_local(env,"self",(LtValue){.tag=LT_OBJECT,.as.object=receiver});
    const char *oldpath=lt_current_path,*oldfn=lt_current_function;int oldline=lt_current_line;
    if(f->spec.source_path)lt_set_location(f->spec.source_path,f->spec.source_line,f->name);
    LtValue result=f->spec.body(env,n,bound);
    lt_set_location(oldpath,oldline,oldfn);return result;
}
static LtFunction *find_method(LtClass*c,const char*n){for(;c;c=c->parent)for(size_t i=0;i<c->count;i++)if(strcmp(c->methods[i]->name,n)==0)return c->methods[i];return NULL;}
static LtValue bound_value(LtObject*o,LtFunction*f){LtValue v=value(LT_BOUND_METHOD);LtBound*b=lt_alloc(sizeof(*b));b->object=o;b->function=f;v.as.pointer=b;return v;}
LtValue lt_call(LtValue c,const LtArg*a,size_t n){
    if(c.tag==LT_FUNCTION)return invoke_function(c.as.function,NULL,a,n);
    if(c.tag==LT_BOUND_METHOD){LtBound*b=c.as.pointer;return invoke_function(b->function,b->object,a,n);}
    if(c.tag==LT_HOST)return lt_host_call_value(c,a,n);
    fail("RuntimeError","call on non-function value");return lt_nil();
}
LtValue lt_class(const char*n,LtValue p,const LtFunctionSpec*specs,size_t count){
    if(count>SIZE_MAX/sizeof(LtFunction *))fail("OverflowError","too many methods");
    if(!n)fail("TypeError","class name must not be null");
    if(count&&!specs)fail("TypeError","class methods must not be null");
    if(p.tag!=LT_NIL&&p.tag!=LT_CLASS)fail("TypeError","parent must be a Latent class");
    LtClass*c=lt_alloc(sizeof(*c));c->name=lt_strdup(n);c->parent=p.tag==LT_CLASS?p.as.klass:NULL;c->count=count;c->methods=count?lt_alloc(count*sizeof(*c->methods)):NULL;
    for(size_t i=0;i<count;i++){if(!specs[i].name)fail("TypeError","method name must not be null");if(strcmp(specs[i].name,"new")==0)fail("RuntimeError","method name 'new' is reserved");c->methods[i]=function_copy(&specs[i],NULL);for(size_t j=0;j<i;j++)if(strcmp(c->methods[j]->name,c->methods[i]->name)==0)fail("RuntimeError","duplicate method '%s'",specs[i].name);}
    LtValue v=value(LT_CLASS);v.as.klass=c;return v;
}
static LtValue empty_body(LtEnv *env,size_t n,const LtValue *args){(void)env;(void)n;(void)args;return lt_nil();}
static LtValue invoke_method(LtObject*o,LtClass*start,const char*n,const LtArg*a,size_t count){LtFunction*f=find_method(start,n);if(!f)fail("RuntimeError","no method '%s' on class %s",n,o->klass->name);return invoke_function(f,o,a,count);}
LtValue lt_wgetattr(LtValue o,const char*n){if(o.tag==LT_HOST)return lt_host_getattr(o,n);if(o.tag!=LT_OBJECT)fail("RuntimeError","attribute access on non-Latent instance");size_t i=map_find(o.as.object->fields,n);if(i!=SIZE_MAX)return o.as.object->fields->items[i].value;LtFunction*f=find_method(o.as.object->klass,n);if(!f)fail("RuntimeError","no field '%s'",n);return bound_value(o.as.object,f);}
LtValue lt_wsetattr(LtValue o,const char*n,LtValue v){if(!n)fail("TypeError","attribute name must not be null");if(o.tag==LT_HOST)return lt_host_setattr(o,n,v);if(o.tag!=LT_OBJECT)fail("RuntimeError","cannot set attribute on %s",type_name(o));map_set(o.as.object->fields,n,v);return lt_nil();}
LtValue lt_wcall(LtValue o,const char*n,const LtArg*a,size_t count){
    if(!n)fail("TypeError","method name must not be null");
    if(o.tag==LT_HOST)return lt_host_call_method(o,n,a,count);
    if(o.tag==LT_OBJECT)return invoke_method(o.as.object,o.as.object->klass,n,a,count);
    if(o.tag==LT_CLASS&&strcmp(n,"new")==0){LtClass*c=o.as.klass;LtObject*obj=lt_alloc(sizeof(*obj));obj->klass=c;obj->fields=lt_alloc(sizeof(*obj->fields));memset(obj->fields,0,sizeof(*obj->fields));LtValue v=value(LT_OBJECT);v.as.object=obj;LtFunction*init=find_method(c,"init");if(init)(void)invoke_function(init,obj,a,count);else if(count){LtFunctionSpec empty={lt_fmt("%s.new",c->name), empty_body, NULL, 0, 0, 0, NULL, 0, NULL, NULL, NULL, 0}; LtFunction *f=function_copy(&empty,NULL); size_t ignored=0; (void)bind_args(f,a,count,&ignored);}return v;}
    fail("RuntimeError","no method '%s' on %s",n,type_name(o));return lt_nil();
}
LtValue lt_super_call(LtValue receiver,LtValue owner,const char*method,const LtArg*a,size_t count){
    if(!method)fail("TypeError","method name must not be null");
    if(receiver.tag!=LT_OBJECT||owner.tag!=LT_CLASS) fail("RuntimeError","super call requires a Latent instance and class");
    LtClass*actual=receiver.as.object->klass,*own=owner.as.klass,*walk=actual;while(walk&&walk!=own)walk=walk->parent;if(!walk||!own->parent)fail("RuntimeError","super call owner is not in the instance inheritance chain");LtFunction*f=find_method(own->parent,method);if(!f)fail("RuntimeError","no parent method '%s' on class %s",method,own->name);return invoke_function(f,receiver.as.object,a,count);
}


/* ---------------- Python / Java host interop ---------------- */

typedef struct {
    pid_t pid;
    FILE *to_child;
    FILE *from_child;
    int active;
} LtHostChild;

typedef struct {
    uint64_t token;
    LtValue callable;
} LtHostCallback;

typedef struct LtHostCallbacks {
    LtHostCallback *items;
    size_t count;
    size_t capacity;
    char *last_error;
} LtHostCallbacks;

enum { LT_HOST_ENDPOINT_PYTHON = 1, LT_HOST_ENDPOINT_JAVA = 2 };
#define LT_HOST_MAX_SAFE_ID UINT64_C(9007199254740991)
#define LT_HOST_RPC_DEPTH_LIMIT 16U

static LtHostChild lt_python_child;
static LtHostChild lt_java_child;
static char *lt_runtime_dir;
static uint64_t lt_python_next_request = 1;
static uint64_t lt_python_request_stack[LT_HOST_RPC_DEPTH_LIMIT];
static size_t lt_python_request_depth;
static uint64_t lt_python_next_callback = 1;
static uint64_t lt_python_last_callback_frame;
static uint64_t lt_python_next_error = 1;
static int lt_java_compiled;

static char *host_join_path(const char *left, const char *right) {
    size_t a = strlen(left ? left : ""), b = strlen(right ? right : "");
    int slash = a && left[a - 1] != '/';
    char *result = lt_alloc(a + (size_t)slash + b + 1);
    memcpy(result, left ? left : "", a);
    if (slash) result[a++] = '/';
    memcpy(result + a, right ? right : "", b + 1);
    return result;
}

static char *host_absolute_path(const char *path) {
    if (path && path[0] == '/') return lt_strdup(path);
    char *cwd = getcwd(NULL, 0);
    if (!cwd) fail("RuntimeError", "cannot resolve executable directory: %s", strerror(errno));
    char *absolute = host_join_path(cwd, path ? path : "");
    free(cwd);
    return absolute;
}

static char *host_strndup(const char *text, size_t length) {
    char *copy = lt_alloc(length + 1);
    memcpy(copy, text, length);
    copy[length] = '\0';
    return copy;
}

static char *host_find_program(const char *argv0) {
    if (!argv0 || !*argv0) return host_absolute_path(".");
    if (strchr(argv0, '/')) return host_absolute_path(argv0);
    const char *path = getenv("PATH");
    if (!path) path = "/usr/bin:/bin";
    const char *part = path;
    while (1) {
        const char *end = strchr(part, ':');
        size_t n = end ? (size_t)(end - part) : strlen(part);
        char *dir = n ? host_strndup(part, n) : lt_strdup(".");
        char *candidate = host_join_path(dir, argv0);
        free(dir);
        if (access(candidate, X_OK) == 0) return host_absolute_path(candidate);
        free(candidate);
        if (!end) break;
        part = end + 1;
    }
    return host_absolute_path(argv0);
}

void lt_runtime_init(const char *argv0) {
    (void)signal(SIGPIPE, SIG_IGN);
    const char *configured = getenv("LATENT_RUNTIME_DIR");
    if (configured && *configured) {
        lt_runtime_dir = lt_strdup(configured);
        return;
    }
    char *program = host_find_program(argv0);
    char *slash = strrchr(program, '/');
    if (!slash) lt_runtime_dir = host_join_path(".", "runtime");
    else {
        if (slash == program) slash[1] = '\0';
        else *slash = '\0';
        lt_runtime_dir = host_join_path(program, "runtime");
    }
    free(program);
}

static char *host_runtime_file(const char *name) {
    if (!lt_runtime_dir) lt_runtime_init(NULL);
    return host_join_path(lt_runtime_dir, name);
}

static int host_wait_process(char *const argv[]) {
    pid_t pid = fork();
    if (pid < 0) return -1;
    if (pid == 0) {
        execvp(argv[0], argv);
        (void)fprintf(stderr, "Latent: cannot start %s: %s\n", argv[0], strerror(errno));
        _exit(127);
    }
    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno == EINTR) continue;
        return -1;
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return 1;
}

static int host_set_cloexec(int fd) {
    int flags = fcntl(fd, F_GETFD);
    return flags < 0 ? -1 : fcntl(fd, F_SETFD, flags | FD_CLOEXEC);
}

static int host_child_start(LtHostChild *child, char *const argv[]) {
    int to_pipe[2], from_pipe[2];
    if (pipe(to_pipe) != 0) return -1;
    if (pipe(from_pipe) != 0) {
        int saved = errno;
        close(to_pipe[0]); close(to_pipe[1]); errno = saved;
        return -1;
    }
    for (size_t i = 0; i < 2; i++) {
        if (host_set_cloexec(to_pipe[i]) != 0 || host_set_cloexec(from_pipe[i]) != 0) {
            int saved = errno;
            close(to_pipe[0]); close(to_pipe[1]);
            close(from_pipe[0]); close(from_pipe[1]); errno = saved;
            return -1;
        }
    }
    pid_t pid = fork();
    if (pid < 0) {
        int saved = errno;
        close(to_pipe[0]); close(to_pipe[1]);
        close(from_pipe[0]); close(from_pipe[1]); errno = saved;
        return -1;
    }
    if (pid == 0) {
        if (dup2(to_pipe[0], STDIN_FILENO) < 0 ||
                dup2(from_pipe[1], STDOUT_FILENO) < 0) _exit(126);
        close(to_pipe[0]); close(to_pipe[1]);
        close(from_pipe[0]); close(from_pipe[1]);
        execvp(argv[0], argv);
        (void)fprintf(stderr, "Latent: cannot start %s: %s\n", argv[0], strerror(errno));
        _exit(127);
    }
    close(to_pipe[0]); close(from_pipe[1]);
    FILE *to_child = fdopen(to_pipe[1], "w");
    FILE *from_child = fdopen(from_pipe[0], "r");
    if (!to_child || !from_child) {
        int saved = errno;
        if (to_child) fclose(to_child); else close(to_pipe[1]);
        if (from_child) fclose(from_child); else close(from_pipe[0]);
        (void)kill(pid, SIGTERM);
        while (waitpid(pid, NULL, 0) < 0 && errno == EINTR) {}
        errno = saved;
        return -1;
    }
    (void)setvbuf(to_child, NULL, _IOLBF, 0);
    child->pid = pid;
    child->to_child = to_child;
    child->from_child = from_child;
    child->active = 1;
    return 0;
}

static void host_child_stop(LtHostChild *child) {
    if (!child || !child->active) return;
    (void)kill(child->pid, SIGTERM);
    if (child->to_child) fclose(child->to_child);
    if (child->from_child) fclose(child->from_child);
    while (waitpid(child->pid, NULL, 0) < 0 && errno == EINTR) {}
    memset(child, 0, sizeof(*child));
}

void lt_runtime_shutdown(void) {
    host_child_stop(&lt_python_child);
    host_child_stop(&lt_java_child);
}

static void host_ensure_python(void) {
    if (lt_python_child.active) return;
    char *script = host_runtime_file("ltpy.py");
    if (access(script, R_OK) != 0)
        fail("RuntimeError", "ltpy.py not found in bundled runtime directory: %s", script);
    const char *python = getenv("LATENT_PYTHON");
    if (!python || !*python) python = "python3";
    char *const argv[] = {(char *)python, "-u", script, NULL};
    if (host_child_start(&lt_python_child, argv) != 0)
        fail("RuntimeError", "cannot start Python host interop daemon: %s", strerror(errno));
    free(script);
}

static char *host_java_classpath(void) {
    const char *extra = getenv("LATENT_JAVAD");
    const char *cwd = ".";
    size_t n = strlen(lt_runtime_dir ? lt_runtime_dir : "") + strlen(cwd) + 2;
    if (extra && *extra) n += strlen(extra) + 1;
    char *result = lt_alloc(n);
    if (extra && *extra)
        (void)snprintf(result, n, "%s:%s:%s", lt_runtime_dir, extra, cwd);
    else
        (void)snprintf(result, n, "%s:%s", lt_runtime_dir, cwd);
    return result;
}

static void host_ensure_java(void) {
    if (lt_java_child.active) return;
    if (!lt_runtime_dir) lt_runtime_init(NULL);
    char *lt_rt = host_runtime_file("LtRt.java");
    char *reflect = host_runtime_file("JReflect.java");
    char *daemon = host_runtime_file("LtJavaDaemon.java");
    if (access(lt_rt, R_OK) != 0 || access(reflect, R_OK) != 0 || access(daemon, R_OK) != 0)
        fail("RuntimeError", "Java interop sources are missing from the bundled runtime directory");
    char *classpath = host_java_classpath();
    if (!lt_java_compiled) {
        const char *javac = getenv("JAVAC");
        if (!javac || !*javac) javac = "javac";
        char *const compile_argv[] = {(char *)javac, "-encoding", "UTF-8", "-d",
            lt_runtime_dir, "-cp", classpath, lt_rt, reflect, daemon, NULL};
        int status = host_wait_process(compile_argv);
        if (status != 0)
            fail("RuntimeError", "cannot compile Java host interop runtime (javac is required; exit %d)", status);
        lt_java_compiled = 1;
    }
    const char *java = getenv("JAVA");
    if (!java || !*java) java = "java";
    char *const argv[] = {(char *)java, "-cp", classpath, "LtJavaDaemon", NULL};
    if (host_child_start(&lt_java_child, argv) != 0)
        fail("RuntimeError", "cannot start Java host interop daemon: %s", strerror(errno));
    free(classpath); free(lt_rt); free(reflect); free(daemon);
}

static void host_json_set(LtJson *object, const char *key, LtJson *item) {
    if (!object || !item || !lt_json_object_set(object, key, item)) {
        lt_json_free(item);
        fail("RuntimeError", "cannot construct host interop request");
    }
}

static void host_json_push(LtJson *array, LtJson *item) {
    if (!array || !item || !lt_json_array_push(array, item)) {
        lt_json_free(item);
        fail("RuntimeError", "cannot construct host interop request");
    }
}

static LtJson *host_json_i64(int64_t value) {
    char text[64];
    (void)snprintf(text, sizeof(text), "%" PRId64, value);
    LtJson *number = lt_json_number(text);
    if (!number) fail("RuntimeError", "cannot encode host interop integer");
    return number;
}

static LtJson *host_json_u64(uint64_t value) {
    char text[64];
    (void)snprintf(text, sizeof(text), "%" PRIu64, value);
    LtJson *number = lt_json_number(text);
    if (!number) fail("RuntimeError", "cannot encode host interop ID");
    return number;
}

static LtJson *host_json_text(const char *text) {
    LtJson *string = lt_json_string(text ? text : "");
    if (!string) fail("RuntimeError", "cannot encode host interop string");
    return string;
}

static const LtJson *host_json_field(const LtJson *object, const char *key) {
    const LtJson *field = lt_json_get(object, key);
    if (!field) fail("RuntimeError", "invalid host interop response: missing '%s'", key);
    return field;
}

static const char *host_json_string(const LtJson *object, const char *key) {
    const LtJson *field = host_json_field(object, key);
    const char *text = lt_json_as_string(field);
    if (!text) fail("RuntimeError", "invalid host interop response: '%s' is not a string", key);
    return text;
}

static int64_t host_json_int64(const LtJson *value, const char *label) {
    int64_t result;
    if (value && lt_json_as_int64(value, &result)) return result;
    double real;
    if (value && lt_json_as_double(value, &real) && isfinite(real) &&
            real >= -0x1p63 && real < 0x1p63 && trunc(real) == real)
        return (int64_t)real;
    fail("RuntimeError", "invalid host interop response: invalid %s", label);
    return 0;
}

static int host_json_is_string(const LtJson *value, const char *text) {
    const char *actual = lt_json_as_string(value);
    return actual && strcmp(actual, text) == 0;
}

static LtJson *host_target_json(const LtHostHandle *handle, int endpoint) {
    LtJson *target = lt_json_object();
    if (!target) fail("RuntimeError", "cannot construct host target");
    if (endpoint == LT_HOST_ENDPOINT_PYTHON) {
        if (handle->kind == LT_HOST_PY_MODULE) {
            host_json_set(target, "mod", host_json_text(handle->name));
        } else if (handle->kind == LT_HOST_PY_OBJECT) {
            host_json_set(target, "id", host_json_i64(handle->id));
        } else {
            lt_json_free(target);
            fail("TypeError", "Python callable handle is not a valid RPC target");
        }
    } else {
        if (handle->kind == LT_HOST_JAVA_CLASS) {
            host_json_set(target, "class", host_json_text(handle->name));
        } else if (handle->kind == LT_HOST_JAVA_OBJECT) {
            host_json_set(target, "id", host_json_i64(handle->id));
        } else {
            lt_json_free(target);
            fail("TypeError", "Java callable handle is not a valid RPC target");
        }
    }
    return target;
}

static void host_callbacks_add(LtHostCallbacks *callbacks, LtValue callable,
                               uint64_t *token_out) {
    if (!callbacks) fail("TypeError", "Latent callable values cannot be passed to Python/Java interop calls");
    if (lt_python_next_callback > LT_HOST_MAX_SAFE_ID)
        fail("OverflowError", "Python callback token limit exceeded");
    if (callbacks->count == callbacks->capacity) {
        size_t capacity = callbacks->capacity ? callbacks->capacity * 2 : 4;
        if (capacity > SIZE_MAX / sizeof(*callbacks->items))
            fail("OverflowError", "too many Python callbacks");
        callbacks->items = lt_realloc(callbacks->items, capacity * sizeof(*callbacks->items));
        callbacks->capacity = capacity;
    }
    uint64_t token = lt_python_next_callback++;
    callbacks->items[callbacks->count++] = (LtHostCallback){token, callable};
    *token_out = token;
}

static LtJson *host_encode_value(LtValue value, int endpoint,
                                 LtHostCallbacks *callbacks, int allow_callback) {
    switch (value.tag) {
        case LT_NIL: return lt_json_null();
        case LT_BOOL: return lt_json_bool(value.as.boolean);
        case LT_INT: return host_json_i64(value.as.integer);
        case LT_FLOAT:
            if (!isfinite(value.as.real)) {
                LtJson *envelope = lt_json_object();
                const char *name = isnan(value.as.real) ? "nan" :
                    (signbit(value.as.real) ? "-inf" : "inf");
                if (!envelope) fail("RuntimeError", "cannot encode host number");
                host_json_set(envelope, "__num", host_json_text(name));
                return envelope;
            } else {
                char text[64];
                (void)snprintf(text, sizeof(text), "%.17g", value.as.real);
                LtJson *number = lt_json_number(text);
                if (!number) fail("RuntimeError", "cannot encode host number");
                return number;
            }
        case LT_STR: return host_json_text(value.as.string);
        case LT_LIST: {
            LtJson *array = lt_json_array();
            if (!array) fail("RuntimeError", "cannot encode host list");
            for (size_t i = 0; i < value.as.list->count; i++)
                host_json_push(array, host_encode_value(value.as.list->items[i], endpoint,
                                                        callbacks, 0));
            return array;
        }
        case LT_MAP: {
            LtJson *envelope = lt_json_object();
            LtJson *entries = lt_json_array();
            if (!envelope || !entries) {
                lt_json_free(envelope); lt_json_free(entries);
                fail("RuntimeError", "cannot encode host map");
            }
            for (size_t i = 0; i < value.as.map->count; i++) {
                LtJson *pair = lt_json_array();
                if (!pair) { lt_json_free(envelope); lt_json_free(entries); fail("RuntimeError", "cannot encode host map"); }
                host_json_push(pair, host_json_text(value.as.map->items[i].key));
                host_json_push(pair, host_encode_value(value.as.map->items[i].value,
                                                       endpoint, callbacks, 0));
                host_json_push(entries, pair);
            }
            host_json_set(envelope, "__map", entries);
            return envelope;
        }
        case LT_HOST: {
            const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
            if (!handle) fail("TypeError", "invalid host handle");
            LtJson *envelope = lt_json_object();
            if (!envelope) fail("RuntimeError", "cannot encode host handle");
            if (endpoint == LT_HOST_ENDPOINT_PYTHON) {
                if (handle->kind == LT_HOST_PY_MODULE) {
                    host_json_set(envelope, "__mod", host_json_text(handle->name));
                } else if (handle->kind == LT_HOST_PY_OBJECT) {
                    host_json_set(envelope, "__ref", host_json_i64(handle->id));
                } else {
                    lt_json_free(envelope);
                    fail("TypeError", "Latent callable values cannot be passed to Python/Java interop calls");
                }
            } else {
                if (handle->kind == LT_HOST_JAVA_CLASS) {
                    host_json_set(envelope, "__jclass", host_json_text(handle->name));
                } else if (handle->kind == LT_HOST_JAVA_OBJECT) {
                    host_json_set(envelope, "__jref", host_json_i64(handle->id));
                } else {
                    lt_json_free(envelope);
                    fail("TypeError", "Latent callable values cannot be passed to Python/Java interop calls");
                }
            }
            return envelope;
        }
        case LT_FUNCTION: case LT_BOUND_METHOD: {
            uint64_t token;
            if (endpoint != LT_HOST_ENDPOINT_PYTHON || !allow_callback) {
                fail("TypeError", "Latent callable values cannot be passed to Python/Java interop calls");
            }
            host_callbacks_add(callbacks, value, &token);
            LtJson *descriptor = lt_json_object();
            if (!descriptor) fail("RuntimeError", "cannot encode Python callback");
            host_json_set(descriptor, "__lt_callback__", host_json_u64(token));
            return descriptor;
        }
        case LT_CLASS: case LT_OBJECT: case LT_MISSING:
            fail("TypeError", "Latent values cannot be passed to Python/Java interop calls");
    }
    fail("TypeError", "unsupported host interop value");
    return lt_json_null();
}

static LtValue host_new_handle(LtHostKind kind, int64_t id,
                               const char *name, const char *owner,
                               const char *member) {
    LtHostHandle *handle = lt_alloc(sizeof(*handle));
    handle->kind = kind;
    handle->id = id;
    handle->name = name ? lt_strdup(name) : NULL;
    handle->owner = owner ? lt_strdup(owner) : NULL;
    handle->member = member ? lt_strdup(member) : NULL;
    LtValue result = value(LT_HOST);
    result.as.pointer = handle;
    return result;
}

static int host_json_is_envelope(const LtJson *value, const char *key) {
    return value && lt_json_type(value) == LT_JSON_OBJECT && lt_json_size(value) == 1 &&
        lt_json_get(value, key) != NULL;
}

static LtValue host_decode_value(const LtJson *json, int endpoint, unsigned depth) {
    if (depth > 128) fail("RuntimeError", "host result nesting exceeds 128 levels");
    if (!json) fail("RuntimeError", "invalid host interop response value");
    switch (lt_json_type(json)) {
        case LT_JSON_NULL: return lt_nil();
        case LT_JSON_BOOL: {
            int boolean = 0;
            if (!lt_json_as_bool(json, &boolean)) fail("RuntimeError", "invalid host boolean");
            return lt_bool(boolean);
        }
        case LT_JSON_NUMBER: {
            int64_t integer;
            if (lt_json_as_int64(json, &integer)) return lt_int(integer);
            const char *text = lt_json_number_text(json);
            if (text && !strchr(text, '.') && !strchr(text, 'e') && !strchr(text, 'E'))
                fail("OverflowError", "int64 overflow");
            double real;
            if (!lt_json_as_double(json, &real) || !isfinite(real))
                fail("RuntimeError", "invalid host interop number");
            return lt_float(real);
        }
        case LT_JSON_STRING: {
            const char *text = lt_json_as_string(json);
            if (!text) fail("RuntimeError", "invalid host string");
            return lt_str(text);
        }
        case LT_JSON_ARRAY: {
            LtValue list = list_value_empty();
            size_t count = lt_json_size(json);
            for (size_t i = 0; i < count; i++)
                list_append(list.as.list, host_decode_value(lt_json_at(json, i), endpoint, depth + 1));
            return list;
        }
        case LT_JSON_OBJECT: {
            if (host_json_is_envelope(json, "__num")) {
                const char *number = lt_json_as_string(lt_json_get(json, "__num"));
                if (!number) fail("RuntimeError", "invalid host number envelope");
                if (strcmp(number, "nan") == 0) return lt_float(NAN);
                if (strcmp(number, "inf") == 0) return lt_float(INFINITY);
                if (strcmp(number, "-inf") == 0) return lt_float(-INFINITY);
                fail("RuntimeError", "invalid host number envelope");
            }
            if (host_json_is_envelope(json, "__map")) {
                const LtJson *entries = lt_json_get(json, "__map");
                if (lt_json_type(entries) != LT_JSON_ARRAY)
                    fail("RuntimeError", "invalid host map envelope");
                LtValue map = map_value_empty();
                for (size_t i = 0; i < lt_json_size(entries); i++) {
                    const LtJson *pair = lt_json_at(entries, i);
                    if (lt_json_type(pair) != LT_JSON_ARRAY || lt_json_size(pair) != 2)
                        fail("RuntimeError", "invalid host map entry");
                    const char *key = lt_json_as_string(lt_json_at(pair, 0));
                    if (!key) fail("RuntimeError", "host map key must be a string");
                    map_set(map.as.map, key,
                            host_decode_value(lt_json_at(pair, 1), endpoint, depth + 1));
                }
                return map;
            }
            if (endpoint == LT_HOST_ENDPOINT_PYTHON && host_json_is_envelope(json, "__ref")) {
                int64_t id = host_json_int64(lt_json_get(json, "__ref"), "Python object ref");
                if (id < 1 || (uint64_t)id > LT_HOST_MAX_SAFE_ID)
                    fail("RuntimeError", "invalid Python object ref");
                return host_new_handle(LT_HOST_PY_OBJECT, id, NULL, NULL, NULL);
            }
            if (endpoint == LT_HOST_ENDPOINT_JAVA && host_json_is_envelope(json, "__jref")) {
                int64_t id = host_json_int64(lt_json_get(json, "__jref"), "Java object ref");
                if (id < 1) fail("RuntimeError", "invalid Java object ref");
                return host_new_handle(LT_HOST_JAVA_OBJECT, id, NULL, NULL, NULL);
            }
            if (endpoint == LT_HOST_ENDPOINT_JAVA && host_json_is_envelope(json, "__jclass")) {
                const char *name = lt_json_as_string(lt_json_get(json, "__jclass"));
                if (!name) fail("RuntimeError", "invalid Java class handle");
                return host_new_handle(LT_HOST_JAVA_CLASS, 0, name, NULL, NULL);
            }
            fail("RuntimeError", "invalid host interop value envelope");
        }
    }
    fail("RuntimeError", "invalid host interop value");
    return lt_nil();
}

static int host_child_send_json(LtHostChild *child, const LtJson *value) {
    char *line = lt_json_stringify(value);
    if (!line) return -1;
    int ok = child && child->active && child->to_child &&
        fputs(line, child->to_child) >= 0 && fputc('\n', child->to_child) != EOF &&
        fflush(child->to_child) == 0;
    free(line);
    return ok ? 0 : -1;
}

static LtJson *host_child_read_json(LtHostChild *child, const char *name) {
    if (!child || !child->active || !child->from_child)
        fail("RuntimeError", "%s host interop daemon is not running", name);
    char *line = NULL;
    size_t capacity = 0;
    ssize_t length = getline(&line, &capacity, child->from_child);
    if (length < 0) {
        free(line);
        fail("RuntimeError", "%s host interop daemon died", name);
    }
    const char *error = NULL;
    LtJson *response = lt_json_parse(line, &error);
    free(line);
    if (!response)
        fail("RuntimeError", "invalid %s host interop JSON: %s",
             name, error ? error : "parse error");
    return response;
}

static LtJson *host_python_request_frame(LtJson *payload, uint64_t request_id,
                                         uint64_t parent_id) {
    LtJson *frame = lt_json_object();
    if (!frame) { lt_json_free(payload); fail("RuntimeError", "cannot construct Python RPC frame"); }
    host_json_set(frame, "kind", host_json_text("request"));
    host_json_set(frame, "id", host_json_u64(request_id));
    host_json_set(frame, "parent", host_json_u64(parent_id));
    host_json_set(frame, "payload", payload);
    return frame;
}

static LtHostCallback *host_find_callback(LtHostCallbacks *callbacks, uint64_t token) {
    if (!callbacks) return NULL;
    for (size_t i = 0; i < callbacks->count; i++)
        if (callbacks->items[i].token == token) return &callbacks->items[i];
    return NULL;
}

static void host_python_callback_result(LtHostCallbacks *callbacks,
                                        uint64_t request_id,
                                        uint64_t invocation_id,
                                        LtValue result,
                                        const char *error_message) {
    LtJson *frame = lt_json_object();
    if (!frame) fail("RuntimeError", "cannot construct Python callback response");
    host_json_set(frame, "kind", host_json_text("callback_result"));
    host_json_set(frame, "parent", host_json_u64(request_id));
    host_json_set(frame, "id", host_json_u64(invocation_id));
    if (error_message) {
        if (!callbacks) { lt_json_free(frame); fail("RuntimeError", "Python callback context expired"); }
        free(callbacks->last_error);
        callbacks->last_error = lt_strdup(error_message);
        if (lt_python_next_error > LT_HOST_MAX_SAFE_ID) {
            lt_json_free(frame); fail("OverflowError", "Python callback error ID limit exceeded");
        }
        host_json_set(frame, "error_id", host_json_u64(lt_python_next_error++));
        host_json_set(frame, "error_message", host_json_text(error_message));
    } else {
        LtHandler encode_handler;
        int encode_status = LT_TRY(encode_handler);
        if (encode_status == 0) {
            LtJson *encoded = host_encode_value(result, LT_HOST_ENDPOINT_PYTHON, NULL, 0);
            lt_handler_pop(&encode_handler);
            host_json_set(frame, "value", encoded);
        } else {
            char *message = lt_strdup(lt_handler_message(&encode_handler));
            lt_handler_pop(&encode_handler);
            lt_json_free(frame);
            host_python_callback_result(callbacks, request_id, invocation_id,
                                        lt_nil(), message);
            free(message);
            return;
        }
    }
    if (host_child_send_json(&lt_python_child, frame) != 0) {
        lt_json_free(frame);
        fail("RuntimeError", "Python callback response write failed");
    }
    lt_json_free(frame);
}

static void host_python_callback(LtJson *frame, LtHostCallbacks *callbacks,
                                 uint64_t request_id) {
    if (!callbacks || (lt_json_size(frame) != 6 && lt_json_size(frame) != 7))
        fail("RuntimeError", "unexpected Python callback frame");
    const LtJson *invocation_json = host_json_field(frame, "id");
    const LtJson *parent_json = host_json_field(frame, "parent");
    const LtJson *token_json = host_json_field(frame, "callback");
    uint64_t invocation = (uint64_t)host_json_int64(invocation_json, "callback invocation ID");
    int64_t parent = host_json_int64(parent_json, "callback parent ID");
    int64_t token_value = host_json_int64(token_json, "callback token");
    if (invocation == 0 || invocation > LT_HOST_MAX_SAFE_ID ||
            invocation <= lt_python_last_callback_frame || parent < 1 ||
            (uint64_t)parent != request_id || token_value < 1 ||
            (uint64_t)token_value > LT_HOST_MAX_SAFE_ID)
        fail("RuntimeError", "invalid Python callback frame IDs");
    LtHostCallback *entry = host_find_callback(callbacks, (uint64_t)token_value);
    if (!entry) fail("RuntimeError", "unknown Python callback token");
    lt_python_last_callback_frame = invocation;
    const LtJson *raw_args = host_json_field(frame, "args");
    if (lt_json_type(raw_args) != LT_JSON_ARRAY)
        fail("RuntimeError", "invalid Python callback args");
    const LtJson *raw_kwargs = lt_json_get(frame, "kwargs");
    if (raw_kwargs && lt_json_type(raw_kwargs) != LT_JSON_ARRAY)
        fail("RuntimeError", "invalid Python callback kwargs");
    size_t positional_count = lt_json_size(raw_args);
    size_t keyword_count = raw_kwargs ? lt_json_size(raw_kwargs) : 0;
    if (keyword_count > SIZE_MAX - positional_count ||
            positional_count + keyword_count > SIZE_MAX / sizeof(LtArg))
        fail("OverflowError", "too many Python callback arguments");
    size_t argument_count = positional_count + keyword_count;
    LtArg *arguments = argument_count ? lt_alloc(argument_count * sizeof(*arguments)) : NULL;
    for (size_t i = 0; i < positional_count; i++)
        arguments[i] = lt_pos(host_decode_value(lt_json_at(raw_args, i),
                                                LT_HOST_ENDPOINT_PYTHON, 0));
    for (size_t i = 0; i < keyword_count; i++) {
        const LtJson *pair = lt_json_at(raw_kwargs, i);
        if (lt_json_type(pair) != LT_JSON_ARRAY || lt_json_size(pair) != 2)
            fail("RuntimeError", "invalid Python callback keyword pair");
        const char *name = lt_json_as_string(lt_json_at(pair, 0));
        if (!name) fail("RuntimeError", "invalid Python callback keyword name");
        arguments[positional_count + i] = lt_named(name,
            host_decode_value(lt_json_at(pair, 1), LT_HOST_ENDPOINT_PYTHON, 0));
    }

    const char *old_path = lt_current_path;
    const char *old_function = lt_current_function;
    int old_line = lt_current_line;
    LtHandler callback_handler;
    LtValue result = lt_nil();
    char *callback_error = NULL;
    if (LT_TRY(callback_handler) == 0) {
        result = lt_call(entry->callable, arguments, argument_count);
        lt_handler_pop(&callback_handler);
    } else {
        callback_error = lt_strdup(lt_handler_message(&callback_handler));
        lt_handler_pop(&callback_handler);
    }
    lt_current_path = old_path;
    lt_current_function = old_function;
    lt_current_line = old_line;
    host_python_callback_result(callbacks, request_id, invocation, result, callback_error);
    free(callback_error);
    free(arguments);
}

static LtJson *host_python_exchange(LtJson *payload, LtHostCallbacks *callbacks) {
    LtHandler guard;
    size_t depth_before = lt_python_request_depth;
    int status = LT_TRY(guard);
    if (status != 0) {
        char *message = lt_strdup(lt_handler_message(&guard));
        const char *type = guard.error_type ? guard.error_type : "RuntimeError";
        lt_handler_pop(&guard);
        lt_python_request_depth = depth_before;
        throw_message(type, message);
    }
    host_ensure_python();
    if (lt_python_request_depth >= LT_HOST_RPC_DEPTH_LIMIT)
        fail("RuntimeError", "Python RPC depth limit exceeded");
    if (lt_python_next_request > LT_HOST_MAX_SAFE_ID)
        fail("OverflowError", "Python request ID limit exceeded");
    uint64_t request_id = lt_python_next_request++;
    uint64_t parent_id = lt_python_request_depth
        ? lt_python_request_stack[lt_python_request_depth - 1] : 0;
    lt_python_request_stack[lt_python_request_depth++] = request_id;
    LtJson *frame = host_python_request_frame(payload, request_id, parent_id);
    if (host_child_send_json(&lt_python_child, frame) != 0) {
        lt_json_free(frame);
        fail("RuntimeError", "Python host interop request write failed");
    }
    lt_json_free(frame);

    for (;;) {
        LtJson *response = host_child_read_json(&lt_python_child, "Python");
        const LtJson *kind = lt_json_get(response, "kind");
        if (host_json_is_string(kind, "callback")) {
            host_python_callback(response, callbacks, request_id);
            lt_json_free(response);
            continue;
        }
        if (!host_json_is_string(kind, "response") || lt_json_size(response) != 4) {
            lt_json_free(response);
            fail("RuntimeError", "invalid Python response frame");
        }
        int64_t response_id = host_json_int64(lt_json_get(response, "id"), "response ID");
        int64_t response_parent = host_json_int64(lt_json_get(response, "parent"), "response parent ID");
        if (response_id < 1 || (uint64_t)response_id != request_id ||
                response_parent < 0 || (uint64_t)response_parent != parent_id ||
                lt_json_type(lt_json_get(response, "payload")) != LT_JSON_OBJECT) {
            lt_json_free(response);
            fail("RuntimeError", "Python response ID/parent mismatch");
        }
        lt_python_request_depth = depth_before;
        lt_handler_pop(&guard);
        return response;
    }
}

static LtJson *host_java_exchange(LtJson *request) {
    host_ensure_java();
    if (host_child_send_json(&lt_java_child, request) != 0) {
        lt_json_free(request);
        fail("RuntimeError", "Java host interop request write failed");
    }
    lt_json_free(request);
    LtJson *response = host_child_read_json(&lt_java_child, "Java");
    if (lt_json_type(response) != LT_JSON_OBJECT)
        { lt_json_free(response); fail("RuntimeError", "invalid Java response"); }
    return response;
}

static void host_python_payload_error(const LtJson *payload,
                                      LtHostCallbacks *callbacks) {
    const LtJson *callback_error = lt_json_get(payload, "callback_error");
    if (callback_error) {
        const char *message = callbacks && callbacks->last_error
            ? callbacks->last_error : "Python host callback failed";
        fail("RuntimeError", "python error: %s", message);
    }
    const LtJson *error = lt_json_get(payload, "error");
    if (error) {
        const char *message = lt_json_as_string(error);
        if (!message) fail("RuntimeError", "invalid Python error response");
        const LtJson *kind = lt_json_get(payload, "error_kind");
        if (host_json_is_string(kind, "int64_overflow"))
            fail("OverflowError", "int64 overflow");
        fail("RuntimeError", "python error: %s", message);
    }
}

static LtValue host_python_response_value(LtJson *response,
                                          LtHostCallbacks *callbacks) {
    const LtJson *payload = host_json_field(response, "payload");
    host_python_payload_error(payload, callbacks);
    const LtJson *value = lt_json_get(payload, "value");
    if (!value) fail("RuntimeError", "invalid Python response payload");
    return host_decode_value(value, LT_HOST_ENDPOINT_PYTHON, 0);
}

static LtValue host_java_response_value(LtJson *response) {
    const LtJson *error = lt_json_get(response, "error");
    if (error) {
        const char *message = lt_json_as_string(error);
        if (!message) fail("RuntimeError", "invalid Java error response");
        fail("RuntimeError", "%s", message);
    }
    const LtJson *bound = lt_json_get(response, "bound_method");
    if (bound) {
        if (lt_json_type(bound) != LT_JSON_OBJECT)
            fail("RuntimeError", "invalid Java bound-method response");
        int64_t id = host_json_int64(lt_json_get(bound, "id"), "Java method receiver ID");
        const char *method = host_json_string(bound, "method");
        const char *type = host_json_string(bound, "type");
        return host_new_handle(LT_HOST_JAVA_BOUND_METHOD, id, NULL, type, method);
    }
    const LtJson *static_method = lt_json_get(response, "static_method");
    if (static_method) {
        if (lt_json_type(static_method) != LT_JSON_OBJECT)
            fail("RuntimeError", "invalid Java static-method response");
        int64_t id = host_json_int64(host_json_field(static_method, "id"), "Java static method ID");
        const char *method = host_json_string(static_method, "method");
        const char *type = host_json_string(static_method, "type");
        return host_new_handle(LT_HOST_JAVA_STATIC_METHOD, id, NULL, type, method);
    }
    const LtJson *value = lt_json_get(response, "value");
    if (!value) fail("RuntimeError", "invalid Java response payload");
    return host_decode_value(value, LT_HOST_ENDPOINT_JAVA, 0);
}

static void host_callbacks_clear(LtHostCallbacks *callbacks) {
    if (!callbacks) return;
    free(callbacks->items);
    free(callbacks->last_error);
    memset(callbacks, 0, sizeof(*callbacks));
}

static LtJson *host_python_payload(const char *op, const LtHostHandle *handle) {
    LtJson *request = lt_json_object();
    if (!request) fail("RuntimeError", "cannot construct Python request");
    host_json_set(request, "op", host_json_text(op));
    host_json_set(request, "target", host_target_json(handle, LT_HOST_ENDPOINT_PYTHON));
    return request;
}

static LtJson *host_java_payload(const char *op, const LtHostHandle *handle) {
    LtJson *request = lt_json_object();
    if (!request) fail("RuntimeError", "cannot construct Java request");
    host_json_set(request, "op", host_json_text(op));
    host_json_set(request, "target", host_target_json(handle, LT_HOST_ENDPOINT_JAVA));
    return request;
}

static void host_add_call_args(LtJson *request, const LtArg *args, size_t count,
                               int endpoint, LtHostCallbacks *callbacks) {
    LtJson *positional = lt_json_array();
    LtJson *keywords = lt_json_array();
    if (!positional || !keywords) {
        lt_json_free(positional); lt_json_free(keywords);
        fail("RuntimeError", "cannot construct host call arguments");
    }
    if (count && !args) {
        lt_json_free(positional); lt_json_free(keywords);
        fail("ArgumentError", "argument array must not be null");
    }
    for (size_t i = 0; i < count; i++) {
        if (args[i].kind == LT_ARG_STAR || args[i].kind == LT_ARG_STARSTAR) {
            lt_json_free(positional); lt_json_free(keywords);
            fail("ArgumentError", "argument unpacking is not supported for Python/Java interop calls");
        }
        if (args[i].kind == LT_ARG_POSITIONAL) {
            host_json_push(positional, host_encode_value(args[i].value, endpoint,
                                                         callbacks, 1));
        } else if (args[i].kind == LT_ARG_NAMED) {
            if (endpoint == LT_HOST_ENDPOINT_JAVA) {
                lt_json_free(positional); lt_json_free(keywords);
                fail("ArgumentError", "named arguments are not supported for Python/Java interop calls");
            }
            LtJson *pair = lt_json_array();
            if (!pair) {
                lt_json_free(positional); lt_json_free(keywords);
                fail("RuntimeError", "cannot construct Python keyword argument");
            }
            host_json_push(pair, host_json_text(args[i].name ? args[i].name : ""));
            host_json_push(pair, host_encode_value(args[i].value, endpoint,
                                                   callbacks, 1));
            host_json_push(keywords, pair);
        } else {
            lt_json_free(positional); lt_json_free(keywords);
            fail("ArgumentError", "invalid interop argument kind");
        }
    }
    host_json_set(request, "args", positional);
    if (endpoint == LT_HOST_ENDPOINT_PYTHON)
        host_json_set(request, "kwargs", keywords);
    else
        lt_json_free(keywords);
}

static LtValue host_python_request_value(LtJson *request,
                                         LtHostCallbacks *callbacks) {
    LtJson *response = host_python_exchange(request, callbacks);
    LtValue result = host_python_response_value(response, callbacks);
    lt_json_free(response);
    return result;
}

static LtValue host_java_request_value(LtJson *request) {
    LtJson *response = host_java_exchange(request);
    LtValue result = host_java_response_value(response);
    lt_json_free(response);
    return result;
}

static LtJson *host_python_call_request(const LtHostHandle *handle,
                                        const char *op, const char *attr,
                                        const LtArg *args, size_t count,
                                        LtHostCallbacks *callbacks) {
    LtJson *request = host_python_payload(op, handle);
    if (attr) host_json_set(request, "attr", host_json_text(attr));
    host_add_call_args(request, args, count, LT_HOST_ENDPOINT_PYTHON, callbacks);
    return request;
}

static LtValue host_python_call_method(const LtHostHandle *handle,
                                       const char *attr,
                                       const LtArg *args, size_t count) {
    LtHostCallbacks callbacks = {0};
    LtJson *request = host_python_call_request(handle, "call", attr, args,
                                               count, &callbacks);
    LtValue result = host_python_request_value(request, &callbacks);
    host_callbacks_clear(&callbacks);
    return result;
}

static LtJson *host_java_call_request(const LtHostHandle *handle,
                                      const char *attr, const LtArg *args,
                                      size_t count) {
    LtJson *request;
    if (handle->kind == LT_HOST_JAVA_CLASS && strcmp(attr, "new") == 0) {
        request = lt_json_object();
        if (!request) fail("RuntimeError", "cannot construct Java request");
        host_json_set(request, "op", host_json_text("new"));
        host_json_set(request, "class", host_json_text(handle->name));
    } else {
        request = host_java_payload("call", handle);
        host_json_set(request, "method", host_json_text(attr));
    }
    host_add_call_args(request, args, count, LT_HOST_ENDPOINT_JAVA, NULL);
    return request;
}

static LtValue host_python_get_value(const LtHostHandle *handle,
                                     const char *attr) {
    LtJson *request = host_python_payload("get", handle);
    host_json_set(request, "attr", host_json_text(attr));
    return host_python_request_value(request, NULL);
}

static LtValue host_host_descriptor(const char *key, const LtJson *descriptor,
                                    LtHostKind kind, const char *owner_key,
                                    const char *member_key) {
    if (!descriptor || lt_json_type(descriptor) != LT_JSON_OBJECT)
        fail("RuntimeError", "invalid host callable descriptor");
    int64_t id = host_json_int64(host_json_field(descriptor, "id"), "host callable ID");
    if (id < 1) fail("RuntimeError", "invalid host callable ID");
    const char *owner = owner_key ? host_json_string(descriptor, owner_key) : NULL;
    const char *member = host_json_string(descriptor, member_key);
    (void)key;
    return host_new_handle(kind, id, NULL, owner, member);
}

static LtValue host_python_getattr(LtValue value, const char *attr) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle || (handle->kind != LT_HOST_PY_MODULE && handle->kind != LT_HOST_PY_OBJECT))
        fail("RuntimeError", "attribute access on non-Python host value");
    if (handle->kind == LT_HOST_PY_MODULE) {
        LtJson *request = host_python_payload("get_module_function", handle);
        host_json_set(request, "attr", host_json_text(attr));
        LtJson *response = host_python_exchange(request, NULL);
        const LtJson *payload = host_json_field(response, "payload");
        host_python_payload_error(payload, NULL);
        const LtJson *function = lt_json_get(payload, "module_function");
        if (function && lt_json_type(function) == LT_JSON_OBJECT) {
            LtValue result = host_host_descriptor("module_function", function,
                LT_HOST_PY_MODULE_FUNCTION, "module", "name");
            lt_json_free(response);
            return result;
        }
        lt_json_free(response);
        return host_python_get_value(handle, attr);
    }
    LtJson *request = host_python_payload("get_bound_method", handle);
    host_json_set(request, "attr", host_json_text(attr));
    LtJson *response = host_python_exchange(request, NULL);
    const LtJson *payload = host_json_field(response, "payload");
    host_python_payload_error(payload, NULL);
    const LtJson *method = lt_json_get(payload, "bound_method");
    if (method && lt_json_type(method) == LT_JSON_OBJECT) {
        LtValue result = host_host_descriptor("bound_method", method,
            LT_HOST_PY_BOUND_METHOD, "type", "member");
        lt_json_free(response);
        return result;
    }
    lt_json_free(response);
    return host_python_get_value(handle, attr);
}

static LtValue host_java_getattr(LtValue value, const char *attr) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    LtJson *request = host_java_payload("get", handle);
    host_json_set(request, "field", host_json_text(attr));
    return host_java_request_value(request);
}

LtValue lt_pymod(LtValue name) {
    if (name.tag != LT_STR) fail("TypeError", "py module name must be a string");
    return host_new_handle(LT_HOST_PY_MODULE, 0, name.as.string, NULL, NULL);
}

LtValue lt_jclass(LtValue name) {
    if (name.tag != LT_STR) fail("TypeError", "java class name must be a string");
    return host_new_handle(LT_HOST_JAVA_CLASS, 0, name.as.string, NULL, NULL);
}

static LtValue lt_host_getattr(LtValue value, const char *attr) {
    if (!attr) fail("TypeError", "attribute name must not be null");
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle) fail("TypeError", "invalid host handle");
    if (handle->kind == LT_HOST_PY_MODULE || handle->kind == LT_HOST_PY_OBJECT)
        return host_python_getattr(value, attr);
    if (handle->kind == LT_HOST_JAVA_CLASS || handle->kind == LT_HOST_JAVA_OBJECT)
        return host_java_getattr(value, attr);
    fail("RuntimeError", "attribute access on %s", host_type_name(value));
    return lt_nil();
}

static LtValue lt_host_call_method(LtValue value, const char *name,
                                   const LtArg *args, size_t count) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle) fail("TypeError", "invalid host handle");
    if (handle->kind == LT_HOST_PY_MODULE || handle->kind == LT_HOST_PY_OBJECT)
        return host_python_call_method(handle, name, args, count);
    if (handle->kind == LT_HOST_JAVA_CLASS || handle->kind == LT_HOST_JAVA_OBJECT)
        return host_java_request_value(host_java_call_request(handle, name, args, count));
    fail("RuntimeError", "method call on %s", host_type_name(value));
    return lt_nil();
}

static LtValue lt_host_call_value(LtValue value, const LtArg *args, size_t count) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle) fail("TypeError", "invalid host callable");
    if (handle->kind == LT_HOST_PY_MODULE_FUNCTION ||
            handle->kind == LT_HOST_PY_BOUND_METHOD) {
        const char *op = handle->kind == LT_HOST_PY_MODULE_FUNCTION
            ? "call_module_function" : "call_bound_method";
        LtHostCallbacks callbacks = {0};
        LtJson *request = lt_json_object();
        if (!request) fail("RuntimeError", "cannot construct Python call request");
        host_json_set(request, "op", host_json_text(op));
        host_json_set(request, "id", host_json_i64(handle->id));
        host_add_call_args(request, args, count, LT_HOST_ENDPOINT_PYTHON, &callbacks);
        LtValue result = host_python_request_value(request, &callbacks);
        host_callbacks_clear(&callbacks);
        return result;
    }
    if (handle->kind == LT_HOST_JAVA_STATIC_METHOD) {
        LtJson *request = lt_json_object();
        if (!request) fail("RuntimeError", "cannot construct Java call request");
        host_json_set(request, "op", host_json_text("call_static_method"));
        host_json_set(request, "id", host_json_i64(handle->id));
        host_add_call_args(request, args, count, LT_HOST_ENDPOINT_JAVA, NULL);
        return host_java_request_value(request);
    }
    if (handle->kind == LT_HOST_JAVA_BOUND_METHOD) {
        LtHostHandle target = {LT_HOST_JAVA_OBJECT, handle->id, NULL, NULL, NULL};
        return host_java_request_value(host_java_call_request(&target,
            handle->member ? handle->member : "", args, count));
    }
    fail("RuntimeError", "call on non-function value");
    return lt_nil();
}

static LtValue lt_host_setattr(LtValue value, const char *name, LtValue assigned) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle || !name) fail("TypeError", "invalid host attribute assignment");
    if (handle->kind == LT_HOST_PY_MODULE)
        fail("RuntimeError", "cannot set attribute on a module");
    if (handle->kind == LT_HOST_PY_OBJECT) {
        LtJson *request = host_python_payload("setattr", handle);
        host_json_set(request, "attr", host_json_text(name));
        host_json_set(request, "value", host_encode_value(assigned,
            LT_HOST_ENDPOINT_PYTHON, NULL, 0));
        return host_python_request_value(request, NULL);
    }
    if (handle->kind == LT_HOST_JAVA_CLASS || handle->kind == LT_HOST_JAVA_OBJECT) {
        LtJson *request = host_java_payload("set", handle);
        host_json_set(request, "field", host_json_text(name));
        host_json_set(request, "value", host_encode_value(assigned,
            LT_HOST_ENDPOINT_JAVA, NULL, 0));
        return host_java_request_value(request);
    }
    fail("RuntimeError", "cannot set attribute on %s", host_type_name(value));
    return lt_nil();
}

static LtValue lt_host_index(LtValue value, LtValue key) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle) fail("TypeError", "invalid host handle");
    if (handle->kind == LT_HOST_PY_MODULE || handle->kind == LT_HOST_PY_OBJECT) {
        LtJson *request = host_python_payload("getitem", handle);
        host_json_set(request, "key", host_encode_value(key,
            LT_HOST_ENDPOINT_PYTHON, NULL, 0));
        return host_python_request_value(request, NULL);
    }
    if (handle->kind == LT_HOST_JAVA_OBJECT) {
        LtJson *request = host_java_payload("getitem", handle);
        host_json_set(request, "key", host_encode_value(key,
            LT_HOST_ENDPOINT_JAVA, NULL, 0));
        return host_java_request_value(request);
    }
    fail("RuntimeError", "cannot index %s", host_type_name(value));
    return lt_nil();
}

static LtValue lt_host_setindex(LtValue value, LtValue key, LtValue assigned) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle) fail("TypeError", "invalid host handle");
    if (handle->kind == LT_HOST_PY_MODULE || handle->kind == LT_HOST_PY_OBJECT) {
        LtJson *request = host_python_payload("setitem", handle);
        host_json_set(request, "key", host_encode_value(key,
            LT_HOST_ENDPOINT_PYTHON, NULL, 0));
        host_json_set(request, "value", host_encode_value(assigned,
            LT_HOST_ENDPOINT_PYTHON, NULL, 0));
        return host_python_request_value(request, NULL);
    }
    if (handle->kind == LT_HOST_JAVA_OBJECT) {
        LtJson *request = host_java_payload("setitem", handle);
        host_json_set(request, "key", host_encode_value(key,
            LT_HOST_ENDPOINT_JAVA, NULL, 0));
        host_json_set(request, "value", host_encode_value(assigned,
            LT_HOST_ENDPOINT_JAVA, NULL, 0));
        return host_java_request_value(request);
    }
    fail("RuntimeError", "cannot index-assign %s", host_type_name(value));
    return lt_nil();
}

static char *lt_host_repr(LtValue value) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle) return lt_strdup("<invalid host value>");
    if (handle->kind == LT_HOST_PY_MODULE_FUNCTION)
        return lt_fmt("<module function %s.%s>", handle->owner, handle->member);
    if (handle->kind == LT_HOST_PY_BOUND_METHOD)
        return lt_fmt("<bound method %s.%s>", handle->owner, handle->member);
    if (handle->kind == LT_HOST_JAVA_STATIC_METHOD)
        return lt_fmt("<static method %s.%s>", handle->owner, handle->member);
    if (handle->kind == LT_HOST_JAVA_BOUND_METHOD)
        return lt_fmt("<bound method %s.%s>", handle->owner, handle->member);
    LtJson *request = handle->kind == LT_HOST_PY_MODULE || handle->kind == LT_HOST_PY_OBJECT
        ? host_python_payload("repr", handle) : host_java_payload("repr", handle);
    LtValue result = handle->kind == LT_HOST_PY_MODULE || handle->kind == LT_HOST_PY_OBJECT
        ? host_python_request_value(request, NULL) : host_java_request_value(request);
    if (result.tag != LT_STR) fail("RuntimeError", "host repr() did not return a string");
    return lt_strdup(result.as.string);
}

static int lt_host_truthy(LtValue value) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle) return 0;
    if (handle->kind != LT_HOST_PY_MODULE && handle->kind != LT_HOST_PY_OBJECT)
        return 1;
    LtJson *request = host_python_payload("truthy", handle);
    LtValue result = host_python_request_value(request, NULL);
    if (result.tag != LT_BOOL) fail("RuntimeError", "Python truthy() did not return a boolean");
    return result.as.boolean;
}

static LtValue lt_host_len(LtValue value) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle) fail("TypeError", "invalid host handle");
    if (handle->kind == LT_HOST_PY_MODULE || handle->kind == LT_HOST_PY_OBJECT) {
        LtHostHandle builtins = {LT_HOST_PY_MODULE, 0, "builtins", NULL, NULL};
        LtArg arg = lt_pos(value);
        return host_python_call_method(&builtins, "len", &arg, 1);
    }
    if (handle->kind == LT_HOST_JAVA_OBJECT) {
        LtHostHandle size_target = *handle;
        LtJson *size_request = host_java_call_request(&size_target, "size", NULL, 0);
        LtJson *size_response = host_java_exchange(size_request);
        if (!lt_json_get(size_response, "error")) {
            LtValue result = host_java_response_value(size_response);
            lt_json_free(size_response);
            if (result.tag != LT_INT) fail("TypeError", "Java size() did not return an integer");
            return result;
        }
        lt_json_free(size_response);
        LtJson *list_request = host_java_payload("tolist", handle);
        LtValue items = host_java_request_value(list_request);
        if (items.tag != LT_LIST) fail("RuntimeError", "Java tolist() did not return a list");
        if (items.as.list->count > INT64_MAX) fail("OverflowError", "int64 overflow");
        return lt_int((int64_t)items.as.list->count);
    }
    fail("RuntimeError", "len() of %s", host_type_name(value));
    return lt_nil();
}

static LtValue lt_host_iter_values(LtValue value) {
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    if (!handle) fail("TypeError", "invalid host handle");
    if (handle->kind == LT_HOST_PY_MODULE || handle->kind == LT_HOST_PY_OBJECT) {
        LtHostHandle builtins = {LT_HOST_PY_MODULE, 0, "builtins", NULL, NULL};
        LtArg arg = lt_pos(value);
        return host_python_call_method(&builtins, "list", &arg, 1);
    }
    if (handle->kind == LT_HOST_JAVA_OBJECT) {
        LtJson *request = host_java_payload("tolist", handle);
        LtValue result = host_java_request_value(request);
        if (result.tag != LT_LIST) fail("RuntimeError", "Java tolist() did not return a list");
        return result;
    }
    fail("RuntimeError", "cannot iterate %s", host_type_name(value));
    return lt_nil();
}

static int lt_host_python_operand(LtValue value) {
    if (value.tag != LT_HOST || !value.as.pointer) return 0;
    const LtHostHandle *handle = (const LtHostHandle *)value.as.pointer;
    return handle->kind == LT_HOST_PY_MODULE || handle->kind == LT_HOST_PY_OBJECT;
}

static LtValue lt_host_binop(const char *name, const LtValue *args, size_t count) {
    LtJson *request = lt_json_object();
    LtJson *operands = lt_json_array();
    if (!request || !operands) {
        lt_json_free(request); lt_json_free(operands);
        fail("RuntimeError", "cannot construct Python operator request");
    }
    host_json_set(request, "op", host_json_text("binop"));
    host_json_set(request, "name", host_json_text(name));
    for (size_t i = 0; i < count; i++)
        host_json_push(operands, host_encode_value(args[i],
            LT_HOST_ENDPOINT_PYTHON, NULL, 0));
    host_json_set(request, "args", operands);
    return host_python_request_value(request, NULL);
}
