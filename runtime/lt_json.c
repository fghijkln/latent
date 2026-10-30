#include "lt_json.h"

#include <errno.h>
#include <float.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

#define LT_JSON_MAX_DEPTH 128U

typedef struct {
    char *data;
    size_t length;
    size_t capacity;
} LtJsonBuffer;

typedef struct {
    char *key;
    LtJson *value;
} LtJsonMember;

struct LtJson {
    LtJsonType type;
    union {
        int boolean;
        char *text;
        struct {
            LtJson **items;
            size_t count;
            size_t capacity;
        } array;
        struct {
            LtJsonMember *members;
            size_t count;
            size_t capacity;
        } object;
    } as;
};

static int buffer_reserve(LtJsonBuffer *buffer, size_t extra)
{
    size_t needed, capacity;
    char *grown;

    if (extra > SIZE_MAX - buffer->length - 1U) {
        return 0;
    }
    needed = buffer->length + extra + 1U;
    if (needed <= buffer->capacity) {
        return 1;
    }
    capacity = buffer->capacity == 0U ? 32U : buffer->capacity;
    while (capacity < needed) {
        if (capacity > SIZE_MAX / 2U) {
            capacity = needed;
            break;
        }
        capacity *= 2U;
    }
    grown = (char *)realloc(buffer->data, capacity);
    if (grown == NULL) {
        return 0;
    }
    buffer->data = grown;
    buffer->capacity = capacity;
    return 1;
}

static int buffer_append(LtJsonBuffer *buffer, const char *data, size_t length)
{
    if (!buffer_reserve(buffer, length)) {
        return 0;
    }
    if (length != 0U) {
        memcpy(buffer->data + buffer->length, data, length);
    }
    buffer->length += length;
    buffer->data[buffer->length] = '\0';
    return 1;
}

static int buffer_byte(LtJsonBuffer *buffer, char byte)
{
    return buffer_append(buffer, &byte, 1U);
}

/* Decode one Unicode scalar; return zero for malformed, overlong or surrogate UTF-8. */
static int utf8_decode(const unsigned char *text, size_t *length, unsigned long *codepoint)
{
    unsigned char a = text[0];
    unsigned char b, c, d;

    if (a <= 0x7fU) {
        *length = 1U;
        *codepoint = (unsigned long)a;
        return 1;
    }
    if (a >= 0xc2U && a <= 0xdfU) {
        b = text[1];
        if (b < 0x80U || b > 0xbfU) return 0;
        *length = 2U;
        *codepoint = ((unsigned long)(a & 0x1fU) << 6) | (unsigned long)(b & 0x3fU);
        return 1;
    }
    if (a >= 0xe0U && a <= 0xefU) {
        b = text[1];
        if (b == 0U) return 0;
        c = text[2];
        if (b < 0x80U || b > 0xbfU || c < 0x80U || c > 0xbfU) return 0;
        if ((a == 0xe0U && b < 0xa0U) || (a == 0xedU && b >= 0xa0U)) return 0;
        *length = 3U;
        *codepoint = ((unsigned long)(a & 0x0fU) << 12)
                   | ((unsigned long)(b & 0x3fU) << 6)
                   | (unsigned long)(c & 0x3fU);
        return 1;
    }
    if (a >= 0xf0U && a <= 0xf4U) {
        b = text[1];
        if (b == 0U) return 0;
        c = text[2];
        if (c == 0U) return 0;
        d = text[3];
        if (b < 0x80U || b > 0xbfU || c < 0x80U || c > 0xbfU
            || d < 0x80U || d > 0xbfU) return 0;
        if ((a == 0xf0U && b < 0x90U) || (a == 0xf4U && b > 0x8fU)) return 0;
        *length = 4U;
        *codepoint = ((unsigned long)(a & 0x07U) << 18)
                   | ((unsigned long)(b & 0x3fU) << 12)
                   | ((unsigned long)(c & 0x3fU) << 6)
                   | (unsigned long)(d & 0x3fU);
        return 1;
    }
    return 0;
}

static int valid_utf8_string(const char *text)
{
    const unsigned char *cursor;
    size_t length;
    unsigned long codepoint;

    if (text == NULL) return 0;
    cursor = (const unsigned char *)text;
    while (*cursor != '\0') {
        if (!utf8_decode(cursor, &length, &codepoint) || codepoint == 0UL) return 0;
        cursor += length;
    }
    return 1;
}

static char *copy_string(const char *text)
{
    size_t length = strlen(text);
    char *copy;

    if (length == SIZE_MAX) return NULL;
    copy = (char *)malloc(length + 1U);
    if (copy != NULL) memcpy(copy, text, length + 1U);
    return copy;
}

static LtJson *new_value(LtJsonType type)
{
    LtJson *value = (LtJson *)calloc(1U, sizeof(*value));
    if (value != NULL) value->type = type;
    return value;
}

LtJson *lt_json_null(void)
{
    return new_value(LT_JSON_NULL);
}

LtJson *lt_json_bool(int value)
{
    LtJson *result = new_value(LT_JSON_BOOL);
    if (result != NULL) result->as.boolean = value != 0;
    return result;
}

static int scan_number(const char *text, size_t *length)
{
    const char *cursor = text;

    if (cursor == NULL || *cursor == '\0') return 0;
    if (*cursor == '-') ++cursor;
    if (*cursor == '0') {
        ++cursor;
        if (*cursor >= '0' && *cursor <= '9') return 0;
    } else {
        if (*cursor < '1' || *cursor > '9') return 0;
        do { ++cursor; } while (*cursor >= '0' && *cursor <= '9');
    }
    if (*cursor == '.') {
        ++cursor;
        if (*cursor < '0' || *cursor > '9') return 0;
        do { ++cursor; } while (*cursor >= '0' && *cursor <= '9');
    }
    if (*cursor == 'e' || *cursor == 'E') {
        ++cursor;
        if (*cursor == '+' || *cursor == '-') ++cursor;
        if (*cursor < '0' || *cursor > '9') return 0;
        do { ++cursor; } while (*cursor >= '0' && *cursor <= '9');
    }
    if (*cursor != '\0') return 0;
    *length = (size_t)(cursor - text);
    return 1;
}

LtJson *lt_json_number(const char *raw_json_number)
{
    LtJson *result;
    size_t length;

    if (!scan_number(raw_json_number, &length)) return NULL;
    result = new_value(LT_JSON_NUMBER);
    if (result == NULL) return NULL;
    result->as.text = copy_string(raw_json_number);
    if (result->as.text == NULL) {
        free(result);
        return NULL;
    }
    return result;
}

LtJson *lt_json_string(const char *utf8)
{
    LtJson *result;

    if (!valid_utf8_string(utf8)) return NULL;
    result = new_value(LT_JSON_STRING);
    if (result == NULL) return NULL;
    result->as.text = copy_string(utf8);
    if (result->as.text == NULL) {
        free(result);
        return NULL;
    }
    return result;
}

LtJson *lt_json_array(void)
{
    return new_value(LT_JSON_ARRAY);
}

LtJson *lt_json_object(void)
{
    return new_value(LT_JSON_OBJECT);
}

static int contains_value(const LtJson *root, const LtJson *target, size_t depth)
{
    size_t i;

    if (root == target) return 1;
    if (root == NULL) return 0;
    if (depth > LT_JSON_MAX_DEPTH) return 1;
    if (root->type == LT_JSON_ARRAY) {
        for (i = 0U; i < root->as.array.count; ++i) {
            if (contains_value(root->as.array.items[i], target, depth + 1U)) return 1;
        }
    } else if (root->type == LT_JSON_OBJECT) {
        for (i = 0U; i < root->as.object.count; ++i) {
            if (contains_value(root->as.object.members[i].value, target, depth + 1U)) return 1;
        }
    }
    return 0;
}

static int reserve_array(LtJson *array, size_t needed)
{
    size_t capacity;
    LtJson **grown;

    if (needed <= array->as.array.capacity) return 1;
    if (needed > SIZE_MAX / sizeof(*grown)) return 0;
    capacity = array->as.array.capacity == 0U ? 4U : array->as.array.capacity;
    while (capacity < needed) {
        if (capacity > SIZE_MAX / 2U) {
            capacity = needed;
            break;
        }
        capacity *= 2U;
    }
    if (capacity > SIZE_MAX / sizeof(*grown)) return 0;
    grown = (LtJson **)realloc(array->as.array.items, capacity * sizeof(*grown));
    if (grown == NULL) return 0;
    array->as.array.items = grown;
    array->as.array.capacity = capacity;
    return 1;
}

int lt_json_array_push(LtJson *array, LtJson *item)
{
    if (array == NULL || array->type != LT_JSON_ARRAY || item == NULL
        || contains_value(item, array, 0U)
        || array->as.array.count == SIZE_MAX
        || !reserve_array(array, array->as.array.count + 1U)) return 0;
    array->as.array.items[array->as.array.count++] = item;
    return 1;
}

static int reserve_object(LtJson *object, size_t needed)
{
    size_t capacity;
    LtJsonMember *grown;

    if (needed <= object->as.object.capacity) return 1;
    if (needed > SIZE_MAX / sizeof(*grown)) return 0;
    capacity = object->as.object.capacity == 0U ? 4U : object->as.object.capacity;
    while (capacity < needed) {
        if (capacity > SIZE_MAX / 2U) {
            capacity = needed;
            break;
        }
        capacity *= 2U;
    }
    if (capacity > SIZE_MAX / sizeof(*grown)) return 0;
    grown = (LtJsonMember *)realloc(object->as.object.members, capacity * sizeof(*grown));
    if (grown == NULL) return 0;
    object->as.object.members = grown;
    object->as.object.capacity = capacity;
    return 1;
}

int lt_json_object_set(LtJson *object, const char *key, LtJson *value)
{
    size_t i;
    char *key_copy;

    if (object == NULL || object->type != LT_JSON_OBJECT || value == NULL
        || !valid_utf8_string(key) || contains_value(value, object, 0U)) return 0;
    for (i = 0U; i < object->as.object.count; ++i) {
        if (strcmp(object->as.object.members[i].key, key) == 0) {
            LtJson *old = object->as.object.members[i].value;
            object->as.object.members[i].value = value;
            if (old != value) lt_json_free(old);
            return 1;
        }
    }
    if (object->as.object.count == SIZE_MAX
        || !reserve_object(object, object->as.object.count + 1U)) return 0;
    key_copy = copy_string(key);
    if (key_copy == NULL) return 0;
    object->as.object.members[object->as.object.count].key = key_copy;
    object->as.object.members[object->as.object.count].value = value;
    ++object->as.object.count;
    return 1;
}

void lt_json_free(LtJson *value)
{
    size_t i;

    if (value == NULL) return;
    if (value->type == LT_JSON_NUMBER || value->type == LT_JSON_STRING) {
        free(value->as.text);
    } else if (value->type == LT_JSON_ARRAY) {
        for (i = 0U; i < value->as.array.count; ++i) lt_json_free(value->as.array.items[i]);
        free(value->as.array.items);
    } else if (value->type == LT_JSON_OBJECT) {
        for (i = 0U; i < value->as.object.count; ++i) {
            free(value->as.object.members[i].key);
            lt_json_free(value->as.object.members[i].value);
        }
        free(value->as.object.members);
    }
    free(value);
}

LtJsonType lt_json_type(const LtJson *value)
{
    return value == NULL ? LT_JSON_NULL : value->type;
}

size_t lt_json_size(const LtJson *value)
{
    if (value == NULL) return 0U;
    if (value->type == LT_JSON_ARRAY) return value->as.array.count;
    if (value->type == LT_JSON_OBJECT) return value->as.object.count;
    return 0U;
}

const LtJson *lt_json_at(const LtJson *array, size_t index)
{
    if (array == NULL || array->type != LT_JSON_ARRAY || index >= array->as.array.count) return NULL;
    return array->as.array.items[index];
}

const LtJson *lt_json_get(const LtJson *object, const char *key)
{
    size_t i;
    if (object == NULL || object->type != LT_JSON_OBJECT || key == NULL) return NULL;
    for (i = 0U; i < object->as.object.count; ++i) {
        if (strcmp(object->as.object.members[i].key, key) == 0) return object->as.object.members[i].value;
    }
    return NULL;
}

const char *lt_json_object_key(const LtJson *object, size_t index)
{
    if (object == NULL || object->type != LT_JSON_OBJECT || index >= object->as.object.count) return NULL;
    return object->as.object.members[index].key;
}

const LtJson *lt_json_object_value(const LtJson *object, size_t index)
{
    if (object == NULL || object->type != LT_JSON_OBJECT || index >= object->as.object.count) return NULL;
    return object->as.object.members[index].value;
}

const char *lt_json_as_string(const LtJson *value)
{
    return value != NULL && value->type == LT_JSON_STRING ? value->as.text : NULL;
}

const char *lt_json_number_text(const LtJson *value)
{
    return value != NULL && value->type == LT_JSON_NUMBER ? value->as.text : NULL;
}

int lt_json_as_bool(const LtJson *value, int *out)
{
    if (value == NULL || value->type != LT_JSON_BOOL || out == NULL) return 0;
    *out = value->as.boolean;
    return 1;
}

int lt_json_as_int64(const LtJson *value, int64_t *out)
{
    const char *text;
    size_t length, i;
    uint64_t magnitude = 0U;
    uint64_t limit;
    int negative = 0;

    if (value == NULL || value->type != LT_JSON_NUMBER || out == NULL) return 0;
    text = value->as.text;
    length = strlen(text);
    i = 0U;
    if (text[i] == '-') {
        negative = 1;
        ++i;
    }
    if (i == length) return 0;
    for (; i < length; ++i) {
        unsigned int digit;
        if (text[i] < '0' || text[i] > '9') return 0;
        digit = (unsigned int)(text[i] - '0');
        if (magnitude > (UINT64_MAX - (uint64_t)digit) / 10U) return 0;
        magnitude = magnitude * 10U + (uint64_t)digit;
    }
    limit = negative ? (uint64_t)INT64_MAX + 1U : (uint64_t)INT64_MAX;
    if (magnitude > limit) return 0;
    if (negative) {
        *out = magnitude == (uint64_t)INT64_MAX + 1U ? INT64_MIN : -(int64_t)magnitude;
    } else {
        *out = (int64_t)magnitude;
    }
    return 1;
}

int lt_json_as_double(const LtJson *value, double *out)
{
    char *end;
    double number;

    if (value == NULL || value->type != LT_JSON_NUMBER || out == NULL) return 0;
    errno = 0;
    number = strtod(value->as.text, &end);
    if (end == value->as.text || *end != '\0' || errno == ERANGE
        || number > DBL_MAX || number < -DBL_MAX) return 0;
    *out = number;
    return 1;
}

static int append_hex4(LtJsonBuffer *buffer, unsigned long codepoint)
{
    static const char hex[] = "0123456789abcdef";
    char escaped[6] = {'\\', 'u', '0', '0', '0', '0'};
    size_t i;

    for (i = 0U; i < 4U; ++i) {
        escaped[5U - i] = hex[codepoint & 0x0fUL];
        codepoint >>= 4;
    }
    return buffer_append(buffer, escaped, sizeof(escaped));
}

static int stringify_string(LtJsonBuffer *buffer, const char *text)
{
    const unsigned char *cursor = (const unsigned char *)text;
    size_t length;
    unsigned long codepoint;

    if (!buffer_byte(buffer, '"')) return 0;
    while (*cursor != '\0') {
        unsigned char byte = *cursor;
        const char *escape = NULL;
        if (byte == '"') escape = "\\\"";
        else if (byte == '\\') escape = "\\\\";
        else if (byte == '\b') escape = "\\b";
        else if (byte == '\f') escape = "\\f";
        else if (byte == '\n') escape = "\\n";
        else if (byte == '\r') escape = "\\r";
        else if (byte == '\t') escape = "\\t";
        if (escape != NULL) {
            if (!buffer_append(buffer, escape, 2U)) return 0;
            ++cursor;
        } else if (byte < 0x20U) {
            if (!append_hex4(buffer, (unsigned long)byte)) return 0;
            ++cursor;
        } else {
            if (!utf8_decode(cursor, &length, &codepoint)
                || !buffer_append(buffer, (const char *)cursor, length)) return 0;
            cursor += length;
        }
    }
    return buffer_byte(buffer, '"');
}

static int stringify_value(LtJsonBuffer *buffer, const LtJson *value, size_t depth)
{
    size_t i;

    if (value == NULL || depth > LT_JSON_MAX_DEPTH) return 0;
    switch (value->type) {
    case LT_JSON_NULL:
        return buffer_append(buffer, "null", 4U);
    case LT_JSON_BOOL:
        return value->as.boolean ? buffer_append(buffer, "true", 4U)
                                 : buffer_append(buffer, "false", 5U);
    case LT_JSON_NUMBER:
        return buffer_append(buffer, value->as.text, strlen(value->as.text));
    case LT_JSON_STRING:
        return stringify_string(buffer, value->as.text);
    case LT_JSON_ARRAY:
        if (!buffer_byte(buffer, '[')) return 0;
        for (i = 0U; i < value->as.array.count; ++i) {
            if ((i != 0U && !buffer_byte(buffer, ','))
                || !stringify_value(buffer, value->as.array.items[i], depth + 1U)) return 0;
        }
        return buffer_byte(buffer, ']');
    case LT_JSON_OBJECT:
        if (!buffer_byte(buffer, '{')) return 0;
        for (i = 0U; i < value->as.object.count; ++i) {
            if ((i != 0U && !buffer_byte(buffer, ','))
                || !stringify_string(buffer, value->as.object.members[i].key)
                || !buffer_byte(buffer, ':')
                || !stringify_value(buffer, value->as.object.members[i].value, depth + 1U)) return 0;
        }
        return buffer_byte(buffer, '}');
    }
    return 0;
}

char *lt_json_stringify(const LtJson *value)
{
    LtJsonBuffer buffer = {NULL, 0U, 0U};

    if (!stringify_value(&buffer, value, 0U)) {
        free(buffer.data);
        return NULL;
    }
    if (buffer.data == NULL) {
        buffer.data = (char *)malloc(1U);
        if (buffer.data == NULL) return NULL;
        buffer.data[0] = '\0';
    }
    return buffer.data;
}

typedef struct {
    const char *cursor;
} LtJsonParser;

static void skip_whitespace(LtJsonParser *parser)
{
    while (*parser->cursor == ' ' || *parser->cursor == '\t'
           || *parser->cursor == '\r' || *parser->cursor == '\n') ++parser->cursor;
}

static int hex_value(char byte)
{
    if (byte >= '0' && byte <= '9') return byte - '0';
    if (byte >= 'a' && byte <= 'f') return byte - 'a' + 10;
    if (byte >= 'A' && byte <= 'F') return byte - 'A' + 10;
    return -1;
}

static int read_hex4(LtJsonParser *parser, unsigned long *value)
{
    size_t i;
    unsigned long result = 0UL;
    for (i = 0U; i < 4U; ++i) {
        int digit = hex_value(parser->cursor[i]);
        if (digit < 0) return 0;
        result = (result << 4) | (unsigned long)digit;
    }
    parser->cursor += 4;
    *value = result;
    return 1;
}

static int append_codepoint(LtJsonBuffer *buffer, unsigned long codepoint)
{
    char bytes[4];
    size_t length;

    if (codepoint == 0UL || codepoint > 0x10ffffUL
        || (codepoint >= 0xd800UL && codepoint <= 0xdfffUL)) return 0;
    if (codepoint <= 0x7fUL) {
        bytes[0] = (char)codepoint;
        length = 1U;
    } else if (codepoint <= 0x7ffUL) {
        bytes[0] = (char)(0xc0U | (codepoint >> 6));
        bytes[1] = (char)(0x80U | (codepoint & 0x3fUL));
        length = 2U;
    } else if (codepoint <= 0xffffUL) {
        bytes[0] = (char)(0xe0U | (codepoint >> 12));
        bytes[1] = (char)(0x80U | ((codepoint >> 6) & 0x3fUL));
        bytes[2] = (char)(0x80U | (codepoint & 0x3fUL));
        length = 3U;
    } else {
        bytes[0] = (char)(0xf0U | (codepoint >> 18));
        bytes[1] = (char)(0x80U | ((codepoint >> 12) & 0x3fUL));
        bytes[2] = (char)(0x80U | ((codepoint >> 6) & 0x3fUL));
        bytes[3] = (char)(0x80U | (codepoint & 0x3fUL));
        length = 4U;
    }
    return buffer_append(buffer, bytes, length);
}

static char *parse_string(LtJsonParser *parser)
{
    LtJsonBuffer buffer = {NULL, 0U, 0U};
    if (*parser->cursor != '"') return NULL;
    ++parser->cursor;
    while (*parser->cursor != '\0' && *parser->cursor != '"') {
        unsigned char byte = (unsigned char)*parser->cursor;
        if (byte < 0x20U) goto error;
        if (byte == '\\') {
            unsigned long codepoint;
            ++parser->cursor;
            switch (*parser->cursor) {
            case '"': if (!buffer_byte(&buffer, '"')) goto error; ++parser->cursor; break;
            case '\\': if (!buffer_byte(&buffer, '\\')) goto error; ++parser->cursor; break;
            case '/': if (!buffer_byte(&buffer, '/')) goto error; ++parser->cursor; break;
            case 'b': if (!buffer_byte(&buffer, '\b')) goto error; ++parser->cursor; break;
            case 'f': if (!buffer_byte(&buffer, '\f')) goto error; ++parser->cursor; break;
            case 'n': if (!buffer_byte(&buffer, '\n')) goto error; ++parser->cursor; break;
            case 'r': if (!buffer_byte(&buffer, '\r')) goto error; ++parser->cursor; break;
            case 't': if (!buffer_byte(&buffer, '\t')) goto error; ++parser->cursor; break;
            case 'u':
                ++parser->cursor;
                if (!read_hex4(parser, &codepoint)) goto error;
                if (codepoint >= 0xd800UL && codepoint <= 0xdbffUL) {
                    unsigned long low;
                    if (parser->cursor[0] != '\\' || parser->cursor[1] != 'u') goto error;
                    parser->cursor += 2;
                    if (!read_hex4(parser, &low) || low < 0xdc00UL || low > 0xdfffUL) goto error;
                    codepoint = 0x10000UL + ((codepoint - 0xd800UL) << 10) + (low - 0xdc00UL);
                }
                if (!append_codepoint(&buffer, codepoint)) goto error;
                break;
            default: goto error;
            }
        } else {
            size_t length;
            unsigned long codepoint;
            if (!utf8_decode((const unsigned char *)parser->cursor, &length, &codepoint)
                || codepoint == 0UL || !buffer_append(&buffer, parser->cursor, length)) goto error;
            parser->cursor += length;
        }
    }
    if (*parser->cursor != '"') goto error;
    ++parser->cursor;
    if (buffer.data == NULL) {
        buffer.data = (char *)malloc(1U);
        if (buffer.data == NULL) return NULL;
        buffer.data[0] = '\0';
    }
    return buffer.data;
error:
    free(buffer.data);
    return NULL;
}

static LtJson *parse_value(LtJsonParser *parser, size_t depth);

static LtJson *parse_array(LtJsonParser *parser, size_t depth)
{
    LtJson *array = lt_json_array();
    if (array == NULL) return NULL;
    ++parser->cursor;
    skip_whitespace(parser);
    if (*parser->cursor == ']') {
        ++parser->cursor;
        return array;
    }
    for (;;) {
        LtJson *item = parse_value(parser, depth + 1U);
        if (item == NULL || !lt_json_array_push(array, item)) {
            lt_json_free(item);
            lt_json_free(array);
            return NULL;
        }
        skip_whitespace(parser);
        if (*parser->cursor == ']') {
            ++parser->cursor;
            return array;
        }
        if (*parser->cursor != ',') {
            lt_json_free(array);
            return NULL;
        }
        ++parser->cursor;
        skip_whitespace(parser);
    }
}

static LtJson *parse_object(LtJsonParser *parser, size_t depth)
{
    LtJson *object = lt_json_object();
    if (object == NULL) return NULL;
    ++parser->cursor;
    skip_whitespace(parser);
    if (*parser->cursor == '}') {
        ++parser->cursor;
        return object;
    }
    for (;;) {
        char *key;
        LtJson *value;
        size_t i;

        key = parse_string(parser);
        if (key == NULL) {
            lt_json_free(object);
            return NULL;
        }
        for (i = 0U; i < object->as.object.count; ++i) {
            if (strcmp(object->as.object.members[i].key, key) == 0) {
                free(key);
                lt_json_free(object);
                return NULL;
            }
        }
        skip_whitespace(parser);
        if (*parser->cursor != ':') {
            free(key);
            lt_json_free(object);
            return NULL;
        }
        ++parser->cursor;
        skip_whitespace(parser);
        value = parse_value(parser, depth + 1U);
        if (value == NULL || !lt_json_object_set(object, key, value)) {
            free(key);
            lt_json_free(value);
            lt_json_free(object);
            return NULL;
        }
        free(key);
        skip_whitespace(parser);
        if (*parser->cursor == '}') {
            ++parser->cursor;
            return object;
        }
        if (*parser->cursor != ',') {
            lt_json_free(object);
            return NULL;
        }
        ++parser->cursor;
        skip_whitespace(parser);
    }
}

static LtJson *parse_value(LtJsonParser *parser, size_t depth)
{
    LtJson *value;
    size_t number_length;

    if (depth > LT_JSON_MAX_DEPTH) return NULL;
    skip_whitespace(parser);
    if (*parser->cursor == 'n' && strncmp(parser->cursor, "null", 4U) == 0) {
        parser->cursor += 4;
        return lt_json_null();
    }
    if (*parser->cursor == 't' && strncmp(parser->cursor, "true", 4U) == 0) {
        parser->cursor += 4;
        return lt_json_bool(1);
    }
    if (*parser->cursor == 'f' && strncmp(parser->cursor, "false", 5U) == 0) {
        parser->cursor += 5;
        return lt_json_bool(0);
    }
    if (*parser->cursor == '"') {
        char *text = parse_string(parser);
        if (text == NULL) return NULL;
        value = new_value(LT_JSON_STRING);
        if (value == NULL) {
            free(text);
            return NULL;
        }
        value->as.text = text;
        return value;
    }
    if (*parser->cursor == '[') return parse_array(parser, depth);
    if (*parser->cursor == '{') return parse_object(parser, depth);
    if (*parser->cursor == '-' || (*parser->cursor >= '0' && *parser->cursor <= '9')) {
        const char *start = parser->cursor;
        while (*parser->cursor != '\0' && *parser->cursor != ',' && *parser->cursor != ']'
               && *parser->cursor != '}' && *parser->cursor != ' '
               && *parser->cursor != '\t' && *parser->cursor != '\r' && *parser->cursor != '\n') {
            ++parser->cursor;
        }
        number_length = (size_t)(parser->cursor - start);
        {
            char *raw = (char *)malloc(number_length + 1U);
            if (raw == NULL) return NULL;
            memcpy(raw, start, number_length);
            raw[number_length] = '\0';
            value = lt_json_number(raw);
            free(raw);
        }
        if (value == NULL) parser->cursor = start;
        return value;
    }
    return NULL;
}

LtJson *lt_json_parse(const char *text, const char **error)
{
    LtJsonParser parser;
    LtJson *result;

    if (error != NULL) *error = text;
    if (text == NULL) return NULL;
    parser.cursor = text;
    skip_whitespace(&parser);
    result = parse_value(&parser, 0U);
    if (result == NULL) {
        if (error != NULL) *error = parser.cursor;
        return NULL;
    }
    skip_whitespace(&parser);
    if (*parser.cursor != '\0') {
        if (error != NULL) *error = parser.cursor;
        lt_json_free(result);
        return NULL;
    }
    if (error != NULL) *error = NULL;
    return result;
}
