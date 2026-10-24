# Latent fuzzing

Established-tool fuzzing for the Latent compiler and playground. Two harnesses:

- **`hypothesis_grammar.py`** — property-based testing with
  [Hypothesis](https://hypothesis.readthedocs.io/). A recursive strategy builds
  grammatically plausible Latent programs; Hypothesis shrinks any failure to a
  minimal reproducer and remembers it in its example database. Three properties:
  - **no-crash**: `latent.compile_src` + `gen_py.generate` + `gen_java.generate`
    never raise anything outside the four declared error types
    (`LexError`/`ParseError`/`DesugarError`/`SemantError`). An undeclared
    exception is a compiler bug.
  - **backend-agree**: a program that runs clean on the Python backend produces
    byte-identical stdout on the Java backend (error messages normalized).
  - **no-escape**: a program never prints an `os`/`uid=` escape marker through
    the playground's `run_program`.
- **`atheris_bytes.py`** — coverage-guided raw-byte fuzzing with
  [Atheris](https://github.com/google/atheris) (Google's libFuzzer for Python).
  Feeds arbitrary bytes to `compile_src` and flags any undeclared exception.
  Nightly only; needs a CPython that Atheris ships a wheel for (3.11 / 3.12).

Everything runs locally and offline. The Java backend needs a JDK (21 in CI).

## Running

```bash
pip install -r fuzz/requirements.txt          # hypothesis (+ atheris on 3.11/3.12)

# property suite, fixed-seed (CI PR gate, ~1 min):
HYPOTHESIS_PROFILE=ci python -m pytest fuzz/hypothesis_grammar.py -q

# longer random exploration (nightly):
HYPOTHESIS_PROFILE=nightly python -m pytest fuzz/hypothesis_grammar.py -q

# coverage-guided byte fuzzing for N seconds (nightly):
python fuzz/atheris_bytes.py -max_total_time=300 fuzz/corpus
```

## Known-open findings

These are pinned as `xfail` so the suite is green until each is fixed; flip to a
hard assertion when the fix lands.

- **R1 (CRITICAL)** — `true + false` / `-true` yield a native Python `int` on the
  Python backend, which `_wv_non_handle_type` does not guard, giving
  `__class__.__base__.__subclasses__()` -> `os.system`. `fuzz/corpus/r1_*`.
- **F1 (medium)** — a `fn` defined inside any block (`if`/`for`/`while`/`try`)
  crashes the Java backend with an uncaught `KeyError` at `gen_java.py`
  (`_prepare_functions` registers only top-level and class-method defs). The
  Python backend compiles the same program. `fuzz/corpus/f1_*`.
- **F2** — the bool/native arithmetic divergence class behind R1: ~19 expressions
  (e.g. `-true`, `1 ** true`) return a number on Python and raise on Java.
  Closed by the same fix as R1.
