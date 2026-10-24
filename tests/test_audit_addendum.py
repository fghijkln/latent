"""Focused regressions for the Latent audit addendum findings."""
import http.client
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playground import app
import gen_py
import latent


class CodegenPhysicalLineTests(unittest.TestCase):
    def test_incremental_counter_preserves_physical_source_map_lines(self):
        generator = gen_py.Gen(source_path="counter.lt")
        generator.indent = 2
        generator.w("first\nembedded", source_line=7)
        generator.w("next", source_line=8, source_path="other.lt")

        self.assertEqual(generator._physical_line_count, 3)
        self.assertEqual(
            generator._physical_line_count,
            len(generator.out) + sum(line.count("\n") for line in generator.out),
        )
        self.assertEqual(
            generator.source_map,
            {1: ("counter.lt", 7), 3: ("other.lt", 8)},
        )
        self.assertEqual(generator._render_output(),
                         "        first\nembedded\n        next\n")


class InternalNameGrowthTests(unittest.TestCase):
    def test_many_classes_use_bounded_internal_factory_names(self):
        source = "".join(
            f"class C{index}:\n    fn value(self):\n        return {index}\n"
            for index in range(1000))
        program, error = latent.compile_src(source, "many_classes.lt")
        self.assertIsNone(error)
        generated = gen_py.generate(program, source_path="many_classes.lt")
        self.assertLess(len(generated), 1_000_000)


class PlaygroundAddendumHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.address = cls.server.server_address

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post(self, source="say 1\n"):
        return self.post_json({"source": source})

    def post_json(self, value):
        conn = http.client.HTTPConnection(*self.address, timeout=5)
        payload = json.dumps(value).encode("utf-8")
        conn.request("POST", "/api/run", body=payload,
                     headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        status = response.status
        body = response.read()
        conn.close()
        return status, body

    def test_non_object_json_returns_bounded_400_instead_of_dropping_connection(self):
        for value in ([], None, "source"):
            with self.subTest(value=value):
                status, body = self.post_json(value)
                result = json.loads(body)

                self.assertEqual(status, 400)
                self.assertEqual(result["error"], "JSON body must be an object")
                self.assertLess(len(body), 512)

    def test_unexpected_recursion_error_is_a_structured_http_response(self):
        with mock.patch.object(app, "blocked_feature", return_value=None), \
                mock.patch.object(app.latent, "compile_src",
                                  side_effect=RecursionError):
            status, body = self.post()
        result = json.loads(body)

        self.assertEqual(status, 200)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "parse")
        self.assertIn("safe depth", result["error"])
        self.assertNotIn("Traceback", result["error"])
        self.assertLess(len(body), 4_096)

    def test_deep_ast_returns_bounded_structured_http_error(self):
        source = "say " + ".".join(["x"] * (app.MAX_AST_DEPTH + 24)) + "\n"
        status, body = self.post(source)
        result = json.loads(body)

        self.assertEqual(status, 200)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "parse")
        self.assertIn("source nesting exceeds maximum supported depth", result["error"])
        self.assertNotIn("Traceback", result["error"])
        self.assertLess(len(body), 4_096)

    def test_150_term_addition_is_not_mistaken_for_unsafe_nesting(self):
        source = "say " + "+".join(["1"] * 150) + "\n"
        status, body = self.post(source)
        result = json.loads(body)

        self.assertEqual(status, 200)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stdout"], "150\n")

    def test_deep_unary_expression_returns_structured_error(self):
        source = "say " + ("-" * (app.MAX_AST_DEPTH + 24)) + "1\n"
        status, body = self.post(source)
        result = json.loads(body)

        self.assertEqual(status, 200)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "parse")
        self.assertNotIn("Traceback", body.decode("utf-8"))
        self.assertLess(len(body), 4_096)

    def test_generated_python_field_and_complete_json_body_are_bounded(self):
        oversized_code = "# " + ("x" * (app.MAX_PYTHON_BYTES + 4096)) + "\n"
        with mock.patch.object(app.gen_py, "generate", return_value=oversized_code):
            status, body = self.post()
        result = json.loads(body)

        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertTrue(result["python_truncated"])
        self.assertLessEqual(len(result["python"].encode("utf-8")),
                             app.MAX_PYTHON_BYTES)
        self.assertLessEqual(len(body), app.MAX_JSON_RESPONSE_BYTES)

        page = (ROOT / "playground" / "index.html").read_text(encoding="utf-8")
        self.assertIn("last.python_truncated", page)
        self.assertIn("生成代码已截断", page)

    def test_oversized_serialized_result_uses_small_structured_fallback(self):
        with mock.patch.object(app, "MAX_JSON_RESPONSE_BYTES", 256), \
                mock.patch.object(app, "run_program", return_value={
                    "ok": True,
                    "python": "x" * 2_000,
                }):
            status, body = self.post()

        self.assertEqual(status, 500)
        self.assertLessEqual(len(body), 256)
        self.assertEqual(json.loads(body)["stage"], "response")


class PlaygroundImagePermissionTests(unittest.TestCase):
    def test_docker_image_keeps_application_tree_unwritable_to_runner(self):
        dockerfile = (ROOT / "playground" / "Dockerfile").read_text(
            encoding="utf-8")
        self.assertIn("chown -R root:root /app", dockerfile)
        self.assertIn("chmod -R a-w /app", dockerfile)
        self.assertIn("USER runner", dockerfile)


if __name__ == "__main__":
    unittest.main()
