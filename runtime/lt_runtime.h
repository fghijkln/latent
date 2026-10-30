#ifndef LT_RUNTIME_H
#define LT_RUNTIME_H

/* Latent C11 后端运行时公开 ABI。仅依赖标准 C11；调用者负责保证名称字符串在进程期内有效。 */
#include <setjmp.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct LtEnv LtEnv;
typedef struct LtFunction LtFunction;
typedef struct LtClass LtClass;
typedef struct LtObject LtObject;
typedef struct LtHostHandle LtHostHandle;
typedef struct LtMap LtMap;
typedef struct LtList LtList;

typedef enum {
    LT_NIL, LT_BOOL, LT_INT, LT_FLOAT, LT_STR, LT_LIST, LT_MAP,
    LT_FUNCTION, LT_CLASS, LT_OBJECT, LT_HOST, LT_BOUND_METHOD, LT_MISSING
} LtTag;

typedef enum {
    LT_HOST_PY_MODULE, LT_HOST_PY_OBJECT, LT_HOST_PY_MODULE_FUNCTION,
    LT_HOST_PY_BOUND_METHOD, LT_HOST_JAVA_CLASS, LT_HOST_JAVA_OBJECT,
    LT_HOST_JAVA_STATIC_METHOD, LT_HOST_JAVA_BOUND_METHOD
} LtHostKind;

struct LtHostHandle {
    LtHostKind kind;
    int64_t id;
    const char *name;
    const char *owner;
    const char *member;
};

typedef struct LtValue {
    LtTag tag;
    union {
        int boolean;
        int64_t integer;
        double real;
        const char *string;
        LtList *list;
        LtMap *map;
        LtFunction *function;
        LtClass *klass;
        LtObject *object;
        void *pointer;
    } as;
} LtValue;

/* 基本值构造器；字符串和容器由 runtime 持有至进程退出。 */
LtValue lt_nil(void);
LtValue lt_bool(int value);
LtValue lt_int(int64_t value);
LtValue lt_float(double value);
LtValue lt_str(const char *value);
LtValue lt_list(const LtValue *values, size_t count);
LtValue lt_map(const char *const *keys, const LtValue *values, size_t count);
LtValue lt_pymod(LtValue name);
LtValue lt_jclass(LtValue name);
int lt_is_missing(LtValue value);

/* Locate bundled runtime sidecars relative to the generated executable. */
void lt_runtime_init(const char *argv0);
void lt_runtime_shutdown(void);

/* 词法环境：get 沿父链读取；set_local 只写当前帧已声明的槽；
 * set_enclosing 从父帧起写最近已声明槽。未知绑定以 Latent runtime error 失败。 */
LtEnv *lt_env_new(LtEnv *parent, const char *const *names, size_t count);
LtValue lt_env_get(LtEnv *env, const char *name);
void lt_env_set_local(LtEnv *env, const char *name, LtValue value);
void lt_env_set_enclosing(LtEnv *env, const char *name, LtValue value);

/* 函数 callback 接收调用帧、完整绑定向量（含 LT_MISSING/default 与收集器槽）及长度。
 * params 顺序为固定形参；required_count 是必需位置形参数量，positional_count 是位置槽数。
 * required_keyword_only 按名称列出必需仅限关键字形参。默认参数未提供时传 LT_MISSING，
 * 由生成代码在进入函数体时按顺序求值。rest_name/extra_name 可为 NULL。 */
typedef LtValue (*LtBody)(LtEnv *, size_t, const LtValue *);
typedef struct LtFunctionSpec {
    const char *name;
    LtBody body;
    const char *const *params;
    size_t param_count, required_count, positional_count;
    const char *const *required_keyword_only;
    size_t required_keyword_only_count;
    const char *rest_name, *extra_name, *source_path;
    int source_line;
} LtFunctionSpec;
LtValue lt_function(const LtFunctionSpec *spec, LtEnv *closure);

typedef enum { LT_ARG_POSITIONAL, LT_ARG_NAMED, LT_ARG_STAR, LT_ARG_STARSTAR } LtArgKind;
typedef struct { LtArgKind kind; const char *name; LtValue value; } LtArg;
/* STAR 仅接受 Latent list；STARSTAR 仅接受 string-keyed Latent map。 */
LtArg lt_pos(LtValue value);
LtArg lt_named(const char *name, LtValue value);
LtArg lt_star(LtValue value);
LtArg lt_starstar(LtValue value);
LtValue lt_call(LtValue callable, const LtArg *args, size_t count);

/* 类使用 LtFunctionSpec 描述方法；方法签名不含隐式 self，callback 的调用帧中
 * 可通过 lt_env_get(env,"self") 访问接收者。parent 必须为 nil 或 Latent class。 */
LtValue lt_class(const char *name, LtValue parent, const LtFunctionSpec *specs, size_t count);
LtValue lt_wgetattr(LtValue object, const char *name);
LtValue lt_wcall(LtValue object, const char *name, const LtArg *args, size_t count);
LtValue lt_wsetattr(LtValue object, const char *name, LtValue value);
LtValue lt_wsetindex(LtValue object, LtValue index, LtValue value);
LtValue lt_index(LtValue object, LtValue index);
LtValue lt_super_call(LtValue receiver, LtValue owner, const char *method,
                      const LtArg *args, size_t count);

/* 运算和内建语义。错误通过当前 LT_TRY handler 捕获；未捕获时报告位置并退出。 */
int lt_truthy(LtValue value);
LtValue lt_add(LtValue a, LtValue b);
LtValue lt_sub(LtValue a, LtValue b);
LtValue lt_mul(LtValue a, LtValue b);
LtValue lt_div(LtValue a, LtValue b);
LtValue lt_mod(LtValue a, LtValue b);
LtValue lt_pow(LtValue a, LtValue b);
LtValue lt_neg(LtValue value);
LtValue lt_eq(LtValue a, LtValue b);
LtValue lt_ne(LtValue a, LtValue b);
LtValue lt_lt(LtValue a, LtValue b);
LtValue lt_lte(LtValue a, LtValue b);
LtValue lt_gt(LtValue a, LtValue b);
LtValue lt_gte(LtValue a, LtValue b);
LtValue lt_say(LtValue value);
LtValue lt_len(LtValue value);
LtValue lt_range1(LtValue stop);
LtValue lt_range2(LtValue start, LtValue stop);
LtValue lt_str_of(LtValue value);
LtValue lt_to_int(LtValue value);
LtValue lt_push(LtValue list, LtValue value);
LtValue lt_keys(LtValue map);

/* Iterator 持有容器快照/游标；map 按插入顺序迭代 key，字符串逐 Unicode 码点迭代。 */
typedef struct LtIterator {
    LtValue source;
    size_t index;
    size_t count;
    LtValue *snapshot;
} LtIterator;
LtIterator lt_iter(LtValue value);
int lt_iter_next(LtIterator *iterator, LtValue *out);

/* 生成代码必须在自身仍活动的栈帧调用 LT_TRY；setjmp 不可封装在已返回的函数中。
 * 返回 0 为正常路径，非 0 为 throw 路径；两路均应调用 lt_handler_pop。
 * catch 消息为普通 Latent 字符串，runtime error 的分类另用于未捕获诊断。 */
typedef struct LtHandler {
    jmp_buf buf;
    struct LtHandler *previous;
    const char * volatile message;
    const char * volatile error_type;
} LtHandler;
void lt_handler_push(LtHandler *handler);
void lt_handler_pop(LtHandler *handler);
const char *lt_handler_message(const LtHandler *handler);
#define LT_TRY(handler) (lt_handler_push(&(handler)), setjmp((handler).buf))
void lt_throw(LtValue value);
void lt_report_uncaught(void);
void lt_set_location(const char *path, int line, const char *function);

#ifdef __cplusplus
}
#endif
#endif
