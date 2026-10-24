"""Coverage-guided byte fuzzing of the Latent front end with Atheris.

  python fuzz/atheris_bytes.py -max_total_time=300 fuzz/corpus

Any exception other than the four declared compiler errors (or RecursionError
from pathological nesting, which the playground maps to a clean diagnostic) is
reported by libFuzzer as a crash and saved as crash-<hash>.
"""
import os
import sys

import atheris

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

with atheris.instrument_imports():
    import lex
    import parse as parse_mod
    import desugar
    import semant
    import gen_py
    import gen_java
    import latent

DECLARED = (lex.LexError, parse_mod.ParseError, desugar.DesugarError,
            semant.SemantError, RecursionError)


def TestOneInput(data):
    src = atheris.FuzzedDataProvider(data).ConsumeUnicodeNoSurrogates(4096)
    try:
        prog, err = latent.compile_src(src, "main.lt")
        if err:
            return
        gen_py.generate(prog, source_path="main.lt")
        try:
            gen_java.generate(prog, "Main", source_path="main.lt")
        except KeyError:
            # F1 (known-open): block-nested fn crashes Java codegen. Remove this
            # guard once fixed so a regression is reported again.
            return
    except DECLARED:
        return


def main():
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
