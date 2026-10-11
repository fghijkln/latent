"""Run the Latent lexer fixture through the current Python Stage0 compiler."""
from __future__ import annotations
import importlib.util
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unicodedata
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "bootstrap_lexer.lt"
STAGE0_COMMIT = "9cc060d0a408ea88bd5a0c42cbf8ad5bf61cf9a9"


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


def load_stage0_lexer(stage0: Path):
    spec = importlib.util.spec_from_file_location("p15_stage0_lex", stage0 / "lex.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the fixed Stage0 lexer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def latent_string(value: str) -> str:
    escaped = []
    for character in value:
        escaped.append({
            "\\": "\\\\",
            '"': '\\"',
            "\n": "\\n",
            "\r": "\\r",
            "\t": "\\t",
            "\0": "\\0",
        }.get(character, character))
    return '"' + "".join(escaped) + '"'


def expected_stage0_result(reference_lexer, source: str) -> dict:
    try:
        return {"tokens": reference_lexer.lex(source)}
    except reference_lexer.LexError as exc:
        match = re.fullmatch(r"(\d+):(\d+): (.*)", str(exc), re.DOTALL)
        if match is None:
            raise AssertionError(f"unexpected Stage0 lexer error format: {exc}") from exc
        return {
            "error": match.group(3),
            "line": int(match.group(1)),
            "col": int(match.group(2)),
        }


def differential_fixture(reference_lexer, cases: list[tuple[str, bool]]) -> str:
    lines = ['import "../../bootstrap/lexer.lt" as lexer', ""]
    for case_index, (source, compare_error_text) in enumerate(cases):
        expected = expected_stage0_result(reference_lexer, source)
        result_name = f"result_{case_index}"
        lines += [
            f"{result_name} = lexer.lex({latent_string(source)})",
        ]
        if "tokens" in expected:
            tokens = expected["tokens"]
            lines += [
                f'if not {result_name}["ok"]:',
                f'    throw "Stage0 success rejected in case {case_index}"',
                f'{result_name}_tokens = {result_name}["tokens"]',
                f"if len({result_name}_tokens) != {len(tokens)}:",
                f'    throw "token count mismatch in case {case_index}"',
            ]
            for token_index, token in enumerate(tokens):
                token_value = token.value
                if token_value is None:
                    value_expression = "nil"
                elif isinstance(token_value, str):
                    value_expression = latent_string(token_value)
                else:
                    value_expression = repr(token_value)
                token_name = f"{result_name}_tokens[{token_index}]"
                checks = (
                    ("kind", latent_string(token.kind), "kind"),
                    ("value", value_expression, "value"),
                    ("line", str(token.line), "line"),
                    ("col", str(token.col), "column"),
                )
                for field, expression, label in checks:
                    lines += [
                        f'if {token_name}["{field}"] != {expression}:',
                        f'    throw "{label} mismatch in case {case_index}, token {token_index}"',
                    ]
        else:
            lines += [
                f'if {result_name}["ok"]:',
                f'    throw "Stage0 error accepted in case {case_index}"',
                f'if {result_name}["line"] != {expected["line"]}:',
                f'    throw "error line mismatch in case {case_index}"',
                f'if {result_name}["col"] != {expected["col"]}:',
                f'    throw "error column mismatch in case {case_index}"',
            ]
            if compare_error_text:
                lines += [
                    f'if {result_name}["error"] != {latent_string(expected["error"])}:',
                    f'    throw "error text mismatch in case {case_index}"',
                ]
    lines += ['say "bootstrap lexer Unicode differential ok"', ""]
    return "\n".join(lines)


def compile_and_run_fixture(stage0: Path, fixture: Path, output: Path) -> subprocess.CompletedProcess:
    compiled = subprocess.run(
        [sys.executable, str(stage0 / "latent.py"), str(fixture), "-t", "py", "-o", str(output)],
        cwd=ROOT, text=True, encoding="utf-8", capture_output=True, check=False)
    if compiled.returncode != 0:
        raise AssertionError(compiled.stdout + compiled.stderr)
    generated = output / (fixture.stem + ".py")
    executed = subprocess.run(
        [sys.executable, str(generated)], cwd=ROOT,
        env={**os.environ, "PYTHONPATH": ""},
        text=True, encoding="utf-8", capture_output=True, check=False)
    if executed.returncode != 0:
        raise AssertionError(executed.stdout + executed.stderr)
    return executed


class BootstrapLexerTests(unittest.TestCase):
    def test_latent_lexer_fixture(self):
        with tempfile.TemporaryDirectory(prefix="p15-lexer-") as temporary:
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
            generated = output / "bootstrap_lexer.py"
            executed = subprocess.run(
                [sys.executable, str(generated)], cwd=ROOT,
                env={**os.environ, "PYTHONPATH": ""},
                text=True, encoding="utf-8", capture_output=True,
                check=False)
            self.assertEqual(executed.returncode, 0,
                             executed.stdout + executed.stderr)
            self.assertEqual(executed.stdout, "bootstrap lexer ok\n")
            self.assertEqual(executed.stderr, "")

    def test_unicode_tokens_and_positions_match_fixed_stage0(self):
        with tempfile.TemporaryDirectory(prefix="p15-lexer-diff-") as temporary:
            temporary = Path(temporary)
            stage0 = temporary / "stage0"
            extract_stage0(stage0)
            reference_lexer = load_stage0_lexer(stage0)

            cases: list[tuple[str, bool]] = [
                ("fn café_β9:\n  say ٣٢.٥\n", True),
                ("变量²Ⅻ½ = 𝟠\nsay 𝒜\n", True),
                ("say .٣\nsay 'é\\n東京'\n", True),
                ("say ²\nsay 1²\n", True),
                ("Ⅻname\n", True),
                ("a\u0301 = 1\n", True),
                ("say \u2003x\n", True),
                ("say \u00a0x\n", True),
                ("say \u001cx\n", True),
                ("say \u200bx\n", False),
            ]
            for codepoint in range(0x110000):
                character = chr(codepoint)
                if not character.isspace() or character == "\n":
                    continue
                cases.append((character + "# unicode comment\nsay 1\n", True))
                cases.append((character + "\nsay 1\n", True))

            with tempfile.TemporaryDirectory(
                    prefix="p15-lexer-probe-", dir=ROOT / "tests") as probe_dir:
                fixture = Path(probe_dir) / "lexer_unicode_probe.lt"
                fixture.write_text(
                    differential_fixture(reference_lexer, cases),
                    encoding="utf-8", newline="\n")
                output = temporary / "unicode-output"
                output.mkdir()
                executed = compile_and_run_fixture(stage0, fixture, output)
                self.assertEqual(executed.stdout, "bootstrap lexer Unicode differential ok\n")
                self.assertEqual(executed.stderr, "")

    def test_unicode_table_matches_stage0_property_boundaries(self):
        if unicodedata.unidata_version not in ("14.0.0", "15.0.0"):
            self.skipTest(
                "checked-in Latent tables support Unicode 14.0.0 and 15.0.0; "
                f"this interpreter uses Unicode {unicodedata.unidata_version}"
            )

        points: set[int] = set()
        start = 0
        previous_state = None

        def state_for(character: str) -> tuple[bool, bool, bool, bool, bool]:
            return (
                character.isalpha(),
                character.isalnum() and not character.isalpha(),
                character.isdigit(),
                character.isdecimal(),
                character.isspace(),
            )

        for codepoint in range(0x110000):
            if 0xD800 <= codepoint <= 0xDFFF:
                if previous_state is not None:
                    end = codepoint - 1
                    points.update((start, end, (start + end) // 2))
                    previous_state = None
                continue
            state = state_for(chr(codepoint))
            if previous_state is None:
                start = codepoint
                previous_state = state
            elif state != previous_state:
                end = codepoint - 1
                points.update((start, end, (start + end) // 2))
                start = codepoint
                previous_state = state
            if chr(codepoint).isdecimal():
                points.add(codepoint)
        if previous_state is not None:
            end = 0x10FFFF
            points.update((start, end, (start + end) // 2))

        rows = []
        for codepoint in sorted(points):
            character = chr(codepoint)
            decimal_value = unicodedata.decimal(character, -1)
            flags = (
                character.isalpha(),
                character.isalnum() and not character.isalpha(),
                character.isdigit(),
                decimal_value,
                character.isspace(),
            )
            values = [latent_string(character)]
            values.extend("true" if value is True else "false" if value is False
                          else str(value) for value in flags)
            rows.append("[" + ", ".join(values) + "]")

        lines = [
            'import "../../bootstrap/unicode_tables.lt" as unicode',
            "fn main():",
            "    checks = [" + ", ".join(rows) + "]",
            "    for item in checks:",
            "        if unicode.is_alpha(item[0]) != item[1]:",
            '            throw "Unicode alpha range mismatch"',
            "        if unicode.is_numeric_tail(item[0]) != item[2]:",
            '            throw "Unicode alphanumeric range mismatch"',
            "        if unicode.is_digit(item[0]) != item[3]:",
            '            throw "Unicode digit range mismatch"',
            "        if unicode.decimal_digit_value(item[0]) != item[4]:",
            '            throw "Unicode decimal value mismatch"',
            "        if unicode.is_space(item[0]) != item[5]:",
            '            throw "Unicode whitespace range mismatch"',
            '    say "Unicode property boundaries ok"',
            "main()",
            "",
        ]

        with tempfile.TemporaryDirectory(prefix="p15-unicode-tables-") as temporary:
            temporary = Path(temporary)
            stage0 = temporary / "stage0"
            extract_stage0(stage0)
            with tempfile.TemporaryDirectory(
                    prefix="p15-unicode-probe-", dir=ROOT / "tests") as probe_dir:
                fixture = Path(probe_dir) / "unicode_properties_probe.lt"
                fixture.write_text("\n".join(lines), encoding="utf-8", newline="\n")
                output = temporary / "unicode-output"
                output.mkdir()
                executed = compile_and_run_fixture(stage0, fixture, output)
                self.assertEqual(executed.stdout, "Unicode property boundaries ok\n")
                self.assertEqual(executed.stderr, "")


if __name__ == "__main__":
    unittest.main()
