# P18: synchronous Python-host callbacks

Callbacks are **borrowed synchronous callables**: the host may invoke them with positional arguments during the active same-thread scope. The delivered positional sequence is passed through the existing Latent binder. Argument provenance inside Python or native wrappers is not part of the callback contract. Test results and reproducibility evidence are recorded separately from this contract.

**Current follow-on status (2026-10-25):** P19-B allows explicit `name=value` arguments on existing Python-host call paths, including eligible extracted P13/P14 callable values. Java-host named calls and `*`/`**` expansion remain unsupported. Keywords delivered by Python to a borrowed Latent callback remain unsupported under the unimplemented P19-C scope.

## One program, both targets

The slice implements **Latent → Python host API → Latent function**. The Java target uses its existing Python child process and a same-thread, re-entrant request pump. It does not add Java SAM/interface callbacks or reverse callbacks through the Python target's Java daemon.

```latent
rx = py "re"
fn make_replacement(prefix):
    count = 0
    fn replacement(match):
        nonlocal count
        count = count + 1
        return prefix + str(count) + ":" + match.group(0)
    return replacement
replacement = make_replacement("#")
say rx.sub("[a-z]+", replacement, "a bb")
say rx.sub("[a-z]+", replacement, "c")
sub = rx.sub
say sub("[a-z]+", replacement, "dd")
```

Expected output is `#1:a #2:bb`, `#3:c`, then `#4:dd`. Closure state survives between calls. `match.group(0)` executes against the actual opaque Match object; on Java it makes a nested request while the original callback is still pending. Direct and extracted compiled-regex methods are also covered.

Direct positional Latent functions, including Latent bound methods, use the normal Latent binder. Default arguments, rest arguments, arity checking and closure bindings are not reimplemented in the bridge. Python host wrappers remain distinct from Latent callback functions. Java-host callbacks remain unsupported runtime-negative cases.

## Lifetime and conversion

Each callback-capable host call owns a short-lived scope. Invocation is restricted to its creating thread and active lifetime. Success and failure revoke the adapters; expired or retained adapters cannot execute the Latent function. Java request-local callback/error registries are cleared in `finally` paths, and daemon shutdown terminates transport and clears handle tracking. Opaque host objects that are deliberately retained in Latent remain usable until collected or daemon shutdown; they are not invalidated merely because the callback returned.

The bridge preserves existing bool/nil/string/number conversion rules. Host integers become Latent numbers; bool is not treated as a number; integral outbound floats become Python integers except negative zero. Exact builtin tuple/list containers are projected to lists. Opaque objects such as `re.Match` are not stringified. Callback containers have cycle checks and a depth bound of 64; callback/RPC nesting is bounded at 16. Arbitrary container identity sharing, cyclic graphs, and non-string map-key equivalence are not new compatibility promises.

Deferred/generator/awaitable values and callable callback results are validated **before the host can consume the callback result**, including nested builtin containers. Returning an adapter, hiding callbacks in argument containers, asynchronous/deferred use, cross-thread execution, and stale callback execution are rejected. P19-B later enabled explicit `name=value` on existing Python-host call paths; Java-host named calls and `*`/`**` expansion remain rejected. Actual keyword arguments delivered to a callback remain rejected pending P19-C.

## Errors and protocol integrity

Java waits for the host result while dispatching callback frames on the original calling thread. Python waits for a callback result while servicing nested requests on that same thread. Request IDs, parent IDs, callback tokens, invocation IDs, error IDs, directions and frame schemas are checked. Malformed frames abort the affected daemon rather than being treated as ordinary host exceptions. User maps have an explicit data envelope and are not control frames.

An uncaught callback failure is rethrown as its original Java `Throwable`, with its Latent source stack retained, using a request-local error ID. The Python host sees an `Exception` carrier with the original message, so broad `except Exception`, swallowing, rethrowing and message-based transformation work. If the host transforms the failure into a new host exception, the outer host call is located; the bridge does not fabricate a callback-body location for that new error.

The daemon duplicates protocol pipes to private, non-inheritable descriptors before isolating normal IO. Descriptor 0 reads from the null device and descriptor 1 writes to stderr; Python-level stdin/stdout are also separated from protocol streams. Native `os.read(0, ...)`, `os.write(1, ...)`, ordinary print/input paths and nested callbacks cannot consume or inject protocol frames through normal standard IO. The request pump owns the actual framing streams, with no competing per-callback reader or writer.

**This is trusted-host IO isolation, not a Python security sandbox.** Host code deliberately inspecting process descriptors or daemon internals can reach private transport capabilities. Arbitrary in-process Python cannot be treated as an adversarial sandboxed party. The implementation does not claim otherwise.

## Host contract and limits

### Positional arguments have no provenance restriction

A callback receives an already-bound positional tuple. Direct `callback(value)`, Python `callback(*args)`, and native wrappers all deliver that sequence to the normal Latent binder. Default values, variadic arguments and arity errors follow the existing language rules. No bytecode or caller-syntax heuristic is used. For example, this host call is supported while the borrowed callback is active:

```python
from functools import partial

def host(callback):
    return partial(callback, *("x",))()
```

Python/native wrappers may obscure how the positional values were assembled; the bridge neither detects nor claims to reject hidden expansion provenance. P19-B permits explicit named **Python-host** entry arguments on the existing supported call paths; Java-host named arguments and `*`/`**` expansion remain rejected. Actual keyword arguments delivered to a callback, including nonempty `**kwargs`, are unsupported and rejected pending P19-C. Stale, cross-thread, deferred and nested/returned-callable boundaries are unchanged.

### Host-specific exception classes are not universally identical

Broad host exception handling and original-message behavior are supported. A Java callback's Python-side carrier cannot generally be the same Python exception class that the Python-target callback would have raised. Arbitrary host handlers such as `except IndexError` may therefore behave differently. No arbitrary type-specific catch equivalence is claimed; preserving the original Java throwable and `.lt` traceback does not prove that equivalence.

### Arbitrary host storage is invisible at storage time

A host can store a callback in a field, closure or queue and return `None`; the bridge cannot observe arbitrary storage as it happens. The enforceable guarantee is rejection of execution after scope expiry, not detection of the storage action itself. Hosts can also swallow exceptions, keep their own data, or fail to terminate. These are not overridden by the callback bridge.

## Bootstrap and regression boundaries

The Python bootstrap emitter implements the same callback-result checks and preserves the original P13/P14 callable classification. It does not permit arbitrary callable fields, descriptors, static/class methods or opaque callable objects merely because Python considers them callable.

Fixed Stage0 is still the old seed. The only declared existing-case **P18** behavior delta is `p14_negative/module_callback`: Stage0 rejects the callback argument before entering the host; Stage1/2 permit the direct callback argument but reject the returned adapter, with exact status, output, diagnostic and source-location expectations. P19-B has separate `p19_stage0_deltas` (three exact IDs) and one `p19_stage1_features` sample in `tests/bootstrap/manifest.json`; these do not replace the P18 delta. Wrapper/container callback negatives and all unrelated baseline comparisons remain strict. Java-host `static_callback` stays in `NEG_RUNTIME` and is not a delta.

Relevant commands are:

```sh
python3 tests/test_p18_callbacks.py
python3 tests/run_tests.py
python3 tests/bootstrap/check_supported_runtime.py
python3 tests/bootstrap/checker.py
python3 tests/bootstrap/test_checker.py
python3 tests/bootstrap/project_stage0.py --report /tmp/p18-projection.json
python3 -m unittest tests.test_latent_stage1 tests.test_bootstrap_host tests.test_bootstrap_lexer tests.test_bootstrap_parser -v
python3 -m unittest tests.test_bootstrap_smoke -v
python3 tests/bootstrap/pending_snapshot_gate.py
```

The pending-source gate snapshots the actual modified/untracked source in two roots and checks both Stage2/3 fixed points and cross-root byte equality without making a commit. The existing clean-clone smoke check is unchanged: before a commit it validates the existing committed HEAD, not pending files. Both the final pending-source gate before commit and the unchanged clean-clone check on the actual new HEAD after commit must be recorded; the old HEAD result must not be presented as evidence for pending files.

The Java unconditional-throw/unreachable-statement generation bug found during P18-B was fixed in subsequent Java generator changes. Abrupt-control-flow handling in `gen_java.py` and `tests/test_audit_java_fixes.py::TestAuditJavaFixes.test_abrupt_control_flow_and_catch_name_isolation` cover the regression; it is historical, not an open limitation. The callback error tests continue to exercise real failures and `.lt` locations.
