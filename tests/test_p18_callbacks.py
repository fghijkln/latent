#!/usr/bin/env python3
"""Focused dual-backend tests for synchronous Python-host callbacks (P18)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
LATENTC = ROOT / "latent.py"
HOST_PATH = TESTS / "python"
sys.path.insert(0, str(ROOT))
from tests._safe_test_env import clean_test_environment

ENV = clean_test_environment({"PYTHONPATH": str(HOST_PATH)})
TARGETS = ("py", "java")
EXPECTED_POSITIVE = (
    "#1:a #2:bb\n"
    "#3:c\n"
    "#4:dd\n"
    "conversion-ok\n"
    "one!\n"
    "2\n"
    "x!xx!x\n"
    "32\n"
    "compiled!\n"
    "extracted!\n"
    "positional:star-arg\n"
    "positional:partial\n"
    "default! | one! | two?\n"
    "0,1,2\n"
)
RECOVERY_CASES = {
    "host_error_then_recover": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "try:\n"
        "    host.save_then_fail(callback)\n"
        "catch error:\n"
        '    say "caught-host-error"\n'
        'say host.identity("still-usable")\n',
        "caught-host-error\nstill-usable\n",
    ),
    "callback_error_then_recover": (
        'rx = py "re"\n'
        "fn broken(match):\n"
        "    if true:\n"
        '        throw "callback-caught"\n'
        "fn good(match):\n"
        '    return "recovered"\n'
        "try:\n"
        '    rx.sub("x", broken, "x")\n'
        "catch error:\n"
        '    say "caught-callback-error"\n'
        'say rx.sub("x", good, "x")\n',
        "caught-callback-error\nrecovered\n",
    ),
    "host_catches_callback_error": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    if true:\n"
        '        throw "caught-by-host"\n'
        'say host.catch_callback_error(callback)\n',
        "caught:caught-by-host\n",
    ),
}

# Each error expectation uses stable semantics/keywords, not spike wording.
ERROR_CASES = {
    "callback_throw": (
        'rx = py "re"\n'
        "fn callback(match):\n"
        "    if true:\n"
        '        throw "callback-boom"\n'
        'say rx.sub("x", callback, "x")\n',
        ("callback-boom", "callback_throw.lt:4", "callback_throw.lt:5"),
        (),
    ),
    "callback_runtime_error": (
        'rx = py "re"\n'
        "fn callback(match):\n"
        "    return [1][9]\n"
        'say rx.sub("x", callback, "x")\n',
        ("index out of range", "callback_runtime_error.lt:3",
         "callback_runtime_error.lt:4"),
        (),
    ),
    "callback_arity": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "say host.invoke_two(callback)\n",
        ("ArgumentError", "takes 1", "callback_arity.lt:4"), (),
    ),
    "callback_expanded_arity": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "say host.call_with_two_star_args(callback)\n",
        ("ArgumentError", "takes 1", "callback_expanded_arity.lt:4"), (),
    ),
    "host_type_error": (
        'rx = py "re"\n'
        "fn callback(match):\n"
        "    return 42\n"
        'say rx.sub("x", callback, "x")\n',
        ("TypeError", "host_type_error.lt:4"),
        ("host_type_error.lt:3",),
    ),
    "expired_after_success": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        '    if true:\n'
        '        throw "MUST-NOT-RUN"\n'
        'say host.save(callback)\n'
        "say host.call_saved()\n",
        ("callback", "expired", "expired_after_success.lt:6"),
        ("MUST-NOT-RUN",),
    ),
    "expired_after_failure": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        '    if true:\n'
        '        throw "MUST-NOT-RUN"\n'
        "try:\n"
        "    host.save_then_fail(callback)\n"
        "catch error:\n"
        '    say "caught"\n'
        "say host.call_saved()\n",
        ("callback", "expired", "expired_after_failure.lt:9"),
        ("MUST-NOT-RUN",),
    ),
    "cross_thread": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        '    if true:\n'
        '        throw "MUST-NOT-RUN"\n'
        "say host.call_on_worker(callback)\n",
        ("thread", "callback", "cross_thread.lt:5"),
        ("MUST-NOT-RUN",),
    ),
    "host_keyword_callback": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "say host.call_keyword(callback)\n",
        ("callback", "keyword"), (),
    ),
    "deferred_host_result": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "say host.deferred(callback)\n",
        ("any:deferred results|async/deferred",), (),
    ),
    "async_host_result": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "say host.async_deferred(callback)\n",
        ("any:coroutine|awaitable|async",), (),
    ),
    "callback_returns_generator": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return host.generator_value()\n"
        "say host.invoke_one(callback)\n",
        ("generator",), (),
    ),
    "callback_returns_callable": (
        'host = py "p18_callbacks_host"\n'
        "fn inner(value):\n"
        "    return value\n"
        "fn callback(value):\n"
        "    return inner\n"
        "say host.invoke_one(callback)\n",
        ("callable", "return"), (),
    ),
    "callback_generator_consumed_immediately": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return host.generator_value()\n"
        "say host.consume_callback_generator(callback)\n",
        ("any:generator|deferred",), ("generator-was-consumed",),
    ),
    "callback_callable_consumed_immediately": (
        'host = py "p18_callbacks_host"\n'
        "fn inner(value):\n"
        '    if true:\n'
        '        throw "MUST-NOT-RUN"\n'
        "fn callback(value):\n"
        "    return inner\n"
        "say host.consume_callback_callable(callback)\n",
        ("any:callable|return",), ("MUST-NOT-RUN",),
    ),
    "callback_star_kwargs": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "say host.call_with_star_kwargs(callback)\n",
        ("callback", "keyword"), (),
    ),
    "host_transforms_callback_error": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    if true:\n"
        '        throw "callback-transform-source"\n'
        "say host.transform_callback_error(callback)\n",
        ("wrapped:", "callback-transform-source", "host_transforms_callback_error.lt:5"),
        (),
    ),
    "host_returns_callback": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "say host.echo(callback)\n",
        ("return",), (),
    ),
    "nested_callable_argument": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "say host.identity([callback])\n",
        ("callable",), (),
    ),
    "host_named_argument": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        'say host.identity(callback, other="x")\n',
        ("named arguments",), (),
    ),
    "host_star_argument": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        "say host.identity(callback, *[])\n",
        ("argument unpacking",), (),
    ),
    "host_starstar_argument": (
        'host = py "p18_callbacks_host"\n'
        "fn callback(value):\n"
        "    return value\n"
        'say host.identity(callback, **{})\n',
        ("argument unpacking",), (),
    ),
    "reentry_depth_limit": (
        'rx = py "re"\n'
        "fn callback(match):\n"
        '    return rx.sub("x", callback, "x")\n'
        'say rx.sub("x", callback, "x")\n',
        ("depth",), (),
    ),
}


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 25,
        env: dict[str, str] | None = None):
    try:
        return subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, env=ENV if env is None else env,
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        out = error.stdout or ""
        err = error.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return subprocess.CompletedProcess(cmd, "timeout", out, err)


def compile_and_run(source: Path, target: str, outdir: Path):
    compiled = run([sys.executable, str(LATENTC), str(source), "-t", target,
                    "-o", str(outdir)])
    if compiled.returncode != 0:
        return compiled
    if target == "py":
        cmd = [sys.executable, str(outdir / (source.stem + ".py"))]
        cwd = None
    else:
        cmd = ["java", "-cp", str(outdir), java_class_name(source.stem)]
        cwd = outdir
    return run(cmd, cwd=cwd)


def java_class_name(stem: str) -> str:
    """Match gen_java.cls_name: sanitize the stem then capitalize it."""
    name = "".join(char if char.isalnum() or char == "_" else "_"
                   for char in stem)
    if not name or name[0].isdigit():
        name = "_" + name
    if name in {"abstract", "assert", "boolean", "break", "byte", "case",
                "catch", "char", "class", "const", "continue", "default",
                "do", "double", "else", "enum", "extends", "final",
                "finally", "float", "for", "goto", "if", "implements",
                "import", "instanceof", "int", "interface", "long",
                "native", "new", "package", "private", "protected",
                "public", "return", "short", "static", "strictfp",
                "super", "switch", "synchronized", "this", "throw",
                "throws", "transient", "try", "void", "volatile",
                "while", "true", "false", "null", "var", "LtRt"}:
        name += "_"
    return name[0].upper() + name[1:]


def has_all(text: str, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        if marker.startswith("any:"):
            if not any(option in text for option in marker[4:].split("|")):
                return False
        elif marker not in text:
            return False
    return True


def test_native_fd_callback_isolation() -> list[bool]:
    """Native fd 0/1 access must not consume or corrupt daemon protocol frames."""
    cases = {
        "native_fd_write": (
            'host = py "p18_callbacks_host"\n'
            "fn echo(value):\n"
            "    return value\n"
            'say host.native_fd_write(echo)\n'
            'say "after-native-write"\n',
            ("fd-write", "after-native-write"),
        ),
        "native_fd_read": (
            'host = py "p18_callbacks_host"\n'
            "fn echo(value):\n"
            "    return value\n"
            'say host.native_fd_read(echo)\n'
            'say "after-native-read"\n',
            ("fd-read-empty", "after-native-read"),
        ),
    }
    results: list[bool] = []
    for name, (source_text, required_output) in cases.items():
        with tempfile.TemporaryDirectory(prefix="latent-p18-fd-") as tmp:
            source = Path(tmp) / f"{name}.lt"
            source.write_text(source_text, encoding="utf-8")
            for target in TARGETS:
                outdir = Path(tmp) / f"out-{target}"
                outdir.mkdir()
                result = compile_and_run(source, target, outdir)
                ok = (result.returncode == 0 and
                      all(line in result.stdout.splitlines()
                          for line in required_output) and
                      "invalid protocol" not in result.stderr.lower() and
                      "invalid response" not in result.stderr.lower())
                if not ok:
                    print(f"FAIL {target} {name}: rc={result.returncode}")
                    print(f"  stdout={result.stdout[-800:]!r}")
                    print(f"  stderr={result.stderr[-1200:]!r}")
                else:
                    print(f"PASS {target} {name}")
                results.append(ok)
    return results


def run_single(source: Path, target: str, case_name: str, expected: str | None,
               markers: tuple[str, ...] = (), absent: tuple[str, ...] = ()):
    with tempfile.TemporaryDirectory(prefix="latent-p18-callback-") as tmp:
        outdir = Path(tmp) / "out"
        outdir.mkdir()
        result = compile_and_run(source, target, outdir)
    if expected is not None:
        ok = (result.returncode == 0 and result.stdout == expected and
              result.stderr == "")
    else:
        ok = (result.returncode not in (0, "timeout") and
              has_all(result.stderr, markers) and
              all(marker not in result.stderr for marker in absent))
    # A deliberate recursion case must terminate promptly with a bounded-depth
    # diagnostic rather than hanging or overflowing into an unrelated failure.
    if case_name == "reentry_depth_limit":
        ok = ok and "Latent runtime error:" in result.stderr
    if not ok:
        print(f"FAIL {target} {case_name}: rc={result.returncode}")
        print(f"  stdout={result.stdout[-800:]!r}")
        print(f"  stderr={result.stderr[-1600:]!r}")
    return ok


def test_python_callback_protocol_ids() -> bool:
    """Reject malformed callback token and callback-result invocation IDs."""
    request_bad_token = {
        "kind": "request", "id": 1, "parent": 0,
        "payload": {"op": "call", "target": {"mod": "p18_callbacks_host"},
                    "attr": "invoke_one", "args": [{"__lt_callback__": -1}]},
    }
    def invoke(request: dict, result_frame: dict | None = None):
        frames = [request]
        if result_frame is not None:
            frames.append(result_frame)
        frames.append({"op": "quit"})
        try:
            return subprocess.run(
                [sys.executable, str(ROOT / "runtime" / "ltpy.py")],
                input="\n".join(json.dumps(frame) for frame in frames) + "\n",
                capture_output=True, text=True, env=ENV, timeout=10)
        except subprocess.TimeoutExpired:
            return None

    # A malformed direct callback descriptor aborts the daemon; it is not a
    # normal request error. Keep it separate from the callback-result test.
    bad_token = invoke(request_bad_token)
    if bad_token is None or bad_token.returncode == 0 or \
            "invalid callback token ID" not in bad_token.stderr:
        print("FAIL Python callback protocol token ID")
        if bad_token is not None:
            print(f"  rc={bad_token.returncode} stderr={bad_token.stderr[-800:]!r}")
        return False

    request_callback = {
        "kind": "request", "id": 2, "parent": 0,
        "payload": {"op": "call", "target": {"mod": "p18_callbacks_host"},
                    "attr": "invoke_one", "args": [{"__lt_callback__": 7}]},
    }
    bad_callback_result = {
        "kind": "callback_result", "parent": 2,
        "id": "not-an-integer", "value": "ignored",
    }
    bad_result = invoke(request_callback, bad_callback_result)
    callback_frames = []
    if bad_result is not None:
        try:
            callback_frames = [json.loads(line) for line in
                               bad_result.stdout.splitlines() if line]
        except json.JSONDecodeError:
            pass
    ok = (bad_result is not None and bad_result.returncode != 0 and
          "invalid callback result ID" in bad_result.stderr and
          any(frame.get("kind") == "callback" and frame.get("callback") == 7
              and frame.get("parent") == 2 for frame in callback_frames))
    if not ok:
        print("FAIL Python callback protocol result ID")
        if bad_result is not None:
            print(f"  rc={bad_result.returncode} frames={callback_frames!r}")
            print(f"  stderr={bad_result.stderr[-1000:]!r}")
    return ok


def compile_java_fixture(source: Path, outdir: Path) -> subprocess.CompletedProcess:
    outdir.mkdir(parents=True, exist_ok=True)
    return run([sys.executable, str(LATENTC), str(source), "-t", "java",
                "-o", str(outdir)])


def run_java_helper(source_text: str, class_name: str, java_text: str,
                    expected: str, *, out_markers: tuple[str, ...] = ()) -> bool:
    """Compile one generated Java runtime, then execute a small API-level probe."""
    with tempfile.TemporaryDirectory(prefix="latent-p18-java-helper-") as tmp:
        base = Path(tmp)
        source = base / "helper_fixture.lt"
        outdir = base / "out"
        source.write_text(source_text, encoding="utf-8")
        compiled = compile_java_fixture(source, outdir)
        if compiled.returncode != 0:
            print(f"FAIL Java helper {class_name}: generated fixture compile failed")
            print(f"  stderr={compiled.stderr[-1200:]!r}")
            return False
        helper = base / f"{class_name}.java"
        helper.write_text(java_text, encoding="utf-8")
        javac = run(["javac", "-cp", str(outdir), "-d", str(outdir),
                     str(helper)])
        if javac.returncode != 0:
            print(f"FAIL Java helper {class_name}: harness compile failed")
            print(f"  stderr={javac.stderr[-1200:]!r}")
            return False
        result = run(["java", "-cp", str(outdir), class_name], cwd=outdir,
                     timeout=35)
    ok = (result.returncode == 0 and result.stderr == "" and
          result.stdout == expected and has_all(result.stdout, out_markers))
    if not ok:
        print(f"FAIL Java helper {class_name}: rc={result.returncode}")
        print(f"  stdout={result.stdout[-1200:]!r}")
        print(f"  stderr={result.stderr[-1600:]!r}")
    return ok


def test_java_throwable_identity_and_error_cleanup() -> bool:
    java_text = r'''import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
public class ThrowableIdentity {
    static WeakReference<Throwable> exercise(Object re, int n) {
        RuntimeException sentinel = new RuntimeException("identity-" + n);
        StackTraceElement[] before = sentinel.getStackTrace();
        LtRt.LtFunction callback = LtRt.function(new String[]{"m"}, 1,
            "identity_callback", args -> {
                Object group = LtRt.wcall(args[0], "group", 0.0);
                if (!"x".equals(group)) throw new AssertionError("bad callback input");
                throw sentinel;
            });
        try {
            LtRt.wcall(re, "sub", "x", callback, "x");
            throw new AssertionError("callback failure was swallowed");
        } catch (RuntimeException got) {
            if (got != sentinel) throw new AssertionError("Throwable identity replaced", got);
            if (!Arrays.equals(before, got.getStackTrace()))
                throw new AssertionError("Throwable stack trace modified");
        }
        callback = null;
        return new WeakReference<Throwable>(sentinel);
    }
    static WeakReference<Throwable> exerciseHostRethrow(Object host, int n) {
        RuntimeException sentinel = new RuntimeException("host-identity-" + n);
        StackTraceElement[] before = sentinel.getStackTrace();
        LtRt.LtFunction callback = LtRt.function(new String[]{"m"}, 1,
            "host_identity_callback", args -> { throw sentinel; });
        try {
            LtRt.wcall(host, "rethrow_callback_error", callback);
            throw new AssertionError("host failed to rethrow callback failure");
        } catch (RuntimeException got) {
            if (got != sentinel) throw new AssertionError("host replaced Throwable", got);
            if (!Arrays.equals(before, got.getStackTrace()))
                throw new AssertionError("host rethrow modified Throwable stack trace");
        }
        callback = null;
        return new WeakReference<Throwable>(sentinel);
    }
    public static void main(String[] args) {
        Object re = LtRt.pymod("re");
        Object host = LtRt.pymod("p18_callbacks_host");
        List<WeakReference<Throwable>> failures = new ArrayList<>();
        try {
            for (int i = 0; i < 50; i++) {
                failures.add(exercise(re, i));
                failures.add(exerciseHostRethrow(host, i));
                Object recovered = LtRt.wcall(re, "sub", "x", "y", "x");
                if (!"y".equals(recovered)) throw new AssertionError("recovery failed");
            }
            for (int retry = 0; retry < 80; retry++) {
                System.gc();
                LtRt.wcall(re, "sub", "x", "y", "x");
                boolean allGone = true;
                for (WeakReference<Throwable> ref : failures)
                    if (ref.get() != null) { allGone = false; break; }
                if (allGone) break;
                Thread.sleep(20);
            }
            for (WeakReference<Throwable> ref : failures)
                if (ref.get() != null)
                    throw new AssertionError("callback Throwable remains strongly reachable");
            System.out.println("same-Throwable-and-stack-50x; host-rethrow-identity-50x; recovery-50x; error-registry-clean");
        } catch (Throwable failure) {
            failure.printStackTrace(System.err);
            System.exit(2);
        } finally {
            LtRt.shutdown();
        }
    }
    }'''
    return run_java_helper('say "fixture-ready"\n', "ThrowableIdentity", java_text,
                           "same-Throwable-and-stack-50x; host-rethrow-identity-50x; recovery-50x; error-registry-clean\n")


def test_java_opaque_gc_and_daemon_stop() -> bool:
    java_text = r'''import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;
public class OpaqueLifecycle {
    static final class Dropped {
        final String key;
        final WeakReference<LtRt.PyHandle> weak;
        Dropped(String key, WeakReference<LtRt.PyHandle> weak) { this.key = key; this.weak = weak; }
    }
    static Dropped make(Object module) {
        LtRt.PyHandle handle = (LtRt.PyHandle)LtRt.wcall(module, "opaque_value");
        String key = handle.session + ":" + handle.id;
        WeakReference<LtRt.PyHandle> weak = new WeakReference<>(handle);
        return new Dropped(key, weak);
    }
    static Field field(Class<?> type, String name) throws Exception {
        Field value = type.getDeclaredField(name); value.setAccessible(true); return value;
    }
    public static void main(String[] args) throws Exception {
        Class<?> daemonType = Class.forName("LtRt$Daemon");
        Field instanceField = field(daemonType, "instance");
        Field countsField = field(daemonType, "HANDLE_COUNTS");
        Field refsField = field(daemonType, "HANDLE_REFS");
        Object module = LtRt.pymod("p18_callbacks_host");
        Dropped dropped = make(module);
        Map<?, ?> counts = (Map<?, ?>)countsField.get(null);
        if (!Integer.valueOf(1).equals(counts.get(dropped.key)))
            throw new AssertionError("opaque handle not tracked once");
        LtRt.wcall(module, "identity", "gc-trigger");
        for (int retry = 0; retry < 80 && counts.containsKey(dropped.key); retry++) {
            System.gc(); Thread.sleep(20);
            LtRt.wcall(module, "identity", "gc-trigger");
        }
        if (dropped.weak.get() != null || counts.containsKey(dropped.key))
            throw new AssertionError("opaque handle not released after GC");

        LtRt.PyHandle retained = (LtRt.PyHandle)LtRt.wcall(module, "opaque_value");
        long session = retained.session;
        Object daemon = instanceField.get(null);
        if (daemon == null) throw new AssertionError("daemon was not started");
        Field closedField = field(daemonType, "closed");
        Field procField = field(daemonType, "proc");
        Process child = (Process)procField.get(daemon);
        LtRt.shutdown();
        LtRt.shutdown();
        if (!Boolean.TRUE.equals(closedField.get(daemon)))
            throw new AssertionError("daemon close flag not set");
        if (instanceField.get(null) != null)
            throw new AssertionError("shutdown retained daemon singleton");
        if (child != null && !child.waitFor(3, TimeUnit.SECONDS))
            throw new AssertionError("daemon child did not stop");
        for (Object key : counts.keySet())
            if (key.toString().startsWith(session + ":"))
                throw new AssertionError("shutdown retained opaque handle count");
        Set<?> refs = (Set<?>)refsField.get(null);
        for (Object ref : refs) {
            Field sessionField = field(ref.getClass(), "session");
            if (sessionField.getLong(ref) == session)
                throw new AssertionError("shutdown retained opaque weak handle ref");
        }
        try {
            LtRt.wcall(retained, "label");
            throw new AssertionError("old-daemon handle unexpectedly remained usable");
        } catch (RuntimeException expected) {
            if (!expected.getMessage().contains("stale Python object handle")) throw expected;
        }
        System.out.println("opaque-gc-released; daemon-stop-idempotent; stale-session-rejected");
    }
}'''
    return run_java_helper('say "fixture-ready"\n', "OpaqueLifecycle", java_text,
                           "opaque-gc-released; daemon-stop-idempotent; stale-session-rejected\n")


def test_java_malicious_frames() -> list[bool]:
    """Inject one exact malformed frame at a time and check rejection/cleanup."""
    cases = (
        ("wrong-parent", "callback", "parent",
         "unknown callback token, duplicate ID, or parent mismatch", False),
        ("unknown-callback", "callback", "callback",
         "unknown callback token, duplicate ID, or parent mismatch", False),
        ("wrong-response", "response", "id",
         "Python response ID/parent mismatch", True),
        ("unknown-error", "response", "callback_error",
         "unknown or expired callback error ID", True),
        ("eof", "callback", None, "python daemon died", False),
    )
    results: list[bool] = []
    with tempfile.TemporaryDirectory(prefix="latent-p18-faults-") as tmp:
        base = Path(tmp)
        outdir = base / "out"
        callback_source = base / "fault_callback.lt"
        callback_source.write_text(
            'host = py "p18_callbacks_host"\n'
            "fn callback(value):\n"
            '    say "CALLBACK_BODY_RAN"\n'
            '    return "callback-ok"\n'
            'say host.invoke_one(callback)\n', encoding="utf-8")
        error_source = base / "fault_callback_error.lt"
        error_source.write_text(
            'host = py "p18_callbacks_host"\n'
            "fn callback(value):\n"
            '    say "CALLBACK_BODY_RAN"\n'
            "    if true:\n"
            '        throw "callback-fault"\n'
            'say host.invoke_one(callback)\n', encoding="utf-8")
        sources = (callback_source, error_source)
        for source in sources:
            compiled = compile_java_fixture(source, outdir)
            if compiled.returncode != 0:
                print(f"FAIL Java malicious-frame fixture {source.stem}: compile failed")
                print(f"  stderr={compiled.stderr[-1200:]!r}")
                return [False] * len(cases)
        daemon = base / "fault_daemon.py"
        daemon.write_text(
            "import json, os\nfrom pathlib import Path\n"
            "ns = {'__name__': 'p18_fault_daemon'}\n"
            f"source = Path({str(ROOT / 'runtime' / 'ltpy.py')!r})\n"
            "exec(compile(source.read_text(), str(source), 'exec'), ns)\n"
            "original = ns['_protocol_send']\nfired = False\n"
            "fault = os.environ.get('P18_FAULT', '')\n"
            "Path(os.environ['P18_DAEMON_PID']).write_text(str(os.getpid()))\n"
            "def send(frame):\n"
            "    global fired\n"
            "    if not fired:\n"
            "        if frame.get('kind') == 'callback' and fault in ('wrong-parent', 'unknown-callback', 'eof'):\n"
            "            fired = True\n"
            "            before = dict(frame)\n"
            "            if fault == 'wrong-parent': frame['parent'] += 999\n"
            "            elif fault == 'unknown-callback': frame['callback'] += 999\n"
            "            elif fault == 'eof':\n"
            "                Path(os.environ['P18_SNAPSHOT']).write_text(json.dumps({'fault': fault, 'before': before, 'injected': before, 'fired': True}))\n"
            "                os._exit(7)\n"
            "            Path(os.environ['P18_SNAPSHOT']).write_text(json.dumps({'fault': fault, 'before': before, 'injected': frame, 'fired': True}))\n"
            "        elif frame.get('kind') == 'response' and fault == 'wrong-response':\n"
            "            fired = True; before = dict(frame); frame['id'] += 999\n"
            "            Path(os.environ['P18_SNAPSHOT']).write_text(json.dumps({'fault': fault, 'before': before, 'injected': frame, 'fired': True}))\n"
            "        elif frame.get('kind') == 'response' and fault == 'unknown-error' and 'callback_error' in frame.get('payload', {}):\n"
            "            fired = True; before = json.loads(json.dumps(frame)); frame['payload']['callback_error'] += 999\n"
            "            Path(os.environ['P18_SNAPSHOT']).write_text(json.dumps({'fault': fault, 'before': before, 'injected': frame, 'fired': True}))\n"
            "    original(frame)\n"
            "ns['_protocol_send'] = send\n"
            "try:\n    ns['main']()\n"
            "finally:\n    Path(os.environ['P18_DAEMON_DONE']).write_text('done')\n",
            encoding="utf-8")
        for fault, kind, changed_field, marker, body_runs in cases:
            source = error_source if fault in ("wrong-response", "unknown-error") else callback_source
            pid_file = base / f"{fault}.pid"
            snapshot_file = base / f"{fault}.json"
            done_file = base / f"{fault}.done"
            env = dict(ENV, LATENT_PY=str(daemon), P18_FAULT=fault,
                       P18_DAEMON_PID=str(pid_file),
                       P18_SNAPSHOT=str(snapshot_file),
                       P18_DAEMON_DONE=str(done_file))
            result = run(["java", "-cp", str(outdir), java_class_name(source.stem)],
                         cwd=outdir, timeout=12, env=env)
            snapshot = {}
            if snapshot_file.exists():
                try:
                    snapshot = json.loads(snapshot_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass
            before = snapshot.get("before", {})
            injected = snapshot.get("injected", {})
            frame_ok = (snapshot.get("fault") == fault and snapshot.get("fired") is True
                        and before.get("kind") == kind and injected.get("kind") == kind)
            if changed_field is None:
                frame_ok = frame_ok and before == injected
            elif changed_field == "callback_error":
                frame_ok = (frame_ok and
                            before.get("payload", {}).get("callback_error") !=
                            injected.get("payload", {}).get("callback_error") and
                            {k: v for k, v in before.items() if k != "payload"} ==
                            {k: v for k, v in injected.items() if k != "payload"} and
                            {k: v for k, v in before.get("payload", {}).items() if k != "callback_error"} ==
                            {k: v for k, v in injected.get("payload", {}).items() if k != "callback_error"})
            else:
                frame_ok = (frame_ok and before.get(changed_field) != injected.get(changed_field)
                            and {k: v for k, v in before.items() if k != changed_field} ==
                            {k: v for k, v in injected.items() if k != changed_field})
            daemon_stopped = False
            if pid_file.exists():
                pid_text = pid_file.read_text(encoding="utf-8").strip()
                for _ in range(20):
                    cmdline = Path(f"/proc/{pid_text}/cmdline")
                    if not cmdline.exists() or str(daemon) not in cmdline.read_bytes().decode("utf-8", "replace"):
                        daemon_stopped = True
                        break
                    import time
                    time.sleep(0.05)
            body_ok = ("CALLBACK_BODY_RAN" in result.stdout) if body_runs else (
                "CALLBACK_BODY_RAN" not in result.stdout)
            ok = (result.returncode not in (0, "timeout") and
                  marker in result.stderr and frame_ok and body_ok and
                  pid_file.exists() and daemon_stopped and
                  "KeyError" not in result.stderr)
            if ok:
                print(f"PASS java malformed frame {fault}: exact injection, diagnostic, body, cleanup")
            else:
                print(f"FAIL java malicious frame {fault}: rc={result.returncode}")
                print(f"  stdout={result.stdout[-800:]!r}")
                print(f"  stderr={result.stderr[-1400:]!r}")
                print(f"  snapshot={snapshot!r} pid={pid_file.exists()} daemon_stopped={daemon_stopped} done={done_file.exists()}")
            results.append(ok)
    return results


def main() -> int:
    checks: list[tuple[str, bool]] = []
    ok = test_python_callback_protocol_ids()
    checks.append(("Python callback protocol malformed IDs", ok))
    if ok:
        print("PASS Python callback protocol malformed IDs")
    positive = TESTS / "p18_callbacks.lt"
    print("== P18 callbacks: same .lt on Python and Java targets ==")
    for target in TARGETS:
        ok = run_single(positive, target, "p18_callbacks", EXPECTED_POSITIVE)
        checks.append((f"{target} p18_callbacks", ok))
        if ok:
            print(f"PASS {target} p18_callbacks")

    print("== P18 callbacks: recovery after callback/host failures ==")
    for name, (source_text, expected) in RECOVERY_CASES.items():
        with tempfile.TemporaryDirectory(prefix="latent-p18-source-") as tmp:
            source = Path(tmp) / f"{name}.lt"
            source.write_text(source_text, encoding="utf-8")
            for target in TARGETS:
                ok = run_single(source, target, name, expected)
                checks.append((f"{target} {name}", ok))
                if ok:
                    print(f"PASS {target} {name}")

    print("== P18 callbacks: failures, cleanup, and explicit rejection boundaries ==")
    for name, (source_text, markers, absent) in ERROR_CASES.items():
        with tempfile.TemporaryDirectory(prefix="latent-p18-source-") as tmp:
            source = Path(tmp) / f"{name}.lt"
            source.write_text(source_text, encoding="utf-8")
            for target in TARGETS:
                expected_markers = markers
                if name == "host_returns_callback":
                    expected_markers = ("callbacks cannot return Latent callable values",)
                elif name == "callback_callable_consumed_immediately":
                    expected_markers = (
                        ("callbacks cannot return Latent callable values",)
                        if target == "py" else
                        ("returning a Latent callback is unsupported",))
                ok = run_single(source, target, name, None,
                                expected_markers, absent)
                checks.append((f"{target} {name}", ok))
                if ok:
                    print(f"PASS {target} {name}")

    # Java host methods are not callback-capable; passing a Latent function to
    # Fixture.accept(local) remains an explicit negative compatibility case.
    old_static_case = TESTS / "p14_negative" / "static_callback.lt"
    for target in TARGETS:
        marker = "Latent callable values cannot be passed to Python/Java interop calls"
        ok = run_single(old_static_case, target, "p14_static_callback", None,
                        (marker,))
        checks.append((f"{target} p14_static_callback (negative)", ok))
        if ok:
            print(f"PASS {target} p14_static_callback")

    print("== P18 callbacks: Java Throwable identity and callback error cleanup ==")
    ok = test_java_throwable_identity_and_error_cleanup()
    checks.append(("Java Throwable identity/error cleanup", ok))
    if ok:
        print("PASS Java Throwable identity/error cleanup")

    print("== P18 callbacks: Java opaque GC and daemon stop lifecycle ==")
    ok = test_java_opaque_gc_and_daemon_stop()
    checks.append(("Java opaque GC/daemon stop lifecycle", ok))
    if ok:
        print("PASS Java opaque GC/daemon stop lifecycle")

    print("== P18 callbacks: native fd 0/1 cannot consume or corrupt protocol ==")
    fd_results = test_native_fd_callback_isolation()
    checks.extend((f"native fd isolation {index + 1}", result)
                  for index, result in enumerate(fd_results))

    print("== P18 callbacks: Java malicious protocol frame fault injection ==")
    fault_results = test_java_malicious_frames()
    checks.extend((f"Java malicious-frame fault {index + 1}", result)
                  for index, result in enumerate(fault_results))

    passed = sum(1 for _, result in checks if result)
    failed = len(checks) - passed
    print(f"P18 callback tests: {passed}/{len(checks)} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
