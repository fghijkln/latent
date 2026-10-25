# Latent fuzzing

Bounded compiler fuzzing and a deterministic security probe for the Latent compiler and playground:

- **`hypothesis_grammar.py`** — property-based testing with [Hypothesis](https://hypothesis.readthedocs.io/). A recursive strategy builds grammatically plausible Latent programs; Hypothesis shrinks failures to minimal reproducers and remembers them in its example database.
  - **no-crash**: valid programs reach both code generators; any unexpected exception is a failure. Ordinary source rejection is returned by `compile_src` and is not treated as a crash.
  - **backend-agree**: for programs that both code generators accept and the Python playground runs cleanly, Java compilation must succeed, Java must exit with status 0, and status, stdout and stderr must match the Python result. Compiler failures and nonzero exits are never skipped.
- **`test_known_findings.py`** — hard regressions for the fixed R1 bool-arithmetic escape and F1 nested-function Java codegen bugs. The escape probe follows the R1 `__subclasses__()` path to request the process UID (`uid=`); it is read-only, performs no command execution or file/network writes, and verifies the playground rejects the payload before the marker can appear.
- **`test_number_format_scan.py`** — deterministic Python/Java output comparison over both N6 notation intervals (`[1e-6, 1e-4)` and `[1e16, 1e17)`), including positive/negative values and floats adjacent to each boundary.
- **`atheris_bytes.py`** — coverage-guided raw-byte fuzzing with [Atheris](https://github.com/google/atheris) (Google's libFuzzer for Python). It feeds bounded inputs to the compiler and both code generators; all unexpected exceptions, including `KeyError`, are reported as crashes. Every PR runs a 5-second smoke; nightly/manual runs are bounded to 30 minutes. Needs a CPython version with an Atheris wheel (3.11 / 3.12).

The PR workflow runs the hard regressions, escape probe, N6 boundary scan, bounded Hypothesis profile and 5-second Atheris smoke for every pull request. The separate 30-minute Atheris job only runs nightly or by manual dispatch. The Java backend needs a JDK (21 in CI).

## Running

```bash
pip install -r fuzz/requirements.txt

# deterministic, bounded PR profile:
HYPOTHESIS_PROFILE=ci python -m pytest fuzz/hypothesis_grammar.py -q
python -m pytest fuzz/test_known_findings.py -q
python -m pytest fuzz/test_number_format_scan.py -q
python fuzz/atheris_bytes.py -max_total_time=5 \
  -artifact_prefix=crashes/ fuzz/corpus

# larger bounded nightly exploration:
HYPOTHESIS_PROFILE=nightly FUZZ_DIFF_EXAMPLES=1500 \
  python -m pytest fuzz/hypothesis_grammar.py -q

# coverage-guided byte fuzzing for a bounded number of seconds:
python fuzz/atheris_bytes.py -max_total_time=300 fuzz/corpus
```
