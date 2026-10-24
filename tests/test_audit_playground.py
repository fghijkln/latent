"""Regression tests for bounded playground execution and HTTP request handling."""
import http.client
import json
import os
import socket
import sys
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from playground import app


class PlaygroundExecutionTests(unittest.TestCase):
    def execute_generated(self, code):
        with mock.patch.object(app, "blocked_feature", return_value=None), \
                mock.patch.object(app.latent, "compile_src", return_value=(object(), None)), \
                mock.patch.object(app.gen_py, "generate", return_value=code):
            return app.run_program("regression test")

    def test_stdout_flood_is_bounded_and_kills_the_process_group(self):
        code = (
            "import os\n"
            "os.write(2, (str(os.getpid()) + ' ' + str(os.getpgrp()) + '\\n').encode())\n"
            "while True: os.write(1, b'x' * 4096)\n"
        )
        start = time.monotonic()
        with mock.patch.object(app, "MAX_OUT", 8192), mock.patch.object(app, "MAX_ERR", 1024), \
                mock.patch.object(app, "RUN_TIMEOUT", 2), \
                mock.patch.object(app.os, "killpg", wraps=os.killpg) as killpg:
            result = self.execute_generated(code)
        elapsed = time.monotonic() - start
        self.assertTrue(result["truncated"])
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["exit"], 0)
        self.assertFalse(result["timeout"])
        self.assertEqual(len(result["stdout"]), 8192)
        self.assertLess(elapsed, 2)
        child_pid, group_id = map(int, result["stderr"].split())
        self.assertEqual(child_pid, group_id)  # start_new_session created a dedicated process group
        self.assertTrue(any(call.args[0] == group_id for call in killpg.call_args_list))

    def test_simultaneous_stdout_and_stderr_floods_do_not_deadlock(self):
        code = (
            "import os\n"
            "while True:\n"
            "    os.write(1, b'o' * 4096)\n"
            "    os.write(2, b'e' * 4096)\n"
        )
        start = time.monotonic()
        with mock.patch.object(app, "MAX_OUT", 8192), mock.patch.object(app, "MAX_ERR", 8192), \
                mock.patch.object(app, "RUN_TIMEOUT", 2):
            result = self.execute_generated(code)
        self.assertTrue(result["truncated"])
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["exit"], 0)
        self.assertFalse(result["timeout"])
        self.assertGreater(len(result["stdout"]) + len(result["stderr"]), 0)
        self.assertLessEqual(len(result["stdout"].encode()), 8192)
        self.assertLessEqual(len(result["stderr"].encode()), 8192)
        self.assertLess(time.monotonic() - start, 2)

    def test_finite_stdout_and_stderr_are_both_preserved(self):
        code = (
            "import os\n"
            "os.write(1, b'stdout-marker\\n')\n"
            "os.write(2, b'stderr-marker\\n')\n"
        )
        with mock.patch.object(app, "MAX_OUT", 8192), mock.patch.object(app, "MAX_ERR", 8192):
            result = self.execute_generated(code)
        self.assertTrue(result["ok"])
        self.assertFalse(result["truncated"])
        self.assertFalse(result["timeout"])
        self.assertEqual(result["stdout"], "stdout-marker\n")
        self.assertEqual(result["stderr"], "stderr-marker\n")

    def test_wall_clock_timeout_terminates_process(self):
        start = time.monotonic()
        with mock.patch.object(app, "RUN_TIMEOUT", 0.2):
            result = self.execute_generated("import time; time.sleep(60)\n")
        self.assertTrue(result["timeout"])
        self.assertFalse(result["truncated"])
        self.assertLess(time.monotonic() - start, 2)

    def test_direct_runs_are_serialized_by_the_global_slot(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_run(src):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return {"ok": True}

        with mock.patch.object(app, "_run_program", side_effect=fake_run):
            threads = [threading.Thread(target=app.run_program, args=(str(i),)) for i in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(peak, 1)

    def test_concurrent_requests_bound_real_subprocess_runs(self):
        active = 0
        peak = 0
        lock = threading.Lock()
        results = []
        real_run = app._run_program

        def tracked_run(src):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                return real_run(src)
            finally:
                with lock:
                    active -= 1

        code = "import time\nprint('done')\ntime.sleep(0.2)\n"
        with mock.patch.object(app, "_run_program", side_effect=tracked_run), \
                mock.patch.object(app, "blocked_feature", return_value=None), \
                mock.patch.object(app.latent, "compile_src", return_value=(object(), None)), \
                mock.patch.object(app.gen_py, "generate", return_value=code):
            threads = [threading.Thread(target=lambda: results.append(app.run_program("sleep")))
                       for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 3)
        self.assertTrue(all(result["ok"] and result["stdout"] == "done\n" for result in results))
        self.assertEqual(peak, 1)

    def test_global_slot_is_released_when_run_raises(self):
        with mock.patch.object(app, "_run_program", side_effect=[RuntimeError("boom"), {"ok": True}]):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                app.run_program("first")
            self.assertEqual(app.run_program("second"), {"ok": True})

    def test_same_client_cannot_hold_multiple_run_or_queue_slots(self):
        running = app._RUN_ADMISSION.acquire("198.51.100.1", wait=True)
        try:
            self.assertIsNotNone(running)
            self.assertIsNone(
                app._RUN_ADMISSION.acquire("198.51.100.1", wait=True))

            queued = []
            started = threading.Event()

            def wait_as_other_client():
                started.set()
                queued.append(app._RUN_ADMISSION.acquire(
                    "198.51.100.2", wait=True))

            waiter = threading.Thread(target=wait_as_other_client)
            waiter.start()
            self.assertTrue(started.wait(timeout=1))
            time.sleep(0.05)
            self.assertTrue(waiter.is_alive())
        finally:
            app._RUN_ADMISSION.release(running)
        waiter.join(timeout=2)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(len(queued), 1)
        self.assertIsNotNone(queued[0])
        app._RUN_ADMISSION.release(queued[0])

    def test_playground_rejects_boolean_arithmetic_before_attribute_access(self):
        sources = (
            "x = true + false\nsay x.__class__.__base__.__subclasses__()\n",
            "x = -true\nsay x.__class__.__base__.__subclasses__()\n",
        )
        for source in sources:
            with self.subTest(source=source.splitlines()[0]):
                result = app.run_program(source)
                self.assertFalse(result["ok"])
                self.assertEqual(result["stage"], "run")
                self.assertIn("bad ", result["stderr"])

    def test_output_limit_is_explained_as_an_interruption_in_the_ui(self):
        with open(os.path.join(ROOT, "playground", "index.html"), encoding="utf-8") as page:
            html = page.read()
        self.assertIn("输出超限，程序已中止", html)
        self.assertIn("退出码：${last.exit}", html)
        self.assertIn("✗ 请求失败", html)


class PlaygroundHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.BoundedThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.address = cls.server.server_address

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def raw_request(self, headers, body=b""):
        sock = socket.create_connection(self.address, timeout=2)
        sock.settimeout(2)
        sock.sendall(("POST /api/run HTTP/1.1\r\nHost: localhost\r\n" + headers +
                      "Connection: close\r\n\r\n").encode("ascii") + body)
        chunks = []
        while True:
            try:
                part = sock.recv(4096)
            except socket.timeout:
                self.fail("server did not complete the HTTP error response promptly")
            if not part:
                break
            chunks.append(part)
        sock.close()
        return b"".join(chunks)

    def test_negative_and_malformed_content_lengths_are_rejected(self):
        for value in ("-1", "+1", "not-a-number"):
            with self.subTest(content_length=value):
                response = self.raw_request(f"Content-Length: {value}\r\n")
                self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])

    def test_oversized_content_length_is_rejected_without_reading_body(self):
        response = self.raw_request(f"Content-Length: {app.MAX_REQUEST + 1}\r\n")
        self.assertIn(b" 413 ", response.split(b"\r\n", 1)[0])

    def test_slow_incomplete_body_gets_bounded_408_response(self):
        start = time.monotonic()
        with mock.patch.object(app, "BODY_TIMEOUT", 0.25), mock.patch.object(app, "BODY_IDLE_TIMEOUT", 0.1):
            response = self.raw_request("Content-Length: 20\r\n", b"{")
        self.assertIn(b" 408 ", response.split(b"\r\n", 1)[0])
        self.assertLess(time.monotonic() - start, 1.5)

    def test_drip_fed_header_is_bounded_by_total_deadline(self):
        for attempt in range(3):
            with self.subTest(attempt=attempt), mock.patch.object(app, "HEADER_TIMEOUT", 0.25):
                # Patch before connect so the accepted handler always sees this deadline.
                sock = socket.create_connection(self.address, timeout=2)
                sock.settimeout(2)
                start = time.monotonic()
                try:
                    sock.sendall(b"POST /not-a-route HTTP/1.1\r\nHost: localhost\r\nX-Slow:")
                    for _ in range(20):
                        time.sleep(0.05)
                        try:
                            sock.sendall(b"a")
                        except OSError:
                            break
                    while sock.recv(4096):
                        pass
                    self.assertLess(time.monotonic() - start, 1.5)
                finally:
                    sock.close()

    def test_header_deadline_does_not_interrupt_a_body_with_its_own_deadline(self):
        payload = b'{"source":"say 1"}'
        with mock.patch.object(app, "HEADER_TIMEOUT", 0.25), \
                mock.patch.object(app, "BODY_TIMEOUT", 1.5), \
                mock.patch.object(app, "BODY_IDLE_TIMEOUT", 0.8):
            sock = socket.create_connection(self.address, timeout=2)
            sock.settimeout(2)
            try:
                sock.sendall((f"POST /api/run HTTP/1.1\r\nHost: localhost\r\n"
                              f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n").encode("ascii"))
                sock.sendall(payload[:5])
                time.sleep(0.35)  # Longer than the header deadline, shorter than the body idle deadline.
                sock.sendall(payload[5:])
                response = bytearray()
                while True:
                    part = sock.recv(4096)
                    if not part:
                        break
                    response.extend(part)
            finally:
                sock.close()
        self.assertIn(b" 200 ", response.split(b"\r\n", 1)[0])
        body = bytes(response).split(b"\r\n\r\n", 1)[1]
        self.assertTrue(json.loads(body)["ok"])

    def test_busy_playground_returns_explicit_503(self):
        payload = b'{"source":"say 1"}'
        running = app._RUN_ADMISSION.acquire("127.0.0.1", wait=True)
        self.assertIsNotNone(running)
        try:
            response = self.raw_request(f"Content-Length: {len(payload)}\r\n", payload)
        finally:
            app._RUN_ADMISSION.release(running)
        self.assertIn(b" 503 ", response.split(b"\r\n", 1)[0])
        body = response.split(b"\r\n\r\n", 1)[1]
        result = json.loads(body)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "capacity")

    def test_live_http_connections_are_bounded(self):
        from http.server import BaseHTTPRequestHandler

        entered = threading.Event()
        release = threading.Event()

        class BlockingHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                entered.set()
                release.wait(timeout=2)
                self.send_response(200)
                self.send_header("Content-Length", "3")
                self.end_headers()
                self.wfile.write(b"ok\n")

            def log_message(self, _format, *_args):
                pass

        server = app.BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), BlockingHandler,
            max_connections=1, max_per_ip=1)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        first = socket.create_connection(server.server_address, timeout=2)
        second = None
        try:
            first.sendall(
                b"GET / HTTP/1.1\r\nHost: localhost\r\n"
                b"Connection: close\r\n\r\n")
            self.assertTrue(entered.wait(timeout=2))
            second = socket.create_connection(server.server_address, timeout=2)
            second.settimeout(2)
            response = second.recv(1024)
            self.assertIn(b" 503 ", response.split(b"\r\n", 1)[0])
        finally:
            release.set()
            first.settimeout(2)
            try:
                first.recv(1024)
            except OSError:
                pass
            first.close()
            if second is not None:
                second.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_ordinary_api_request_remains_compatible(self):
        conn = http.client.HTTPConnection(*self.address, timeout=3)
        payload = b'{"source":"say \\\"ok\\\""}'
        conn.request("POST", "/api/run", body=payload, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("ok", response.read().decode("utf-8"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
