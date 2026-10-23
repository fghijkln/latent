#!/usr/bin/env python3
"""Latent dual-backend differential test: same .lt -> py & java must agree."""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from tests._safe_test_env import clean_test_environment, isolate_test_environment

TEST_ENV = isolate_test_environment()
LATENTC = os.path.join(ROOT, "latent.py")
TESTS = os.path.join(ROOT, "tests")
OUT = os.path.join(ROOT, "out")

POSITIVE = ["hello", "fib", "loop", "data", "truthy", "scope",
            "str_interp", "py_basic", "py_numpy", "py_lazy",
            "java_basic", "java_lazy", "mixed", "indexing",
            "class_basic", "inheritance", "setassign", "try_basic",
            "p5_values", "p5_closure_capture", "p5_shared_capture",
            "p5_recursion", "p5_shadowing", "p5_global",
            "p5_invalid_calls", "p6_nonlocal_nearest", "p6_nonlocal_skip",
            "p6_nonlocal_shared", "p6_nonlocal_returned",
            "p6_nonlocal_nested_scope", "p7_bound_methods"]
POSITIVE.append("p8_named_arguments")
POSITIVE.append("p9_default_parameters")
POSITIVE.append("p10_variadic_arguments")
POSITIVE.append("p11_keyword_only_parameters")
POSITIVE.append("p12_override_compatibility")
POSITIVE.append("phase1_semantics")
POSITIVE.append("p13_interop_bound_methods")
POSITIVE.append("p14_module_and_static_values")
POSITIVE.append("p14_static_method_extraction")
MODULE_POSITIVE = ["modules/app/main", "modules/same/main",
                   "modules/inheritance/main",
                   "modules/function_values/main", "modules/nonlocal/main",
                   "modules/bound_methods/main",
                   "modules/named_arguments/main",
                   "modules/default_arguments/main",
                   "modules/variadic/main", "modules/keyword_only/main"]
NEG_COMPILE = ["err_undef", "err_arity", "err_readbefore", "err_exprstmt",
               "err_break", "err_dupmethod", "err_assign_target",
               "err_nested_readbefore", "err_p6_nonlocal_missing",
               "err_p6_nonlocal_parameter", "err_p6_nonlocal_global",
               "err_p6_nonlocal_duplicate", "err_p6_nonlocal_top",
               "err_p6_nonlocal_class"]
NONLOCAL_COMPILE_MARKERS = {
    "err_p6_nonlocal_missing": "no binding for nonlocal 'absent'",
    "err_p6_nonlocal_parameter": "parameter 'value' cannot be nonlocal",
    "err_p6_nonlocal_global": "cannot be both global and nonlocal",
    "err_p6_nonlocal_duplicate": "duplicate name in nonlocal declaration",
    "err_p6_nonlocal_top": "nonlocal declaration is only valid inside a function",
    "err_p6_nonlocal_class": "only fn definitions allowed in class body",
}
P8_NEG_COMPILE = {
    "p8_negative/duplicate": "got duplicate named argument 'left'",
    "p8_negative/duplicate_position": "got multiple values for argument 'left'",
    "p8_negative/unknown": "got unexpected named argument 'other'",
    "p8_negative/missing": "missing required argument 'right'",
    "p8_negative/extra": "pair() takes 2 args, got 3",
    "p8_negative/bad_order": "positional argument follows named argument",
    "p8_negative/builtin": "built-in function 'len' does not accept named arguments",
    "p8_negative/method_unknown": "combine() got unexpected named argument 'other'",
    "p8_negative/method_missing": "combine() missing required argument 'right'",
    "p8_negative/constructor_unknown": "C.new() got unexpected named argument 'other'",
    "p8_negative/constructor_missing": "C.new() missing required argument 'right'",
    "p8_negative/super_unknown": "super.combine() got unexpected named argument 'other'",
    "modules/named_negative/main": "pair() got unexpected named argument 'other'",
    "inheritance_bad_init_arity": "Child.new() takes 1 args, got 0",
}
P9_NEG_COMPILE = {
    "p9_negative/static_missing": "pair() missing required argument 'left'",
    "p9_negative/static_duplicate": "pair() got multiple values for argument 'left'",
    "p9_negative/static_unknown": "pair() got unexpected named argument 'other'",
    "p9_negative/required_after_default": "required parameter follows defaulted parameter",
    "p9_negative/default_future_parameter": "cannot reference parameter 'later' before it is bound",
    "p9_negative/default_receiver": "receiver parameter cannot have a default",
}
P10_NEG_COMPILE = {
    "p10_negative/positional_after_variadic": "positional parameter follows variadic parameter",
    "p10_negative/rest_after_extra": "*rest must precede **extra and appear once",
    "p10_negative/duplicate_rest": "*rest must precede **extra and appear once",
    "p10_negative/duplicate_extra": "multiple **extra parameters",
    "p10_negative/keyword_only": "bare * must be followed by at least one keyword-only parameter",
    "p10_negative/unpack_after_named": "positional unpacking follows named argument",
    "p10_negative/builtin_star": "built-in function 'len' does not support argument unpacking",
    "p10_negative/builtin_starstar": "built-in function 'len' does not support argument unpacking",
}
P11_NEG_COMPILE = {
    "p11_negative/required_missing": "take() missing required keyword-only argument 'flag'",
    "p11_negative/position_does_not_bind": "take() missing required keyword-only argument 'flag'",
    "p11_negative/unknown_name": "take() got unexpected named argument 'other'",
    "p11_negative/duplicate_name": "take() got duplicate named argument 'flag'",
    "p11_negative/bare_star_empty": "bare * must be followed by at least one keyword-only parameter",
    "p11_negative/default_future": "cannot reference parameter 'later' before it is bound",
    "p11_negative/keyword_only_receiver": "receiver must be a required positional parameter",
    "p11_negative/positional_capacity": "take() takes 1 args, got 2",
    "p11_negative/collector_capacity": "collect() takes 0 args, got 1",
    "p11_negative/method_missing": "run() missing required keyword-only argument 'flag'",
    "p11_negative/constructor_missing": "C.new() missing required keyword-only argument 'flag'",
    "p11_negative/super_missing": "super.run() missing required keyword-only argument 'flag'",
    "p11_negative/duplicate_separator": "*rest must precede **extra and appear once",
    "p11_negative/after_extra": "positional parameter follows variadic parameter",
}
P12_NEG_COMPILE = {
    "inheritance_negative/override_arity": "fixed parameter 'value' is missing",
    "p12_negative/rename_extra": "is renamed to 'item'",
    "p12_negative/positional_to_keyword_only": "is keyword-only here",
    "p12_negative/positional_reorder": "must remain in slot 1",
    "p12_negative/new_required_positional": "required positional parameter 'audit' is new",
    "p12_negative/new_required_keyword_only": "required keyword-only parameter 'audit' is new",
    "p12_negative/optional_to_required": "optional parameter 'mode' became required",
    "p12_negative/missing_rest": "*rest collector required by Base.run is missing",
    "p12_negative/missing_extra": "**extra collector required by Base.run is missing",
    "p12_negative/missing_keyword_name_extra": "**extra cannot replace a base fixed parameter",
}
NEG_INHERITANCE_COMPILE = [
    "inheritance_negative/unknown", "inheritance_negative/not_class",
    "inheritance_negative/self_cycle", "inheritance_negative/cycle",
    "inheritance_negative/super_top", "inheritance_negative/super_no_parent",
    "inheritance_negative/super_missing_method",
    "inheritance_negative/super_wrong_receiver",
]
INHERITANCE_COMPILE_MARKERS = {
    "inheritance_negative/unknown": "unknown parent class",
    "inheritance_negative/not_class": "is not a Latent class",
    "inheritance_negative/self_cycle": "inheritance cycle",
    "inheritance_negative/cycle": "inheritance cycle",
    "inheritance_negative/super_top": "super is only valid",
    "inheritance_negative/super_no_parent": "has no parent for super",
    "inheritance_negative/super_missing_method": "has no method 'missing'",
    "inheritance_negative/super_wrong_receiver": "pass the current receiver first",
}
INHERITANCE_ERROR_LOCATIONS = {
    "inheritance_negative/unknown": ["unknown.lt:1:"],
    "inheritance_negative/not_class": ["not_class.lt:4:"],
    "inheritance_negative/self_cycle": ["self_cycle.lt:1:"],
    "inheritance_negative/cycle": ["cycle.lt:5:"],
    "inheritance_negative/super_top": ["super_top.lt:1:"],
    "inheritance_negative/super_no_parent": ["super_no_parent.lt:3:"],
    "inheritance_negative/super_missing_method": ["super_missing_method.lt:7:"],
    "inheritance_negative/super_wrong_receiver": ["super_wrong_receiver.lt:7:"],
}
NEG_MODULE_COMPILE = [
    "modules/negative/missing/main",
    "modules/negative/missing_export/main",
    "modules/negative/private/main",
    "modules/negative/late/main",
    "modules/negative/nested/main",
    "modules/negative/bad_extension/main",
    "modules/negative/readonly/main",
    "modules/negative/namespace_value/main",
    "modules/negative/cycle/main",
    "modules/negative/collision/main",
    "modules/negative/leak/main",
    "modules/negative/function_value/main",
    "modules/negative/inherit_private/main",
    "modules/negative/inherit_not_class/main",
]
MODULE_COMPILE_MARKERS = {
    "modules/negative/missing/main": "cannot read module",
    "modules/negative/missing_export/main": "no exported name 'no_such_name'",
    "modules/negative/private/main": "module member '_secret' is private",
    "modules/negative/late/main": "imports must precede module code",
    "modules/negative/nested/main": "imports must be top-level",
    "modules/negative/bad_extension/main": "must end in .lt",
    "modules/negative/readonly/main": "exports are read-only",
    "modules/negative/namespace_value/main": "undefined name 'lib'",
    "modules/negative/cycle/main": "module import cycle",
    "modules/negative/collision/main": "conflicts with a local binding",
    "modules/negative/leak/main": "undefined name 'value'",
    "modules/negative/function_value/main": "built-in functions are not first-class values",
    "modules/negative/inherit_private/main": "module member '_Hidden' is private",
    "modules/negative/inherit_not_class/main": "is not a Latent class",
}
MODULE_ERROR_LOCATIONS = {
    "modules/negative/missing/main": ["main.lt:1:"],
    "modules/negative/missing_export/main": ["main.lt:2:"],
    "modules/negative/private/main": ["main.lt:2:"],
    "modules/negative/late/main": ["main.lt:2:"],
    "modules/negative/nested/main": ["main.lt:2:"],
    "modules/negative/bad_extension/main": ["main.lt:1:"],
    "modules/negative/readonly/main": ["main.lt:2:"],
    "modules/negative/cycle/main": ["b.lt:1:"],
    "modules/negative/collision/main": ["main.lt:1:"],
    "modules/negative/function_value/main": ["main.lt:2:"],
    "modules/negative/inherit_private/main": ["main.lt:2:"],
    "modules/negative/inherit_not_class/main": ["main.lt:2:"],
}
NEG_RUNTIME = ["py_lazy_use", "java_lazy_use", "err_index_range",
               "err_index_key", "err_nomethod", "err_nofield",
               "err_location_top", "err_location_nested",
               "err_location_method", "err_location_loop",
               "err_location_throw", "err_location_try",
               "err_location_py_handle", "err_location_java_handle",
               "modules/runtime/main", "p7_bound_method_error",
               "p8_negative/indirect_duplicate",
               "p8_negative/indirect_duplicate_position",
               "p8_negative/indirect_unknown",
               "p8_negative/indirect_missing",
               "p8_negative/indirect_extra",
               "p8_negative/bound_unknown",
               "p8_negative/python_interop",
               "p8_negative/java_interop_method",
               "p8_negative/java_interop_constructor",
               "p9_negative/indirect_missing",
               "p9_negative/indirect_unknown",
               "p9_negative/indirect_duplicate",
               "p9_negative/bound_missing",
               "p10_negative/missing_expanded",
               "p10_negative/unknown_expanded",
               "p10_negative/duplicate_star_named",
               "p10_negative/duplicate_named_mapping",
               "p10_negative/duplicate_mappings",
               "p10_negative/too_many_expanded",
               "p10_negative/nonlist_star",
               "p10_negative/nonmap_starstar",
               "p10_negative/nonstring_key",
               "p10_negative/python_interop_star",
               "p10_negative/python_interop_starstar",
               "p10_negative/java_interop_star",
               "p10_negative/java_interop_starstar",
               "p11_runtime_negative/indirect_missing",
               "p11_runtime_negative/position_does_not_bind",
               "p11_runtime_negative/starstar_missing",
               "p11_runtime_negative/bound_missing",
               "p11_runtime_negative/constructor_missing",
               "p11_runtime_negative/duplicate_mapping",
               "p11_runtime_negative/unknown_mapping",
               "phase1_negative/ambiguous_method",
               "phase1_negative/ambiguous_constructor",
               "phase1_negative/ambiguous_parameters",
               "phase1_negative/ambiguous_numeric_types",
               "p13_negative/python_named", "p13_negative/python_star",
               "p13_negative/python_starstar", "p13_negative/java_named",
               "p13_negative/java_star", "p13_negative/java_starstar",
               "p13_negative/callback", "p13_negative/java_callback",
               "p13_negative/python_builtin",
               "p13_negative/java_constructor",
               "p14_negative/module_named", "p14_negative/module_star",
               "p14_negative/module_starstar",
               "p14_negative/module_builtin_len",
               "p14_negative/static_named", "p14_negative/static_star",
               "p14_negative/static_starstar",
               "p14_negative/static_callback",
               "p14_negative/module_callback",
               "p14_negative/module_wrapper_callback",
               "p14_negative/module_list_callback",
               "p14_negative/module_nested_callback",
               "p14_negative/static_wrapper_callback",
               "p14_negative/static_list_callback",
               "p14_negative/static_nested_callback",
               "p14_negative/module_bad_arity",
               "p14_negative/static_no_match",
               "p14_negative/module_callable_object",
               "p14_negative/module_dynamic_callable",
               "p14_negative/java_opaque_callable",
               "p14_negative/static_constructor_value",
               "p14_negative/static_collision_value"]
RUNTIME_SOURCE_LINES = {
    "py_lazy_use": [2], "java_lazy_use": [2],
    "err_index_range": [2], "err_index_key": [2],
    "err_nomethod": [6], "err_nofield": [6],
    "err_location_top": [2], "err_location_nested": [2, 4, 5],
    "err_location_method": [3, 5], "err_location_loop": [2],
    "err_location_throw": [1], "err_location_try": [4],
    "err_location_py_handle": [2], "err_location_java_handle": [2],
    "p7_bound_method_error": [4, 8],
    "p8_negative/indirect_duplicate": [5, 7],
    "p8_negative/indirect_duplicate_position": [5, 7],
    "p8_negative/indirect_unknown": [5, 7],
    "p8_negative/indirect_missing": [5, 7],
    "p8_negative/indirect_extra": [5, 7],
    "p8_negative/bound_unknown": [8, 10],
    "p8_negative/python_interop": [2],
    "p8_negative/java_interop_method": [2],
    "p8_negative/java_interop_constructor": [2],
    "p9_negative/indirect_missing": [4],
    "p9_negative/indirect_unknown": [4],
    "p9_negative/indirect_duplicate": [4],
    "p9_negative/bound_missing": [6],
    "p10_negative/missing_expanded": [4],
    "p10_negative/unknown_expanded": [4],
    "p10_negative/duplicate_star_named": [4],
    "p10_negative/duplicate_named_mapping": [4],
    "p10_negative/duplicate_mappings": [4],
    "p10_negative/too_many_expanded": [4],
    "p10_negative/nonlist_star": [4],
    "p10_negative/nonmap_starstar": [4],
    "p10_negative/nonstring_key": [6],
    "p10_negative/python_interop_star": [2],
    "p10_negative/python_interop_starstar": [2],
    "p10_negative/java_interop_star": [3],
    "p10_negative/java_interop_starstar": [3],
    "p11_runtime_negative/indirect_missing": [4],
    "p11_runtime_negative/position_does_not_bind": [4],
    "p11_runtime_negative/starstar_missing": [4, 5],
    "p11_runtime_negative/bound_missing": [5, 7],
    "p11_runtime_negative/constructor_missing": [5, 6],
    "p11_runtime_negative/duplicate_mapping": [4, 5],
    "p11_runtime_negative/unknown_mapping": [4, 5],
    "phase1_negative/ambiguous_method": [2],
    "phase1_negative/ambiguous_constructor": [2],
    "phase1_negative/ambiguous_parameters": [2],
    "phase1_negative/ambiguous_numeric_types": [2],
    "p13_negative/python_named": [4],
    "p13_negative/python_star": [5],
    "p13_negative/python_starstar": [5],
    "p13_negative/java_named": [4],
    "p13_negative/java_star": [5],
    "p13_negative/java_starstar": [5],
    "p13_negative/callback": [7],
    "p13_negative/java_callback": [7],
    "p13_negative/python_builtin": [3],
    "p13_negative/java_constructor": [2],
    "p14_negative/module_named": [3],
    "p14_negative/module_star": [4],
    "p14_negative/module_starstar": [4],
    "p14_negative/module_builtin_len": [3],
    "p14_negative/static_named": [3],
    "p14_negative/static_star": [4],
    "p14_negative/static_starstar": [4],
    "p14_negative/module_callback": [4],
    "p14_negative/module_wrapper_callback": [3],
    "p14_negative/module_list_callback": [3],
    "p14_negative/module_nested_callback": [3],
    "p14_negative/static_callback": [4],
    "p14_negative/static_wrapper_callback": [3],
    "p14_negative/static_list_callback": [3],
    "p14_negative/static_nested_callback": [3],
    "p14_negative/module_bad_arity": [3],
    "p14_negative/static_no_match": [3],
    "p14_negative/module_callable_object": [3],
    "p14_negative/module_dynamic_callable": [3],
    "p14_negative/java_opaque_callable": [3],
    "p14_negative/static_constructor_value": [2],
    "p14_negative/static_collision_value": [3],
}
RUNTIME_SOURCE_MARKERS = {
    "modules/runtime/main": ["main.lt:2", "lib.lt:3"],
}
RUNTIME_ERROR_MARKERS = {
    "p7_bound_method_error": "bound method failed",
}
P8_RUNTIME_ERROR_MARKERS = {
    "p8_negative/indirect_duplicate": "Latent runtime error: ArgumentError: pair() got duplicate named argument 'left'",
    "p8_negative/indirect_duplicate_position": "Latent runtime error: ArgumentError: pair() got multiple values for argument 'left'",
    "p8_negative/indirect_unknown": "Latent runtime error: ArgumentError: pair() got unexpected named argument 'other'",
    "p8_negative/indirect_missing": "Latent runtime error: ArgumentError: pair() missing required argument 'right'",
    "p8_negative/indirect_extra": "Latent runtime error: ArgumentError: pair() takes 2 args, got 3",
    "p8_negative/bound_unknown": "Latent runtime error: ArgumentError: combine() got unexpected named argument 'other'",
    "p8_negative/python_interop": "Latent runtime error: TypeError: math.sqrt() takes no keyword arguments",
    "p8_negative/java_interop_method": "Latent runtime error: ArgumentError: named arguments are not supported for Python/Java interop calls",
    "p8_negative/java_interop_constructor": "Latent runtime error: ArgumentError: named arguments are not supported for Python/Java interop calls",
}
P9_RUNTIME_ERROR_MARKERS = {
    "p9_negative/indirect_missing": "Latent runtime error: ArgumentError: pair() missing required argument 'left'",
    "p9_negative/indirect_unknown": "Latent runtime error: ArgumentError: pair() got unexpected named argument 'other'",
    "p9_negative/indirect_duplicate": "Latent runtime error: ArgumentError: pair() got multiple values for argument 'left'",
    "p9_negative/bound_missing": "Latent runtime error: ArgumentError: combine() missing required argument 'left'",
}
P10_RUNTIME_ERROR_MARKERS = {
    "p10_negative/missing_expanded": "Latent runtime error: ArgumentError: pair() takes 2 args, got 0",
    "p10_negative/unknown_expanded": "Latent runtime error: ArgumentError: pair() got unexpected named argument 'other'",
    "p10_negative/duplicate_star_named": "Latent runtime error: ArgumentError: pair() got multiple values for argument 'right'",
    "p10_negative/duplicate_named_mapping": "Latent runtime error: ArgumentError: pair() got duplicate named argument 'right'",
    "p10_negative/duplicate_mappings": "Latent runtime error: ArgumentError: collect() got duplicate named argument 'x'",
    "p10_negative/too_many_expanded": "Latent runtime error: ArgumentError: one() takes 1 args, got 2",
    "p10_negative/nonlist_star": "Latent runtime error: ArgumentError: * unpacking requires a Latent list",
    "p10_negative/nonmap_starstar": "Latent runtime error: ArgumentError: ** unpacking requires a Latent map",
    "p10_negative/nonstring_key": "Latent runtime error: ArgumentError: ** unpacking requires string keys",
    "p10_negative/python_interop_star": "Latent runtime error: ArgumentError: argument unpacking is not supported for Python/Java interop calls",
    "p10_negative/python_interop_starstar": "Latent runtime error: ArgumentError: argument unpacking is not supported for Python/Java interop calls",
    "p10_negative/java_interop_star": "Latent runtime error: ArgumentError: argument unpacking is not supported for Python/Java interop calls",
    "p10_negative/java_interop_starstar": "Latent runtime error: ArgumentError: argument unpacking is not supported for Python/Java interop calls",
}
P11_RUNTIME_ERROR_MARKERS = {
    "p11_runtime_negative/indirect_missing": "Latent runtime error: ArgumentError: required() missing required keyword-only argument 'flag'",
    "p11_runtime_negative/position_does_not_bind": "Latent runtime error: ArgumentError: required() missing required keyword-only argument 'flag'",
    "p11_runtime_negative/starstar_missing": "Latent runtime error: ArgumentError: required() missing required keyword-only argument 'flag'",
    "p11_runtime_negative/bound_missing": "Latent runtime error: ArgumentError: required() missing required keyword-only argument 'flag'",
    "p11_runtime_negative/constructor_missing": "Latent runtime error: ArgumentError: C.new() missing required keyword-only argument 'flag'",
    "p11_runtime_negative/duplicate_mapping": "Latent runtime error: ArgumentError: required() got duplicate named argument 'flag'",
    "p11_runtime_negative/unknown_mapping": "Latent runtime error: ArgumentError: required() got unexpected named argument 'other'",
}
PHASE1_RUNTIME_ERROR_MARKERS = {
    "phase1_negative/ambiguous_method": (
        "Latent runtime error: RuntimeException: java: ambiguous method "
        "java.nio.file.Paths.get(1 args): [java.nio.file.Paths#get("
        "java.lang.String,java.lang.String...), "
        "java.nio.file.Paths#get(java.net.URI)]"),
    "phase1_negative/ambiguous_constructor": (
        "Latent runtime error: RuntimeException: java: ambiguous constructor "
        "java.io.File(1 args): [java.io.File#<init>(java.lang.String), "
        "java.io.File#<init>(java.net.URI)]"),
    "phase1_negative/ambiguous_parameters": (
        "Latent runtime error: RuntimeException: java: ambiguous method "
        "Stage1Overloads.cross(2 args): [Stage1Overloads#cross("
        "java.lang.Number,java.lang.Object), Stage1Overloads#cross("
        "java.lang.Object,java.lang.Number)]"),
    "phase1_negative/ambiguous_numeric_types": (
        "Latent runtime error: RuntimeException: java: ambiguous method "
        "Stage1Overloads.sameGrade(1 args): [Stage1Overloads#sameGrade(int), "
        "Stage1Overloads#sameGrade(long)]"),
}
P14_RUNTIME_ERROR_MARKERS = {
    "p14_negative/module_named": "named arguments are not supported for Python/Java interop calls",
    "p14_negative/module_star": "argument unpacking is not supported for Python/Java interop calls",
    "p14_negative/module_starstar": "argument unpacking is not supported for Python/Java interop calls",
    "p14_negative/module_builtin_len": "call on non-function value",
    "p14_negative/static_named": "named arguments are not supported for Python/Java interop calls",
    "p14_negative/static_star": "argument unpacking is not supported for Python/Java interop calls",
    "p14_negative/static_starstar": "argument unpacking is not supported for Python/Java interop calls",
    "p14_negative/static_callback": "Latent callable values cannot be passed to Python/Java interop calls",
    "p14_negative/module_wrapper_callback": "Latent callable values cannot be passed to Python/Java interop calls",
    "p14_negative/module_list_callback": "Latent callable values cannot be passed to Python/Java interop calls",
    "p14_negative/module_nested_callback": "Latent callable values cannot be passed to Python/Java interop calls",
    "p14_negative/static_wrapper_callback": "Latent callable values cannot be passed to Python/Java interop calls",
    "p14_negative/static_list_callback": "Latent callable values cannot be passed to Python/Java interop calls",
    "p14_negative/static_nested_callback": "Latent callable values cannot be passed to Python/Java interop calls",
    "p14_negative/module_callable_object": "call on non-function value",
    "p14_negative/module_dynamic_callable": "call on non-function value",
    "p14_negative/java_opaque_callable": "call on non-function value",
    "p14_negative/static_collision_value": "call on non-function value",
}

COOKBOOK_DIR = os.path.join(ROOT, "cookbook")
COOKBOOK = ["py_math", "py_datetime", "py_json", "py_re", "py_os",
            "java_strings", "java_collections", "java_time", "java_nio",
            "java_bigdecimal", "mixed_io", "classes", "inheritance",
            "pipeline", "modules", "functions", "named_arguments"]
COOKBOOK.append("variadic_arguments")
COOKBOOK.append("keyword_only_arguments")
COOKBOOK.append("interop_bound_methods")
COOKBOOK.append("interop_function_values")

TRY_BASIC_STDOUT = ("caught: index out of range: 5\n"
                    "k=key not found: zz\n"
                    "e=negative!\n"
                    "no error\n"
                    "42\n"
                    "5\n"
                    "-1\n"
                    "n=outer: inner\n"
                    "javaerr\n"
                    "pyerr\n")
MODULE_APP_STDOUT = "factor init\nshared init\n42\n2\n9\n"
MODULE_SAME_STDOUT = "33\nleft\nright\nleft\nright\n"
INHERITANCE_STDOUT = ("root-init\nroot-init\nvalue:inherited\n"
                      "root-middle-leaf\nvalue:shared:field\nisolated\n"
                      "empty\ntrue\nfalse\n<Leaf object>\n")
MODULE_INHERITANCE_STDOUT = "base:module+derived\nright\n<class Derived>\n"
COOKBOOK_INHERITANCE_STDOUT = "animal:Milo (shiba)\n<Dog object>\n"
P5_EXPECTED_STDOUT = {
    "p5_values": "<function add>\n5\n9\n9\n",
    "p5_closure_capture": "2\n",
    "p5_shared_capture": "27\n",
    "p5_recursion": "true\nfalse\n",
    "p5_shadowing": "8\n",
    "p5_global": "100004\n4\n",
    "p5_invalid_calls": ("one() takes 1 args, got 0\n"
                         "one() takes 1 args, got 2\n"
                         "call on non-function value\n"),
    "modules/function_values/main": "6\n15\n100008\n8\n",
}
P6_EXPECTED_STDOUT = {
    "p6_nonlocal_nearest": "11\n1\n",
    "p6_nonlocal_skip": "41\n42\n",
    "p6_nonlocal_shared": "102\n122\n124\n",
    "p6_nonlocal_returned": "7\n11\n",
    "p6_nonlocal_nested_scope": "[90, 3, 3]\n",
    "modules/nonlocal/main": "41\n42\n100\n",
}
P7_EXPECTED_STDOUT = {
    "p7_bound_methods": (
        "<bound method Child.bump>\ntrue\nfalse\n12\n15\n16\n20\n"
        "<bound method Child.kind>\nchild\nshadowed field\nchild\n"
        "bump() takes 1 args, got 0\n"
        "bump() takes 1 args, got 2\n101\n20\n"),
    "modules/bound_methods/main": (
        "<bound method Counter.advance>\n42\n"),
}
P8_EXPECTED_STDOUT = {
    "p8_named_arguments": "123\n123\n15\n123\nACB\n12\n34\n78\n122\n56\n9\n10\n11\n",
    "modules/named_arguments/main": "12\n34\n",
}
P9_EXPECTED_STDOUT = {
    "p9_default_parameters": (
        "6\n12\n15\n8\n6\n12\n15\n6\n12\n1\n2\n9\n2\n"
        "nil\n2\nBa\n2\nab\n2\nXY\n10\n11\n12\n5\n24\n22\n25\n29\n30\n10\n11\n"),
    "modules/default_arguments/main": "12\n5\n6\n15\n10\n",
}
P10_EXPECTED_STDOUT = {
    "p10_variadic_arguments": (
        '["default", 2, [], {}]\n'
        '["mixed", 3, [4, 5], {"label": "x", "mode": "fast"}]\n'
        '["expanded", 4, [6], {"note": "ok"}]\n'
        '["empty", 2, [], {}]\n'
        '["named", 5, [], {"tag": "mapping"}]\n'
        '["many", 7, [8], {"first": 1, "second": 2}]\n'
        '[10, "closure", [11], {"flag": true}]\n'
        '[1, [2, 3], {"name": 4, "x": 5}]\n'
        'A12SNVM\n'
        '["new", [1], {"mode": "constructor"}]\n'
        '["child", [5], {"inherited": true}]\n'
        '["inherited-method", [6], {"key": "value"}]\n'
        '["bound", [7], {"bound-key": 8}]\n'
        '["super", [9], {"via": "parent"}]\n'),
    "modules/variadic/main": (
        '["module", 2, [], {}]\n'
        '["expanded", 3, [4], {"mode": "module"}]\n'
        '["value", 5, [], {"extra": "function-value"}]\n'),
}
P11_EXPECTED_STDOUT = {
    "p11_keyword_only_parameters": (
        '["ops", "copy", ["archive"], true, "safe", false, {"trace": 7}]\n'
        '["api", nil, [], false, "fast", true, {"source": "mapping"}]\n'
        '[1, "yes", 2]\n[1, 2, 4, 3]\nARPK\n'
        '["captured-closure", true]\n'
        '["wolf", ["Nova", "dog", "direct", "child", false]]\n'
        '["wolf", ["Nova", "dog", "dynamic", "child", true]]\n'
        '["wolf", ["Nova", "dog", "bound", "child", true]]\n'
        '["old", "inherited"]\n'
        '["old", "base", "parent", "base", true]\n'),
    "modules/keyword_only/main": (
        '["module", "module-default", 3]\n'
        '["expanded", "fast", 5]\n'
        '["function-value", "slow", true]\n'
        '["worker", "Kira", "module-default", 9]\n'),
}
P12_EXPECTED_STDOUT = {
    "p12_override_compatibility": (
        "base:dynamic:child:leaf\n"
        '["L", "child", "R", false]\n'
        '["A", "M", "R", true]\n'
        "fast\nexplicit\n"
        '["x", [1, 2], {"color": "blue"}, true]\n'
        '["x", [], {}, false]\n'
        "N:custom\n"),
}
P13_EXPECTED_STDOUT = (
    "<bound method p13_host.Sample.push>\n1\n2\n3\n"
    "<bound method collections.deque.append>\nnil\n"
    "<bound method P13InteropFixture.append>\n"
    "one:direct:1\none:java-1:2\ntwo:0:java-2:3\none:java-3:4\n"
    "field\nmethod\nfield\n" + "call on non-function value\n" * 5)
P14_EXPECTED_STDOUT = (
    "<module function p14_host.scale>\n8\n10\n12\n"
    "<module function p14_host.scale>\n18\n"
    "<module function math.sqrt>\n5\n"
    "<static method P14InteropFixture.classify>\n"
    "text:hello\nnumber:7.0\ntext:escaped\ntext:direct\n"
    "<static method P14InteropChild.inherited>\n"
    "inherited:parent\nfield\ncollision:direct\n" +
    "call on non-function value\n" * 4)
PHASE1_EXPECTED_STDOUT = (
    "true\nnan\nnan is truthy\n"
    "false\ntrue\nfalse\ntrue\nfalse\ntrue\n"
    "false\nfalse\nfalse\nfalse\nfalse\nfalse\nfalse\nfalse\n"
    "false\nfalse\ntrue\nfalse\nfalse\ntrue\nfalse\ntrue\ntrue\n"
    "nan\ntrue\nfalse\ntrue\nfalse\ntrue\n"
    "3\n😀\n😀\nA\n😀\nB\n1.9\n1.9\n"
    "Number\nNumber\nList\nObject\nfixed\nvarargs\nString\n"
    "1.5\n1.1\n1\n"
    "{__num=nan}\n{__num=nan, keep=1.0}\n"
    "{__map=[[__num, nan]]}\n{nested={__num=nan}}\n"
    '{"__num": "nan"}\n{"__num": "nan", "keep": 1}\n'
    '{"__map": [["__num", "nan"]]}\n'
    '{"nested": {"__num": "nan"}}\n'
    '{"__num": "nan"}\n{"__num": "nan", "keep": 1}\n'
    '{"__map": [["__num", "nan"]]}\n'
    '{"nested": {"__num": "nan"}}\n')
COOKBOOK_VARIADIC_STDOUT = (
    '["default", 2, [], {}]\n'
    '["card", 3, ["extra"], {"color": "blue"}]\n'
    '["keyword", 4, [], {"border": true}]\n')
COOKBOOK_KEYWORD_ONLY_STDOUT = (
    '["api.example", 80, true, 5]\n'
    '["edge.example", 80, false, nil]\n'
    '["build", ["clean", "test"], "safe", 2, {"verbose": true}]\n')
COOKBOOK_INTEROP_BOUND_METHODS_STDOUT = (
    "<bound method collections.deque.append>\nnil\nnil\n"
    "<bound method java.util.ArrayList.add>\ntrue\nnil\ntrue\n")
COOKBOOK_INTEROP_FUNCTION_VALUES_STDOUT = (
    "<module function math.sqrt>\n5\n6\n"
    "<static method java.lang.Math.pow>\n8\n8\n")


def run(cmd, cwd=None, timeout=60, env=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout,
                           env=TEST_ENV if env is None else env)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as exc:
        def text(value):
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return value or ""
        return "timeout", text(exc.stdout), text(exc.stderr)


def cap(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    s = "".join(c if (c.isalnum() or c == "_") else "_" for c in stem)
    return (s[0].upper() + s[1:]) if s else "_"


def is_module_case(name, srcdir):
    return (name.startswith("modules/") or
            (os.path.realpath(srcdir) == os.path.realpath(COOKBOOK_DIR) and
             name == "modules"))


def compile_and_run(name, target, srcdir=TESTS):
    src = os.path.join(srcdir, name + ".lt")
    outdir = os.path.join(OUT, "t_" + name + "_" + target)
    os.makedirs(outdir, exist_ok=True)
    rc, so, se = run([sys.executable, LATENTC, src, "-t", target, "-o", outdir])
    if rc != 0:
        return ("compile-fail", so, se)
    if name == "phase1_semantics" or name.startswith("phase1_negative/"):
        fixture = os.path.join(TESTS, "java", "Stage1Overloads.java")
        rc, so, se = run(["javac", "-cp", outdir, "-d", outdir, fixture])
        if rc != 0:
            return ("compile-fail", so, se)
    p13_case = name == "p13_interop_bound_methods" or name.startswith("p13_negative/")
    p14_case = (name in ("p14_module_and_static_values",
                         "p14_static_method_extraction") or
                name.startswith("p14_negative/"))
    run_env = None
    if p13_case:
        fixture = os.path.join(TESTS, "java", "P13InteropFixture.java")
        rc, so, se = run(["javac", "-cp", outdir, "-d", outdir, fixture])
        if rc != 0:
            return ("compile-fail", so, se)
    if p14_case:
        fixture = os.path.join(TESTS, "java", "P14InteropFixture.java")
        rc, so, se = run(["javac", "-cp", outdir, "-d", outdir, fixture])
        if rc != 0:
            return ("compile-fail", so, se)
    if p13_case or p14_case:
        run_env = clean_test_environment()
        run_env["PYTHONPATH"] = os.path.join(TESTS, "python") + os.pathsep + \
            run_env.get("PYTHONPATH", "")
    if target == "py":
        cwd = tempfile.gettempdir() if is_module_case(name, srcdir) else None
        rc, so, se = run([sys.executable, os.path.join(outdir, os.path.basename(src)[:-3] + ".py")],
                         cwd=cwd, env=run_env)
    else:
        cwd = tempfile.gettempdir() if is_module_case(name, srcdir) else outdir
        rc, so, se = run(["java", "-cp", outdir, cap(name)], cwd=cwd,
                         env=run_env)
    return (rc, so, se)


def has_source_locations(name, result):
    """Require mapped source locations without coupling to backend wording."""
    _, _, stderr = result
    if name in RUNTIME_SOURCE_MARKERS:
        locations = all(marker in stderr
                        for marker in RUNTIME_SOURCE_MARKERS[name])
    else:
        locations = all(f"{name}.lt:{line}" in stderr
                        for line in RUNTIME_SOURCE_LINES[name])
    error_marker = RUNTIME_ERROR_MARKERS.get(name)
    return ("Latent runtime error:" in stderr and locations and
            (error_marker is None or error_marker in stderr))


def main():
    fails = 0

    print("== positive: py vs java must produce identical stdout, exit 0 ==")
    for name in POSITIVE + MODULE_POSITIVE:
        py = compile_and_run(name, "py")
        jv = compile_and_run(name, "java")
        ok = (py[0] == 0 and jv[0] == 0 and py[1] == jv[1] and
              not py[2] and not jv[2])
        if name == "try_basic":
            ok = ok and py[1] == TRY_BASIC_STDOUT and jv[1] == TRY_BASIC_STDOUT
        elif name == "modules/app/main":
            ok = ok and py[1] == MODULE_APP_STDOUT
        elif name == "modules/same/main":
            ok = ok and py[1] == MODULE_SAME_STDOUT
        elif name == "inheritance":
            ok = ok and py[1] == INHERITANCE_STDOUT
        elif name == "modules/inheritance/main":
            ok = ok and py[1] == MODULE_INHERITANCE_STDOUT
        if name in P5_EXPECTED_STDOUT:
            ok = ok and py[1] == P5_EXPECTED_STDOUT[name]
        if name in P6_EXPECTED_STDOUT:
            ok = ok and py[1] == P6_EXPECTED_STDOUT[name]
        if name in P7_EXPECTED_STDOUT:
            ok = ok and py[1] == P7_EXPECTED_STDOUT[name]
        if name in P8_EXPECTED_STDOUT:
            ok = ok and py[1] == P8_EXPECTED_STDOUT[name]
        if name in P9_EXPECTED_STDOUT:
            ok = ok and py[1] == P9_EXPECTED_STDOUT[name]
        if name in P10_EXPECTED_STDOUT:
            ok = ok and py[1] == P10_EXPECTED_STDOUT[name]
        if name in P11_EXPECTED_STDOUT:
            ok = ok and py[1] == P11_EXPECTED_STDOUT[name]
        if name in P12_EXPECTED_STDOUT:
            ok = ok and py[1] == P12_EXPECTED_STDOUT[name]
        if name == "phase1_semantics":
            ok = ok and py[1] == PHASE1_EXPECTED_STDOUT
        if name == "p13_interop_bound_methods":
            ok = ok and py[1] == P13_EXPECTED_STDOUT
        if name == "p14_module_and_static_values":
            ok = ok and py[1] == P14_EXPECTED_STDOUT
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={py[0]} out={py[1]!r} err={py[2][-500:]}")
            print(f"  java: rc={jv[0]} out={jv[1]!r} err={jv[2][-500:]}")

    print("== negative compile: latent must reject ==")
    for name in NEG_COMPILE:
        src = os.path.join(TESTS, name + ".lt")
        rc, so, se = run([sys.executable, LATENTC, src, "-t", "py",
                          "-o", os.path.join(OUT, "t_" + name)])
        detail = so + se
        ok = rc != 0 and (name not in NONLOCAL_COMPILE_MARKERS or
                          NONLOCAL_COMPILE_MARKERS[name] in detail)
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else " (compiled!)"))
        if not ok:
            fails += 1
        else:
            print(f"  msg: {(so + se).strip().splitlines()[0]}")

    print("== P8 negative compile: py/java must match static diagnostics ==")
    for name, marker in P8_NEG_COMPILE.items():
        details = []
        results = []
        for target in ("py", "java"):
            outdir = os.path.join(OUT, "t_" + name.replace("/", "_") + "_" + target)
            rc, so, se = run([sys.executable, LATENTC,
                              os.path.join(TESTS, name + ".lt"),
                              "-t", target, "-o", outdir])
            detail = so + se
            details.append(detail)
            results.append(rc)
        first_lines = [d.strip().splitlines()[0] if d.strip() else ""
                       for d in details]
        ok = (all(rc != 0 for rc in results) and
              all(marker in detail for detail in details) and
              first_lines[0] == first_lines[1])
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={results[0]} detail={details[0][-500:]}")
            print(f"  java: rc={results[1]} detail={details[1][-500:]}")

    print("== P9 negative compile: default-parameter binding and syntax ==")
    for name, marker in P9_NEG_COMPILE.items():
        details = []
        results = []
        for target in ("py", "java"):
            outdir = os.path.join(OUT, "t_" + name.replace("/", "_") + "_" + target)
            rc, so, se = run([sys.executable, LATENTC,
                              os.path.join(TESTS, name + ".lt"),
                              "-t", target, "-o", outdir])
            details.append(so + se)
            results.append(rc)
        first_lines = [d.strip().splitlines()[0] if d.strip() else ""
                       for d in details]
        ok = (all(rc != 0 for rc in results) and
              all(marker in detail for detail in details) and
              first_lines[0] == first_lines[1])
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={results[0]} detail={details[0][-500:]}")
            print(f"  java: rc={results[1]} detail={details[1][-500:]}")

    print("== P10 negative compile: variadic signature and call ordering ==")
    for name, marker in P10_NEG_COMPILE.items():
        details = []
        results = []
        for target in ("py", "java"):
            outdir = os.path.join(OUT, "t_" + name.replace("/", "_") + "_" + target)
            rc, so, se = run([sys.executable, LATENTC,
                              os.path.join(TESTS, name + ".lt"),
                              "-t", target, "-o", outdir])
            details.append(so + se)
            results.append(rc)
        first_lines = [d.strip().splitlines()[0] if d.strip() else ""
                       for d in details]
        ok = (all(rc != 0 for rc in results) and
              all(marker in detail for detail in details) and
              first_lines[0] == first_lines[1])
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={results[0]} detail={details[0][-500:]}")
            print(f"  java: rc={results[1]} detail={details[1][-500:]}")

    print("== P11 negative compile: keyword-only syntax and static binding ==")
    for name, marker in P11_NEG_COMPILE.items():
        details = []
        results = []
        for target in ("py", "java"):
            outdir = os.path.join(OUT, "t_" + name.replace("/", "_") + "_" + target)
            rc, so, se = run([sys.executable, LATENTC,
                              os.path.join(TESTS, name + ".lt"),
                              "-t", target, "-o", outdir])
            details.append(so + se)
            results.append(rc)
        first_lines = [d.strip().splitlines()[0] if d.strip() else ""
                       for d in details]
        ok = (all(rc != 0 for rc in results) and
              all(marker in detail for detail in details) and
              first_lines[0] == first_lines[1])
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={results[0]} detail={details[0][-500:]}")
            print(f"  java: rc={results[1]} detail={details[1][-500:]}")

    print("== negative inheritance compile: parent graph and super rules ==")
    for name in NEG_INHERITANCE_COMPILE:
        src = os.path.join(TESTS, name + ".lt")
        rc, so, se = run([sys.executable, LATENTC, src, "-t", "py",
                          "-o", os.path.join(OUT, "t_" + name)])
        detail = so + se
        ok = (rc != 0 and INHERITANCE_COMPILE_MARKERS[name] in detail and
              all(marker in detail for marker in
                  INHERITANCE_ERROR_LOCATIONS[name]))
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  expected={INHERITANCE_COMPILE_MARKERS[name]!r} "
                  f"out={so!r} err={se[-500:]}")

    print("== P12 override signature compatibility: reject narrowing on py/java ==")
    for name, marker in P12_NEG_COMPILE.items():
        details = []
        results = []
        src = os.path.join(TESTS, name + ".lt")
        location = f"{os.path.basename(name)}.lt:6:"
        for target in ("py", "java"):
            outdir = os.path.join(OUT, "t_" + name.replace("/", "_") + "_" + target)
            rc, so, se = run([sys.executable, LATENTC, src, "-t", target,
                              "-o", outdir])
            details.append(so + se)
            results.append(rc)
        first_lines = [d.strip().splitlines()[0] if d.strip() else ""
                       for d in details]
        ok = (all(rc != 0 for rc in results) and
              all(marker in detail and location in detail for detail in details) and
              first_lines[0] == first_lines[1])
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={results[0]} detail={details[0][-500:]}")
            print(f"  java: rc={results[1]} detail={details[1][-500:]}")

    print("== negative module compile: paths, visibility, graph, and namespace rules ==")
    for name in NEG_MODULE_COMPILE:
        src = os.path.join(TESTS, name + ".lt")
        rc, so, se = run([sys.executable, LATENTC, src, "-t", "py",
                          "-o", os.path.join(OUT, "t_" + name)])
        detail = so + se
        ok = (rc != 0 and MODULE_COMPILE_MARKERS[name] in detail and
              all(marker in detail for marker in
                  MODULE_ERROR_LOCATIONS.get(name, [])))
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  rc={rc} expected={MODULE_COMPILE_MARKERS[name]!r} "
                  f"out={so!r} err={se[-500:]}")

    print("== negative runtime: must fail on both backends and map source ==")
    for name in NEG_RUNTIME:
        py = compile_and_run(name, "py")
        jv = compile_and_run(name, "java")
        if name in {"p13_negative/python_named", "p14_negative/module_named"}:
            # P19-B: retain the historical IDs and sources, but now both
            # Python-host call paths must actually succeed without output.
            ok = all(result[0] == 0 and result[1] == "" and result[2] == ""
                     for result in (py, jv))
            print(("PASS " if ok else "FAIL ") + name + " (P19-B success)")
            if not ok:
                fails += 1
                print(f"  py: {py!r}\n  java: {jv!r}")
            continue
        ok = (py[0] not in (0, "compile-fail", "timeout") and
              jv[0] not in (0, "compile-fail", "timeout") and
              has_source_locations(name, py) and has_source_locations(name, jv))
        if (name in P8_RUNTIME_ERROR_MARKERS or
                name in P9_RUNTIME_ERROR_MARKERS or
                name in P10_RUNTIME_ERROR_MARKERS or
                name in P11_RUNTIME_ERROR_MARKERS or
                name in PHASE1_RUNTIME_ERROR_MARKERS):
            expected = (P8_RUNTIME_ERROR_MARKERS.get(name) or
                        P9_RUNTIME_ERROR_MARKERS.get(name) or
                        P10_RUNTIME_ERROR_MARKERS.get(name) or
                        P11_RUNTIME_ERROR_MARKERS.get(name) or
                        PHASE1_RUNTIME_ERROR_MARKERS[name])
            py_error = next((line for line in py[2].splitlines()
                             if line.startswith("Latent runtime error:")), "")
            jv_error = next((line for line in jv[2].splitlines()
                             if line.startswith("Latent runtime error:")), "")
            if name == "p8_negative/python_interop":
                # P19-B delegates this native signature error to Python.
                # Java keeps its existing host-error carrier; pin both the
                # carrier and the actual Python exception, not its traceback.
                host_error = expected.removeprefix("Latent runtime error: ")
                ok = (ok and py[0] == 1 and jv[0] == 1 and
                      py[1] == "" and jv[1] == "" and py_error == expected and
                      jv_error == "Latent runtime error: RuntimeException: "
                                  "python error: Traceback (most recent call last):" and
                      host_error in jv[2].splitlines())
            else:
                ok = ok and py_error == expected and jv_error == expected
        if name == "p14_negative/module_callback":
            # Both bridges must reject the returned adapter at the same
            # boundary rather than accidentally passing the callable.
            ok = (ok and "callbacks cannot return Latent callable values" in py[2]
                  and "callbacks cannot return Latent callable values" in jv[2])
        if name in P14_RUNTIME_ERROR_MARKERS:
            marker = P14_RUNTIME_ERROR_MARKERS[name]
            ok = ok and marker in py[2] and marker in jv[2]
        print(("PASS " if ok else "FAIL ") + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={py[0]} mapped={has_source_locations(name, py)} "
                  f"err={py[2][-500:]}")
            print(f"  java: rc={jv[0]} mapped={has_source_locations(name, jv)} "
                  f"err={jv[2][-500:]}")

    print("== P14 typed daemon refs: stale ids must be rejected ==")
    rc, so, se = run([sys.executable, os.path.join(TESTS,
                                                   "test_p14_protocol.py")])
    print(("PASS " if rc == 0 else "FAIL ") + "P14 daemon typed refs")
    if rc != 0:
        fails += 1
        print(f"  {(so + se)[-1500:]}")

    print("== P18 synchronous Python-host callbacks ==")
    rc, so, se = run([sys.executable, os.path.join(TESTS,
                                                   "test_p18_callbacks.py")],
                     timeout=300)
    print(("PASS " if rc == 0 else "FAIL ") + "P18 callback matrix")
    if rc != 0:
        fails += 1
        print(f"  {(so + se)[-2500:]}")

    print("== P19-B explicit Python-host named arguments ==")
    rc, so, se = run([sys.executable, os.path.join(TESTS,
                                                   "test_p19_named_host.py")],
                     timeout=300)
    print(("PASS " if rc == 0 else "FAIL ") + "P19-B named host matrix")
    if rc != 0:
        fails += 1
        print(f"  {(so + se)[-3500:]}")

    print("== cookbook: py vs java must produce identical stdout, exit 0 ==")
    for name in COOKBOOK:
        py = compile_and_run(name, "py", COOKBOOK_DIR)
        jv = compile_and_run(name, "java", COOKBOOK_DIR)
        ok = (py[0] == 0 and jv[0] == 0 and py[1] == jv[1] and
              not py[2] and not jv[2])
        if name == "modules":
            ok = ok and py[1] == "Hello Latent\n"
        elif name == "inheritance":
            ok = ok and py[1] == COOKBOOK_INHERITANCE_STDOUT
        elif name == "named_arguments":
            ok = ok and py[1] == "tea:3\n6\n"
        elif name == "variadic_arguments":
            ok = ok and py[1] == COOKBOOK_VARIADIC_STDOUT
        elif name == "keyword_only_arguments":
            ok = ok and py[1] == COOKBOOK_KEYWORD_ONLY_STDOUT
        elif name == "interop_bound_methods":
            ok = ok and py[1] == COOKBOOK_INTEROP_BOUND_METHODS_STDOUT
        elif name == "interop_function_values":
            ok = ok and py[1] == COOKBOOK_INTEROP_FUNCTION_VALUES_STDOUT
        print(("PASS " if ok else "FAIL ") + "cookbook/" + name)
        if not ok:
            fails += 1
            print(f"  py:   rc={py[0]} out={py[1]!r} err={py[2][-500:]}")
            print(f"  java: rc={jv[0]} out={jv[1]!r} err={jv[2][-500:]}")

    print("== Python REPL CLI regressions ==")
    rc, so, se = run([sys.executable, os.path.join(TESTS, "test_repl.py")])
    repl_output = so + se
    repl_count_match = re.search(r"Ran (\d+) tests?", repl_output)
    repl_total = int(repl_count_match.group(1)) if repl_count_match else 1
    print(("PASS " if rc == 0 else "FAIL ") +
          f"CLI REPL ({repl_total} regression tests)")
    if rc != 0:
        repl_failure_match = re.search(
            r"FAILED \(failures=(\d+)(?:, errors=(\d+))?\)", repl_output)
        repl_failures = (sum(int(n or 0) for n in repl_failure_match.groups())
                         if repl_failure_match else repl_total)
        fails += repl_failures
        print(f"  {repl_output[-1500:]}")

    print("== read-only CLI diagnostics: AST / desugar and no runtime execution ==")
    rc, so, se = run([sys.executable, os.path.join(TESTS,
                                                   "test_diagnostics.py")])
    diagnostic_output = so + se
    count_match = re.search(r"Ran (\d+) tests?", diagnostic_output)
    diagnostic_total = int(count_match.group(1)) if count_match else 1
    print(("PASS " if rc == 0 else "FAIL ") +
          f"CLI diagnostics ({diagnostic_total} unit/regression tests)")
    if rc != 0:
        failure_match = re.search(
            r"FAILED \(failures=(\d+)(?:, errors=(\d+))?\)",
            diagnostic_output)
        diagnostic_failures = (sum(int(n or 0) for n in failure_match.groups())
                               if failure_match else diagnostic_total)
        fails += diagnostic_failures
        print(f"  {diagnostic_output[-1500:]}")

    print("== seeded bounded cross-backend property tests ==")
    rc, so, se = run([sys.executable, os.path.join(
        TESTS, "test_differential_properties.py")], timeout=180)
    property_output = so + se
    if rc == 0 and property_output.strip():
        print(property_output.strip())
    else:
        print(("PASS " if rc == 0 else "FAIL ") +
              "seeded differential/property tests")
    if rc != 0:
        fails += 1
        print(f"  {property_output[-3000:]}")

    print("== security and compiler audit regressions ==")
    rc, so, se = run([sys.executable, "-m", "unittest", "discover",
                      "-s", TESTS, "-p", "test_audit_*.py"], timeout=300)
    audit_output = so + se
    count_match = re.search(r"Ran (\d+) tests?", audit_output)
    audit_total = int(count_match.group(1)) if count_match else 1
    print(("PASS " if rc == 0 else "FAIL ") +
          f"audit regressions ({audit_total} tests)")
    if rc != 0:
        failure_match = re.search(r"FAILED \(([^)]+)\)", audit_output)
        audit_failures = (sum(int(n) for n in re.findall(
            r"(?:failures|errors)=(\d+)", failure_match.group(1)))
            if failure_match else audit_total)
        fails += max(1, audit_failures)
        print(f"  {audit_output[-5000:]}")

    total = (audit_total + repl_total + diagnostic_total + len(POSITIVE) + len(MODULE_POSITIVE) +
             len(NEG_COMPILE) +
             len(P8_NEG_COMPILE) +
             len(P9_NEG_COMPILE) +
             len(P10_NEG_COMPILE) +
             len(P11_NEG_COMPILE) +
             len(P12_NEG_COMPILE) +
             len(NEG_INHERITANCE_COMPILE) +
             len(NEG_MODULE_COMPILE) + len(NEG_RUNTIME) + len(COOKBOOK) + 3)
    print(f"\n{total - fails} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
