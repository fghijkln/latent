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
MAX_REQUEST = MAX_SRC * 4 + 1000
TIMEOUT = 5
RUN_TIMEOUT = TIMEOUT + 1
BODY_TIMEOUT = 5
BODY_IDLE_TIMEOUT = 2
HEADER_TIMEOUT = 10
READ_CHUNK = 8_192
TERMINATE_GRACE = 0.1
BLOCKED = (nodes.PyImport, nodes.JavaImport, nodes.ImportStmt)


def _walk(node, seen=None):
    seen = seen if seen is not None else set()
    if id(node) in seen:
        return
    seen.add(id(node))
    yield node
    if isinstance(node, nodes.Node):
        children = vars(node).values()
    elif isinstance(node, (list, tuple)):
        children = node
    elif isinstance(node, dict):
        children = node.values()
    else:
        return
    for c in children:
        if isinstance(c, (nodes.Node, list, tuple, dict)):
            yield from _walk(c, seen)


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


def run_program(src):
    if len(src) > MAX_SRC:
        return {"ok": False, "stage": "input", "error": f"source too long (max {MAX_SRC} chars)"}
    msg = blocked_feature(src)
    if msg:
        return {"ok": False, "stage": "playground", "error": latent.render_error("main.lt", src, "playground", msg)}
    prog, err = latent.compile_src(src, "main.lt")
    if err:
        kind, raw = err
        return {"ok": False, "stage": kind, "error": latent.render_error("main.lt", src, kind, raw)}
    try:
        code = gen_py.generate(prog, source_path="main.lt")
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
    return {"ok": rc == 0, "stage": "run", "stdout": out, "stderr": errs[-MAX_ERR:],
            "exit": rc, "timeout": timed_out, "truncated": truncated, "python": code}


class Handler(BaseHTTPRequestHandler):
    server_version = "LatentPlayground/1.0"

    def setup(self):
        super().setup()
        # Bound idle/slow request headers; request bodies use a separate total deadline.
        self.connection.settimeout(HEADER_TIMEOUT)

    def handle_one_request(self):
        # socket.settimeout is an inactivity limit; this also bounds drip-fed headers.
        timer = threading.Timer(HEADER_TIMEOUT, self._expire_request)
        timer.daemon = True
        timer.start()
        try:
            return super().handle_one_request()
        finally:
            timer.cancel()

    def _expire_request(self):
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
                return self._send(400, json.dumps({"ok": False, "error": "invalid content length"}),
                                  "application/json")
            normalized = raw_length.lstrip("0") or "0"
            maximum = str(MAX_REQUEST)
            if len(normalized) > len(maximum) or (len(normalized) == len(maximum) and normalized > maximum):
                self.close_connection = True
                return self._send(413, json.dumps({"ok": False, "error": "too large"}), "application/json")
            n = int(normalized)
        if n > MAX_REQUEST:
            self.close_connection = True
            return self._send(413, json.dumps({"ok": False, "error": "too large"}), "application/json")

        body = bytearray()
        deadline = time.monotonic() + BODY_TIMEOUT
        while len(body) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close_connection = True
                return self._send(408, json.dumps({"ok": False, "error": "request body timed out"}),
                                  "application/json")
            self.connection.settimeout(min(BODY_IDLE_TIMEOUT, remaining))
            try:
                chunk = self.rfile.read1(min(READ_CHUNK, n - len(body)))
            except (socket.timeout, TimeoutError):
                self.close_connection = True
                return self._send(408, json.dumps({"ok": False, "error": "request body timed out"}),
                                  "application/json")
            if not chunk:
                self.close_connection = True
                return self._send(400, json.dumps({"ok": False, "error": "incomplete request body"}),
                                  "application/json")
            body.extend(chunk)
        self.connection.settimeout(HEADER_TIMEOUT)
        try:
            src = json.loads(body or b"{}").get("source", "")
        except Exception:
            return self._send(400, json.dumps({"ok": False, "error": "bad json"}), "application/json")
        res = run_program(str(src))
        self._send(200, json.dumps(res), "application/json")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"Latent Playground on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
