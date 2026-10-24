#!/usr/bin/env python3
"""Latent Playground: compile Latent to Python in the browser and run it.

Stdlib only. Interop (`py "..."`, `java "..."`) and module imports are
disabled for safety; programs run in a subprocess with CPU, memory, time and
output limits.
"""
import json
import os
import resource
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LATENT_DIR = os.environ.get("LATENT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, LATENT_DIR)

import lex  # noqa: E402
import parse as parse_mod  # noqa: E402
import nodes  # noqa: E402
import gen_py  # noqa: E402
import latent  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_SRC = 20_000
MAX_OUT = 64_000
MAX_ERR = 8_000
MAX_PYTHON_BYTES = 64 * 1024
MAX_JSON_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST = MAX_SRC * 4 + 1000
TIMEOUT = 5
RUN_TIMEOUT = TIMEOUT + 1
# A child may use up to 256 MiB; one active child leaves room for the server
# and compiler within the playground's 512 MiB instance budget.
MAX_CONCURRENT_RUNS = 1
_RUN_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)
BODY_TIMEOUT = 5
BODY_IDLE_TIMEOUT = 2
HEADER_TIMEOUT = 10
READ_CHUNK = 8_192
TERMINATE_GRACE = 0.1
MAX_AST_DEPTH = 128
BLOCKED = (nodes.PyImport, nodes.JavaImport, nodes.ImportStmt)


class AstDepthError(ValueError):
    pass


def _walk(node, seen=None):
    seen = seen if seen is not None else set()
    stack = [(node, 0)]
    while stack:
        value, depth = stack.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, nodes.Node):
            depth += 1
            if depth > MAX_AST_DEPTH:
                line = getattr(value, "line", 1)
                col = getattr(value, "col", 1)
                raise AstDepthError(
                    f"{line}:{col}: source nesting exceeds maximum supported "
                    f"depth ({MAX_AST_DEPTH})")
            yield value
            children = list(vars(value).values())
        elif isinstance(value, (list, tuple)):
            children = list(value)
        elif isinstance(value, dict):
            children = list(value.values())
        else:
            continue
        for child in reversed(children):
            if isinstance(child, (nodes.Node, list, tuple, dict)):
                stack.append((child, depth))


def blocked_feature(src):
    try:
        tree = parse_mod.parse(lex.lex(src))
    except Exception:
        return None  # let the real compiler report the error
    for n in _walk(tree):
        if isinstance(n, BLOCKED):
            kind = {nodes.PyImport: "py interop", nodes.JavaImport: "java interop",
                    nodes.ImportStmt: "module imports"}[type(n)]
            return f"{getattr(n, 'line', 1)}:{getattr(n, 'col', 1)}: {kind} is disabled in the playground"
    return None


def _limits():
    cpu = max(1, int(TIMEOUT))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    mem = 256 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


def _compile_failure(src, message):
    return {
        "ok": False,
        "stage": "parse",
        "error": latent.render_error("main.lt", src, "parse", message),
    }


def _python_preview(code):
    encoded = code.encode("utf-8")
    if len(encoded) <= MAX_PYTHON_BYTES:
        return code, False
    preview = encoded[:MAX_PYTHON_BYTES].decode("utf-8", "ignore")
    return preview, True


def _stop_process_group(proc):
    """Stop the isolated child session, including descendants holding pipes."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + TERMINATE_GRACE
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    # A descendant may outlive the direct child (and keep the pipes open).
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def _run_program(src):
    if len(src) > MAX_SRC:
        return {"ok": False, "stage": "input", "error": f"source too long (max {MAX_SRC} chars)"}
    try:
        msg = blocked_feature(src)
        if msg:
            return {"ok": False, "stage": "playground", "error": latent.render_error("main.lt", src, "playground", msg)}
        prog, err = latent.compile_src(src, "main.lt")
    except AstDepthError as e:
        return _compile_failure(src, str(e))
    except RecursionError:
        return _compile_failure(
            src, "1:1: source nesting exceeds the compiler's safe depth")
    if err:
        kind, raw = err
        return {"ok": False, "stage": kind, "error": latent.render_error("main.lt", src, kind, raw)}
    try:
        code = gen_py.generate(prog, source_path="main.lt")
    except RecursionError:
        return _compile_failure(
            src, "1:1: source nesting exceeds the compiler's safe depth")
    except Exception as e:  # codegen bug
        return {"ok": False, "stage": "codegen", "error": f"codegen error: {e}"}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "main.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        env = {"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8", "HOME": d}
        proc = subprocess.Popen(
            [sys.executable, "-I", path], cwd=d, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, preexec_fn=_limits, start_new_session=True,
        )
        output = {"stdout": bytearray(), "stderr": bytearray()}
        limits = {"stdout": MAX_OUT, "stderr": MAX_ERR}
        selector = selectors.DefaultSelector()
        for name, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)

        deadline = time.monotonic() + RUN_TIMEOUT
        timed_out = False
        truncated = False
        stop = False
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = stop = True
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    name = key.data
                    try:
                        chunk = os.read(key.fd, READ_CHUNK)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    buf = output[name]
                    room = limits[name] - len(buf)
                    if len(chunk) > room:
                        if room > 0:
                            buf.extend(chunk[:room])
                        truncated = stop = True
                        break
                    buf.extend(chunk)
                if stop:
                    break

            if not stop:
                remaining = max(0, deadline - time.monotonic())
                try:
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = stop = True
        finally:
            if stop or proc.poll() is None:
                _stop_process_group(proc)
            selector.close()
            for pipe in (proc.stdout, proc.stderr):
                if not pipe.closed:
                    pipe.close()

        rc = proc.returncode if proc.returncode is not None else -9
    out = output["stdout"].decode("utf-8", "replace")
    errs = output["stderr"].decode("utf-8", "replace").replace(d + "/", "")
    if rc < 0 and not timed_out and not truncated and rc in (-9, -24):
        timed_out = True
    python_preview, python_truncated = _python_preview(code)
    return {"ok": rc == 0 and not truncated and not timed_out, "stage": "run", "stdout": out, "stderr": errs[-MAX_ERR:],
            "exit": rc, "timeout": timed_out, "truncated": truncated,
            "python": python_preview, "python_truncated": python_truncated}


def run_program(src, *, wait=True):
    """Run at most one program at a time; direct callers wait by default."""
    if not _RUN_SLOTS.acquire(blocking=wait):
        return {"ok": False, "stage": "capacity", "error": "playground is busy; retry shortly"}
    try:
        return _run_program(src)
    finally:
        _RUN_SLOTS.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "LatentPlayground/1.0"

    def setup(self):
        super().setup()
        # Bound idle/slow request headers; request bodies use a separate total deadline.
        self.connection.settimeout(HEADER_TIMEOUT)

    def handle_one_request(self):
        # socket.settimeout is an inactivity limit; this also bounds drip-fed headers.
        self._header_deadline_lock = threading.Lock()
        self._headers_parsed = False
        timer = threading.Timer(HEADER_TIMEOUT, self._expire_request)
        timer.daemon = True
        self._header_deadline_timer = timer
        timer.start()
        try:
            return super().handle_one_request()
        finally:
            timer.cancel()

    def parse_request(self):
        parsed = super().parse_request()
        # Do not let the header deadline race with body reads or program execution.
        with self._header_deadline_lock:
            self._headers_parsed = True
            self._header_deadline_timer.cancel()
        return parsed

    def _expire_request(self):
        with self._header_deadline_lock:
            if self._headers_parsed:
                return
            try:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RD)
            except OSError:
                pass

    def _send(self, status, body, ctype):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status, body):
        data = json.dumps(body).encode("utf-8")
        if len(data) > MAX_JSON_RESPONSE_BYTES:
            status = 500
            data = json.dumps({
                "ok": False,
                "stage": "response",
                "error": "response exceeded the maximum size",
            }).encode("utf-8")
        return self._send(status, data, "application/json")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/healthz":
            return self._send(200, "ok", "text/plain")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/run":
            return self._send(404, "not found", "text/plain")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            n = 0
        else:
            raw_length = raw_length.strip()
            if not raw_length or any(ch < "0" or ch > "9" for ch in raw_length):
                self.close_connection = True
                return self._send_json(400, {"ok": False, "error": "invalid content length"})
            normalized = raw_length.lstrip("0") or "0"
            maximum = str(MAX_REQUEST)
            if len(normalized) > len(maximum) or (len(normalized) == len(maximum) and normalized > maximum):
                self.close_connection = True
                return self._send_json(413, {"ok": False, "error": "too large"})
            n = int(normalized)
        if n > MAX_REQUEST:
            self.close_connection = True
            return self._send_json(413, {"ok": False, "error": "too large"})

        body = bytearray()
        deadline = time.monotonic() + BODY_TIMEOUT
        while len(body) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close_connection = True
                return self._send_json(408, {"ok": False, "error": "request body timed out"})
            self.connection.settimeout(min(BODY_IDLE_TIMEOUT, remaining))
            try:
                chunk = self.rfile.read1(min(READ_CHUNK, n - len(body)))
            except (socket.timeout, TimeoutError):
                self.close_connection = True
                return self._send_json(408, {"ok": False, "error": "request body timed out"})
            if not chunk:
                self.close_connection = True
                return self._send_json(400, {"ok": False, "error": "incomplete request body"})
            body.extend(chunk)
        self.connection.settimeout(HEADER_TIMEOUT)
        try:
            payload = json.loads(body or b"{}")
        except Exception:
            return self._send_json(400, {"ok": False, "error": "bad json"})
        if not isinstance(payload, dict):
            return self._send_json(
                400, {"ok": False, "error": "JSON body must be an object"})
        src = payload.get("source", "")
        res = run_program(str(src), wait=False)
        status = 503 if res.get("stage") == "capacity" else 200
        self._send_json(status, res)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"Latent Playground on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
