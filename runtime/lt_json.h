#ifndef LT_JSON_H
#define LT_JSON_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct LtJson LtJson;
typedef enum {
    LT_JSON_NULL,
    LT_JSON_BOOL,
    LT_JSON_NUMBER,
    LT_JSON_STRING,
    LT_JSON_ARRAY,
    LT_JSON_OBJECT
} LtJsonType;

LtJson *lt_json_null(void);
LtJson *lt_json_bool(int value);
LtJson *lt_json_number(const char *raw_json_number);
LtJson *lt_json_string(const char *utf8);
LtJson *lt_json_array(void);
LtJson *lt_json_object(void);
int lt_json_array_push(LtJson *array, LtJson *item);
int lt_json_object_set(LtJson *object, const char *key, LtJson *value);
LtJson *lt_json_parse(const char *text, const char **error);
char *lt_json_stringify(const LtJson *value);
void lt_json_free(LtJson *value);
LtJsonType lt_json_type(const LtJson *value);
size_t lt_json_size(const LtJson *value);
const LtJson *lt_json_at(const LtJson *array, size_t index);
const LtJson *lt_json_get(const LtJson *object, const char *key);
const char *lt_json_object_key(const LtJson *object, size_t index);
const LtJson *lt_json_object_value(const LtJson *object, size_t index);
const char *lt_json_as_string(const LtJson *value);
const char *lt_json_number_text(const LtJson *value);
int lt_json_as_bool(const LtJson *value, int *out);
int lt_json_as_int64(const LtJson *value, int64_t *out);
int lt_json_as_double(const LtJson *value, double *out);

#ifdef __cplusplus
}
#endif
#endif
