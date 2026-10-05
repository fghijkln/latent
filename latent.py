#!/usr/bin/env python3
"""latent - the Latent compiler.  latent prog.lt -t py|java [-o OUTDIR] [--run]"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lex
import parse as parse_mod
import desugar
import semant
import gen_py
import gen_java


def compile_src(src, path="<src>"):
    try:
        toks = lex.lex(src)
    except lex.LexError as e:
        return None, f"lex error: {e}"
    try:
        prog = parse_mod.parse(toks)
    except parse_mod.ParseError as e:
        return None, f"parse error: {e}"
    try:
        prog = desugar.desugar(prog)
    except desugar.DesugarError as e:
        return None, f"desugar error: {e}"
    try:
        prog = semant.check(prog)
    except semant.SemantError as e:
        return None, f"semant error: {e}"
    return prog, None


def main():
    ap = argparse.ArgumentParser(prog="latent", description="Latent compiler")
    ap.add_argument("src", help=".lt source file")
    ap.add_argument("-t", "--target", choices=["py", "java"], default="py")
    ap.add_argument("-o", "--outdir", default=None)
    ap.add_argument("--run", action="store_true", help="compile and run")
    args = ap.parse_args()

    with open(args.src, encoding="utf-8") as f:
        src = f.read()
    prog, err = compile_src(src, args.src)
    if err:
        print(f"{args.src}: {err}", file=sys.stderr)
        return 1

    stem = os.path.splitext(os.path.basename(args.src))[0]
    outdir = os.path.abspath(args.outdir or os.path.join(HERE, "out"))
    os.makedirs(outdir, exist_ok=True)

    if args.target == "py":
        code = gen_py.generate(prog)
        out = os.path.join(outdir, stem + ".py")
        with open(out, "w", encoding="utf-8") as f:
            f.write(code)
        # JVM daemon sources for `java ...` handles; compiled lazily
        # (at latent time if javac exists, otherwise on first use by the .py)
        for rt in ("LtJavaDaemon.java", "JReflect.java", "LtRt.java"):
            shutil.copy(os.path.join(HERE, "runtime", rt), outdir)
        r = None
        try:
            r = subprocess.run(
                ["javac", "-d", outdir, "-cp", outdir] +
                [os.path.join(outdir, s) for s in
                 ("LtJavaDaemon.java", "JReflect.java", "LtRt.java")],
                capture_output=True, text=True)
        except OSError:
            r = None  # no javac at all: pure-python output still works;
                      # the JVM daemon compiles on first `java` use instead
        if r is None or r.returncode != 0:
            print("note: javac unavailable/failed; the JVM daemon will be "
                  "compiled on first `java` use if javac exists then",
                  file=sys.stderr)
        print(f"wrote {out}")
        if args.run:
            r = subprocess.run([sys.executable, out])
            return r.returncode
    else:
        cls = gen_java.cls_name(stem)
        code = gen_java.generate(prog, cls)
        main_java = os.path.join(outdir, cls + ".java")
        with open(main_java, "w", encoding="utf-8") as f:
            f.write(code)
        shutil.copy(os.path.join(HERE, "runtime", "LtRt.java"), outdir)
        shutil.copy(os.path.join(HERE, "runtime", "JReflect.java"), outdir)
        shutil.copy(os.path.join(HERE, "runtime", "ltpy.py"), outdir)
        print(f"wrote {main_java} (+ LtRt.java, JReflect.java, ltpy.py)")
        try:
            r = subprocess.run(["javac", "-d", outdir, "-cp", outdir,
                                main_java, os.path.join(outdir, "LtRt.java"),
                                os.path.join(outdir, "JReflect.java")],
                               capture_output=True, text=True)
        except OSError:
            print("error: javac not found; the java target needs a JDK to "
                  "compile. Install one, or use -t py.", file=sys.stderr)
            return 1
        if r.returncode != 0:
            print(r.stdout, r.stderr, file=sys.stderr)
            return 1
        # compile classes into a separate dir so ltpy.py lookup via
        # code-source dir still works? No - keep classes next to sources:
        if args.run:
            r = subprocess.run(["java", "-cp", outdir, cls], cwd=outdir)
            return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
