#!/usr/bin/env python3
"""latent - the Latent compiler.  latent prog.lt -t py|java [-o OUTDIR] [--run]"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lex
import nodes
import parse as parse_mod
import desugar
import semant
import module_system


def compile_src(src, path="<src>"):
    """Returns (prog, None) on success, (None, (kind, raw_msg)) on failure.
    kind is one of lex/parse/desugar/semant; raw_msg starts with 'line:col: '."""
    try:
        toks = lex.lex(src)
    except lex.LexError as e:
        return None, ("lex", str(e))
    try:
        prog = parse_mod.parse(toks)
    except parse_mod.ParseError as e:
        return None, ("parse", str(e))
    try:
        prog = desugar.desugar(prog)
    except desugar.DesugarError as e:
        return None, ("desugar", str(e))
    try:
        prog = semant.check(prog)
    except semant.SemantError as e:
        return None, ("semant", str(e))
    return prog, None


_ERR_HEAD = re.compile(r"^(\d+):(\d+):\s*(.*)$", re.DOTALL)


def render_error(path, src, kind, raw_msg):
    """Render a compile error with the offending source line and a caret."""
    m = _ERR_HEAD.match(raw_msg)
    if m:
        line, col, msg = int(m.group(1)), int(m.group(2)), m.group(3)
        srclines = src.split("\n")
        if 1 <= line <= len(srclines):
            text = srclines[line - 1]
            gutter = f"    {line} | "
            caret = " " * (len(gutter) + min(col, len(text) + 1) - 1) + "^"
            return (f"{path}:{line}:{col}: {kind} error: {msg}\n"
                    f"{gutter}{text}\n{caret}")
    # no position info (shouldn't happen for the four stages): plain fallback
    return f"{path}: {kind} error: {raw_msg}"


def _needs_more(buf):
    """True while the buffer holds an open block. In Latent a physical
    line ending with ':' is always a block header (fn/if/while/for/try/
    catch/elif/else/class), so any such line means: keep reading until
    a blank line ends the block."""
    return any(line.strip().endswith(":") for line in buf)


def _exec_chunk(chunk, ns, checker):
    """Compile one REPL chunk (py backend) and exec it in ns."""
    prog = None
    try:  # a bare expression: auto-print its value
        toks = lex.lex(chunk)
        p = parse_mod.Parser(toks)
        e = p.expr()
        if p.peek().kind in ("NEWLINE", "EOF"):
            prog = nodes.Program([nodes.Say(e, line=e.line, col=e.col)],
                                 line=e.line, col=e.col)
    except (lex.LexError, parse_mod.ParseError):
        prog = None
    if prog is None:
        try:
            prog = parse_mod.parse(lex.lex(chunk))
        except lex.LexError as e:
            print(render_error("<repl>", chunk, "lex", str(e)),
                  file=sys.stderr)
            return
        except parse_mod.ParseError as e:
            print(render_error("<repl>", chunk, "parse", str(e)),
                  file=sys.stderr)
            return
    try:
        prog = desugar.desugar(prog)
    except desugar.DesugarError as e:
        print(render_error("<repl>", chunk, "desugar", str(e)),
              file=sys.stderr)
        return
    # REPL allows redefinition: forget previous defs from this chunk first
    for s in prog.stmts:
        if isinstance(s, (nodes.FnDef, nodes.ClassDef)):
            checker.functions.pop(s.name, None)
            checker.classes.pop(s.name, None)
            checker.globals.discard(s.name)
    try:
        checker.run(prog)
    except semant.SemantError as e:
        print(render_error("<repl>", chunk, "semant", str(e)),
              file=sys.stderr)
        return
    g = gen_py.Gen()
    for s in prog.stmts:
        if isinstance(s, nodes.FnDef):
            g.fndef(s)
        elif isinstance(s, nodes.ClassDef):
            g.classdef(s)
        else:
            g.stmt(s)
    try:
        exec(compile("\n".join(g.out) + "\n", "<repl>", "exec"), ns)
    except Exception:
        traceback.print_exc()


def run_repl():
    try:
        import readline  # noqa: F401  (history + line editing)
    except ImportError:
        pass
    ns = {}
    checker = semant.Checker()

    def reset():
        ns.clear()
        exec(gen_py.PRELUDE, ns)
        checker.__init__()

    reset()
    print("Latent REPL (python backend). Blank line ends a block; "
          ":reset clears; :quit exits.")
    buf = []
    while True:
        try:
            line = input("lt> " if not buf else "... ")
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            buf = []
            continue
        stripped = line.strip()
        if not buf and stripped in (":quit", ":q", "exit"):
            return 0
        if not buf and stripped == ":reset":
            reset()
            print("(state cleared)")
            continue
        if stripped == "":
            if buf:
                chunk, buf = "\n".join(buf), []
                _exec_chunk(chunk, ns, checker)
            continue
        buf.append(line)
        if not _needs_more(buf):
            chunk, buf = "\n".join(buf), []
            _exec_chunk(chunk, ns, checker)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "repl":
        return run_repl()
    ap = argparse.ArgumentParser(prog="latent", description="Latent compiler")
    ap.add_argument("src", help=".lt source file")
    ap.add_argument("-t", "--target", choices=["py", "java"], default="py")
    ap.add_argument("-o", "--outdir", default=None)
    ap.add_argument("--run", action="store_true", help="compile and run")
    diagnostics = ap.add_mutually_exclusive_group()
    diagnostics.add_argument("--show-ast", "--dump-ast", dest="show_ast",
                             action="store_true",
                             help="print the parsed AST as stable JSON; do not compile")
    diagnostics.add_argument("--show-desugar", action="store_true",
                             help="print the desugared AST as stable JSON; do not compile")
    args = ap.parse_args()

    if args.show_ast or args.show_desugar:
        if args.run:
            ap.error("diagnostic options cannot be combined with --run")
        path = os.path.realpath(args.src)
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
        except OSError as e:
            print(f"{path}: input error: {e}", file=sys.stderr)
            return 1
        try:
            tree = parse_mod.parse(lex.lex(src))
        except lex.LexError as e:
            print(render_error(path, src, "lex", str(e)), file=sys.stderr)
            return 1
        except parse_mod.ParseError as e:
            print(render_error(path, src, "parse", str(e)), file=sys.stderr)
            return 1
        phase = "parsed"
        if args.show_desugar:
            try:
                tree = desugar.desugar(tree)
            except desugar.DesugarError as e:
                print(render_error(path, src, "desugar", str(e)),
                      file=sys.stderr)
                return 1
            phase = "desugared"
        import ast_dump
        sys.stdout.write(ast_dump.dumps(tree, path, phase))
        return 0

    prog, err = module_system.compile_module_graph(args.src)
    if err:
        try:
            with open(err.path, encoding="utf-8") as f:
                error_src = f.read()
        except OSError:
            error_src = ""
        print(render_error(err.path, error_src, err.kind,
                           err.raw_message()), file=sys.stderr)
        return 1

    stem = os.path.splitext(os.path.basename(args.src))[0]
    outdir = os.path.abspath(args.outdir or os.path.join(HERE, "out"))
    os.makedirs(outdir, exist_ok=True)

    if args.target == "py":
        import gen_py
        code = gen_py.generate(prog, source_path=os.path.realpath(args.src))
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
        import gen_java
        cls = gen_java.cls_name(stem)
        code = gen_java.generate(prog, cls,
                                 source_path=os.path.realpath(args.src))
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
