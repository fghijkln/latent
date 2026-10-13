"""Compile and run the Latent parser fixture with the pinned Stage0 only."""
from __future__ import annotations

import io
import importlib
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests._safe_test_env import isolate_test_environment

isolate_test_environment()
FIXTURE = ROOT / "tests" / "bootstrap_parser.lt"
STAGE0_COMMIT = "9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9"

PARSER_CASES = (
    ("required/default/rest/keyword-only/extra parameters",
     "fn f(a, b=1, *rest, required, option=2, **extra):\n  return a\n"),
    ("bare star and extra parameters",
     "fn f(a, *, required, option=2, **extra):\n  return a\n"),
    ("rest and extra parameters", "fn f(*items, **options):\n  return items\n"),
    ("positional, named, and unpacked call arguments",
     "say f(1, *items, flag=2, **options)\n"),
    ("named arguments after mapping unpack",
     "say f(**first, flag=2, **last)\n"),
    ("py/java/super prefixes",
     "say py \"math\".sqrt(4)\nsay java \"java.lang.String\"\nsay super.method(self)\n"),
    ("local import and binding declarations",
     "import \"lib.lt\" as lib\nglobal first, second\nnonlocal outer\n"),
    ("class with local parent and methods",
     "class Child(Base):\n  fn init(self):\n    return nil\n"),
    ("class with aliased parent and try/catch/throw",
     "class Child(lib.Base):\n  fn run(self, value):\n    try:\n      throw value\n    catch error:\n      say error\n"),
    ("try/catch statement",
     "try:\n  say \"work\"\ncatch problem:\n  say problem\n"),
    ("required positional after default",
     "fn f(optional=1, required):\n  return required\n"),
    ("bare star without keyword-only parameter",
     "fn f(*):\n  return nil\n"),
    ("duplicate star separator",
     "fn f(*items, *more):\n  return items\n"),
    ("parameter after extra mapping", "fn f(**extra, value):\n  return value\n"),
    ("duplicate extra parameter", "fn f(**first, **second):\n  return first\n"),
    ("positional argument after named argument", "say f(flag=1, 2)\n"),
    ("positional unpack after named argument", "say f(flag=1, *items)\n"),
    ("trailing comma in parameter list", "fn f(value,):\n  return value\n"),
    ("trailing comma in call", "say f(1,)\n"),
    ("unary plus is not part of Latent grammar", "say +1\n"),
    ("import requires alias", "import \"lib.lt\"\n"),
    ("global requires a name", "global\n"),
    ("nonlocal rejects missing name after comma", "nonlocal outer,\n"),
    ("class body only accepts methods", "class Empty:\n  say 1\n"),
    ("class body must have a method", "class Empty:\n  \n"),
    ("try requires catch", "try:\n  say 1\nsay 2\n"),
    ("catch requires a binding name", "try:\n  say 1\ncatch:\n  say 2\n"),
    ("throw requires an expression", "throw\n"),
)

M5_REFERENCE_SOURCE = (
    'fn normalized(name, obj, xs):\n'
    '  say "Hi $$ $name ${1 + 2}"\n'
    '  obj.item\n'
    '  obj.work(name)\n'
    '  xs[0]\n'
    'fn branch(flag):\n'
    '  if flag:\n'
    '    flag\n'
    '  else:\n'
    '    9\n'
)


def _m5_expression_signature(node) -> str:
    kind = type(node).__name__
    if kind == "Name":
        return f"Name{{{node.id}}}"
    if kind == "Str":
        return f"Str{{{node.value}}}"
    if kind == "Num":
        value = node.value
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return f"Num{{{value}}}"
    if kind == "BinOp":
        return (f"BinOp({node.op},"
                f"{_m5_expression_signature(node.left)},"
                f"{_m5_expression_signature(node.right)})")
    if kind == "Call":
        arguments = ",".join(_m5_expression_signature(argument)
                              for argument in node.args)
        return (f"Call({_m5_expression_signature(node.func)};"
                f"{arguments})")
    raise AssertionError(f"unexpected Stage0 M5 expression node: {kind}")


def extract_stage0(destination: Path) -> None:
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", STAGE0_COMMIT],
        capture_output=True, check=False)
    if archive.returncode != 0:
        raise RuntimeError(
            f"fixed Stage0 commit {STAGE0_COMMIT} is unavailable in this checkout")
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
        for member in bundle.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"unsafe Stage0 archive member: {member.name}")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise RuntimeError(f"cannot read Stage0 file: {member.name}")
                target.write_bytes(stream.read())
            elif member.issym() or member.islnk():
                raise RuntimeError(f"unexpected link in Stage0 archive: {member.name}")


class BootstrapParserTests(unittest.TestCase):
    def test_latent_parser_fixture(self):
        with tempfile.TemporaryDirectory(prefix="p15-parser-") as temporary:
            temporary = Path(temporary)
            stage0 = temporary / "stage0"
            extract_stage0(stage0)
            output = temporary / "output"
            output.mkdir()
            compiled = subprocess.run(
                [sys.executable, str(stage0 / "latent.py"), str(FIXTURE),
                 "-t", "py", "-o", str(output)],
                cwd=ROOT, text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(compiled.returncode, 0,
                             compiled.stdout + compiled.stderr)
            (output / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            generated = output / "bootstrap_parser.py"
            executed = subprocess.run(
                [sys.executable, str(generated)], cwd=ROOT,
                text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertEqual(executed.returncode, 0,
                             executed.stdout + executed.stderr)
            output_lines = executed.stdout.splitlines()
            latent_signatures = [line.removeprefix("M5SIG|")
                                 for line in output_lines
                                 if line.startswith("M5SIG|")]
            self.assertEqual(latent_signatures,
                             self._stage0_m5_signatures(stage0))
            self.assertEqual([line for line in output_lines
                              if not line.startswith("M5SIG|")],
                             ["bootstrap parser ok"])
            self.assertEqual(executed.stderr, "")

    def test_module_graph_deduplicates_and_keeps_logical_ids_stable(self):
        with tempfile.TemporaryDirectory(prefix="p15-module-graph-") as temporary:
            temporary = Path(temporary)
            stage0 = temporary / "stage0"
            extract_stage0(stage0)
            output = temporary / "output"
            output.mkdir()
            compiled = subprocess.run(
                [sys.executable, str(stage0 / "latent.py"), str(FIXTURE),
                 "-t", "py", "-o", str(output)],
                cwd=ROOT, text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(compiled.returncode, 0,
                             compiled.stdout + compiled.stderr)
            (output / "latent_host.py").write_bytes(
                (ROOT / "bootstrap" / "latent_host.py").read_bytes())
            generated = output / "bootstrap_parser.py"

            def run_probe(project: Path,
                          source_root: Path | None = None) -> list[str]:
                arguments = [sys.executable, str(generated),
                             str(project / "main.lt")]
                if source_root is not None:
                    arguments.append(str(source_root))
                result = subprocess.run(
                    arguments,
                    cwd=ROOT, env={"PYTHONPATH": str(output)}, text=True,
                    encoding="utf-8", capture_output=True, check=False)
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                return [line for line in result.stdout.splitlines()
                        if line.startswith("GRAPH_")]

            outputs = []
            for suffix in ("checkout-one", "checkout-two"):
                project = temporary / suffix
                (project / "dir").mkdir(parents=True)
                (project / "nested").mkdir()
                (project / "main.lt").write_text(
                    'import "dir/../lib.lt" as first\n'
                    'import "lib.lt" as second\n'
                    'say "root"\n', encoding="utf-8", newline="\n")
                (project / "lib.lt").write_text(
                    'import "nested/dep.lt" as dep\n'
                    'say "library"\n', encoding="utf-8", newline="\n")
                (project / "nested" / "dep.lt").write_text(
                    'say "dependency"\n', encoding="utf-8", newline="\n")
                outputs.append(run_probe(project))
                self.assertNotIn(str(project), "\n".join(outputs[-1]))
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0], [
                "GRAPH_OK|main.lt|3|3",
                "GRAPH_UNIT|nested/dep.lt|nested/dep.lt",
                "GRAPH_UNIT|lib.lt|lib.lt",
                "GRAPH_UNIT|main.lt|main.lt",
            ])

            rooted_outputs = []
            for suffix in ("rooted-one", "rooted-two"):
                project = temporary / suffix
                modules = project / "modules"
                app = modules / "app"
                library = modules / "lib"
                app.mkdir(parents=True)
                library.mkdir(parents=True)
                (app / "main.lt").write_text(
                    'import "../lib/shared.lt" as shared\n',
                    encoding="utf-8", newline="\n")
                (library / "shared.lt").write_text(
                    'say "shared"\n', encoding="utf-8", newline="\n")
                rooted_outputs.append(run_probe(app, modules))
                self.assertNotIn(str(project), "\n".join(rooted_outputs[-1]))
            self.assertEqual(rooted_outputs[0], rooted_outputs[1])
            self.assertEqual(rooted_outputs[0], [
                "GRAPH_OK|app/main.lt|2|1",
                "GRAPH_UNIT|lib/shared.lt|lib/shared.lt",
                "GRAPH_UNIT|app/main.lt|app/main.lt",
            ])

            explicit_escape = temporary / "explicit-root-escape"
            modules = explicit_escape / "modules"
            app = modules / "app"
            app.mkdir(parents=True)
            (app / "main.lt").write_text(
                'import "../../outside.lt" as outside\n',
                encoding="utf-8", newline="\n")
            (explicit_escape / "outside.lt").write_text(
                'say "outside"\n', encoding="utf-8", newline="\n")
            self.assertIn("module path escapes the source root",
                          "\n".join(run_probe(app, modules)))

            outside_entry = temporary / "outside-entry"
            outside_entry.mkdir()
            (outside_entry / "main.lt").write_text(
                'say "outside entry"\n', encoding="utf-8", newline="\n")
            nested_root = outside_entry / "inside"
            nested_root.mkdir()
            self.assertIn("entry path escapes the source root",
                          "\n".join(run_probe(outside_entry, nested_root)))
            self.assertIn("source root is not a directory",
                          "\n".join(run_probe(
                              outside_entry, outside_entry / "missing-root")))

            def run_error(name: str, source: str,
                          imported: dict[str, str] | None = None,
                          main_relative: str = "main.lt") -> str:
                project = temporary / name
                project.mkdir()
                if "/" in main_relative:
                    (project / main_relative).parent.mkdir(parents=True,
                                                           exist_ok=True)
                main = project / main_relative
                main.write_text(source, encoding="utf-8", newline="\n")
                for relative, text in (imported or {}).items():
                    target = project / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8", newline="\n")
                return "\n".join(run_probe(main.parent))

            errors = (
                ("missing", 'import "absent.lt" as lib\n', None,
                 "GRAPH_ERROR|module|main.lt|1|1|cannot read module absent.lt: not-found"),
                ("extension", 'import "lib.txt" as lib\n', None,
                 "module path must end in .lt"),
                ("absolute", 'import "/outside.lt" as lib\n', None,
                 "module path must be relative to the importing file"),
                ("backslash-relative", r'import "dir\\lib.lt" as lib' + "\n",
                 None, "module path must be relative to the importing file"),
                ("drive-relative", 'import "C:drive.lt" as lib\n', None,
                 "module path must be relative to the importing file"),
                ("nul-path", 'import "bad\\0.lt" as lib\n', None,
                 "module path contains NUL"),
                ("duplicate-alias",
                 'import "lib.lt" as lib\nimport "lib.lt" as lib\n',
                 {"lib.lt": 'say "library"\n'},
                 "duplicate module alias 'lib'"),
                ("late", 'say 1\nimport "lib.lt" as lib\n', None,
                 "imports must precede module code"),
                ("nested", 'fn f():\n  import "lib.lt" as lib\n', None,
                 "imports must be top-level module declarations"),
                ("bad-import-location", 'import "lib.lt" as lib\n',
                 {"lib.lt": "say 1 +\n"},
                 "GRAPH_ERROR|parse|lib.lt|1|8|unexpected NEWLINE in expression"),
                ("cycle", 'import "b.lt" as b\n',
                 {"b.lt": 'import "main.lt" as root\n'},
                 "GRAPH_ERROR|module|b.lt|1|1|module import cycle: main.lt -> b.lt -> main.lt"),
                ("escape", 'import "../outside.lt" as out\n',
                 {"../outside.lt": 'say "outside"\n'},
                 "module path escapes the source root"),
            )
            for name, source, imported, marker in errors:
                with self.subTest(name=name):
                    graph_output = run_error(name, source, imported)
                    self.assertIn(marker, graph_output)

            symlink_project = temporary / "symlink-escape"
            symlink_project.mkdir()
            outside_file = temporary / "outside-symlink.lt"
            outside_file.write_text('say "outside"\n',
                                    encoding="utf-8", newline="\n")
            (symlink_project / "link.lt").symlink_to(outside_file)
            (symlink_project / "main.lt").write_text(
                'import "link.lt" as linked\n',
                encoding="utf-8", newline="\n")
            symlink_error = "\n".join(run_probe(symlink_project))
            self.assertIn("module path escapes the source root", symlink_error)

    def _stage0_m5_signatures(self, stage0: Path) -> list[str]:
        module_names = ("nodes", "lex", "parse", "desugar")
        previous_modules = {name: sys.modules.pop(name, None)
                            for name in module_names}
        previous_path = list(sys.path)
        try:
            sys.path.insert(0, str(stage0))
            importlib.invalidate_caches()
            reference_lexer = importlib.import_module("lex")
            reference_parser = importlib.import_module("parse")
            reference_desugar = importlib.import_module("desugar")
            parsed = reference_parser.parse(
                reference_lexer.lex(M5_REFERENCE_SOURCE))
            lowered = reference_desugar.desugar(parsed)
            body = lowered.stmts[0].body
            return [
                _m5_expression_signature(body[0].expr),
                _m5_expression_signature(body[1].expr),
                _m5_expression_signature(body[2].expr),
                _m5_expression_signature(body[3].value),
                _m5_expression_signature(lowered.stmts[1].body[0].then_body[0].value),
                _m5_expression_signature(lowered.stmts[1].body[0].else_body[0].value),
            ]
        finally:
            sys.path[:] = previous_path
            for name in module_names:
                sys.modules.pop(name, None)
                if previous_modules[name] is not None:
                    sys.modules[name] = previous_modules[name]

    def test_parameter_and_call_forms_match_fixed_stage0(self):
        with tempfile.TemporaryDirectory(prefix="p15-parser-diff-") as temporary:
            temporary = Path(temporary)
            stage0 = temporary / "stage0"
            extract_stage0(stage0)
            expectations = self._stage0_expectations(stage0)
            output = temporary / "output"
            output.mkdir()
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", prefix="p15-parser-diff-",
                    suffix=".lt", dir=ROOT / "tests", delete=False) as source_file:
                generated_source = Path(source_file.name)
                source_file.write(self._differential_fixture(expectations))
            try:
                compiled = subprocess.run(
                    [sys.executable, str(stage0 / "latent.py"),
                     str(generated_source), "-t", "py", "-o", str(output)],
                    cwd=ROOT, text=True, encoding="utf-8", capture_output=True,
                    check=False)
                self.assertEqual(compiled.returncode, 0,
                                 compiled.stdout + compiled.stderr)
                executed = subprocess.run(
                    [sys.executable,
                     str(output / (generated_source.stem + ".py"))],
                    cwd=ROOT, text=True, encoding="utf-8", capture_output=True,
                    check=False)
                self.assertEqual(executed.returncode, 0,
                                 executed.stdout + executed.stderr)
                self.assertEqual(executed.stdout, "parser differential ok\n")
                self.assertEqual(executed.stderr, "")
            finally:
                generated_source.unlink(missing_ok=True)

    def test_repository_latent_sources_match_fixed_stage0_parser(self):
        roots = (ROOT / "bootstrap", ROOT / "tests", ROOT / "cookbook")
        sources = []
        for base in roots:
            for path in sorted(base.rglob("*.lt")):
                sources.append((path.relative_to(ROOT).as_posix(),
                                path.read_text(encoding="utf-8")))
        self.assertGreater(len(sources), 250)
        with tempfile.TemporaryDirectory(prefix="p15-parser-corpus-") as temporary:
            temporary = Path(temporary)
            stage0 = temporary / "stage0"
            extract_stage0(stage0)
            expectations = self._stage0_expectations(stage0, sources)
            output = temporary / "output"
            output.mkdir()
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", prefix="p15-parser-corpus-",
                    suffix=".lt", dir=ROOT / "tests", delete=False) as source_file:
                generated_source = Path(source_file.name)
                source_file.write(self._differential_fixture(expectations))
            try:
                compiled = subprocess.run(
                    [sys.executable, str(stage0 / "latent.py"),
                     str(generated_source), "-t", "py", "-o", str(output)],
                    cwd=ROOT, text=True, encoding="utf-8", capture_output=True,
                    check=False)
                self.assertEqual(compiled.returncode, 0,
                                 compiled.stdout + compiled.stderr)
                executed = subprocess.run(
                    [sys.executable,
                     str(output / (generated_source.stem + ".py"))],
                    cwd=ROOT, text=True, encoding="utf-8", capture_output=True,
                    check=False)
                self.assertEqual(executed.returncode, 0,
                                 executed.stdout + executed.stderr)
                self.assertEqual(executed.stdout, "parser differential ok\n")
                self.assertEqual(executed.stderr, "")
            finally:
                generated_source.unlink(missing_ok=True)

    def _stage0_expectations(self, stage0: Path, cases=PARSER_CASES):
        module_names = ("nodes", "lex", "parse")
        previous_modules = {name: sys.modules.pop(name, None)
                            for name in module_names}
        previous_path = list(sys.path)
        try:
            sys.path.insert(0, str(stage0))
            importlib.invalidate_caches()
            reference_lexer = importlib.import_module("lex")
            reference_parser = importlib.import_module("parse")
            expectations = []
            for label, source in cases:
                try:
                    reference_parser.parse(reference_lexer.lex(source))
                    expectations.append((label, source, True, 0, 0, None))
                except reference_parser.ParseError as error:
                    match = re.fullmatch(r"(\d+):(\d+): (.*)", str(error))
                    self.assertIsNotNone(match, f"unexpected Stage0 diagnostic: {error}")
                    expectations.append((label, source, False,
                                         int(match.group(1)), int(match.group(2)),
                                         match.group(3)))
            return expectations
        finally:
            sys.path[:] = previous_path
            for name in module_names:
                sys.modules.pop(name, None)
                if previous_modules[name] is not None:
                    sys.modules[name] = previous_modules[name]

    def _differential_fixture(self, expectations):
        def latent_string(value: str) -> str:
            value = (value.replace("$", "$$").replace("\\", "\\\\")
                     .replace('"', '\\"')
                     .replace("\n", "\\n").replace("\t", "\\t")
                     .replace("\r", "\\r").replace("\0", "\\0"))
            return '"' + value + '"'

        lines = [
            'import "../bootstrap/parser.lt" as parser',
            "",
            "fn assert_equal(actual, expected, message):",
            "    if actual != expected:",
            '        throw message + ": got " + str(actual)',
            "",
            "fn check_case(source, expected_ok, expected_line, expected_col, expected_error, label):",
            "    result = parser.parse(source, label)",
            '    if result["ok"] != expected_ok:',
            '        throw label + ": accepted/rejected differently"',
            '    if expected_ok and result["tree"]["source_id"] != label:',
            '        throw label + ": logical source id was not preserved"',
            "    if not expected_ok:",
            '        assert_equal(result["line"], expected_line, label + " line")',
            '        assert_equal(result["col"], expected_col, label + " column")',
            '        assert_equal(result["error"], expected_error, label + " message")',
            "",
            "fn main():",
        ]
        for label, source, accepted, line, col, error in expectations:
            lines.append(
                "    check_case(" + ", ".join((
                    latent_string(source), "true" if accepted else "false",
                    str(line), str(col),
                    "nil" if error is None else latent_string(error),
                    latent_string(label))) + ")")
        lines += ['', '    say "parser differential ok"', '', 'main()', '']
        return "\n".join(lines)


if __name__ == "__main__":
    unittest.main()
