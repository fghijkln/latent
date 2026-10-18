#!/usr/bin/env python3
"""Latent Playground: compile Latent to Python in the browser and run it.

Stdlib only. Interop (`py "..."`, `java "..."`) and module imports are
disabled for safety; programs run in a subprocess with CPU, memory, time and
output limits.
"""
import json
import os
import resource
import subprocess
import sys
import tempfile
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
TIMEOUT = 5
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
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT))
    mem = 256 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    os.setsid()


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
        try:
            r = subprocess.run([sys.executable, "-I", path], cwd=d, env=env,
                               capture_output=True, timeout=TIMEOUT + 1,
                               preexec_fn=_limits, stdin=subprocess.DEVNULL)
            out = r.stdout.decode("utf-8", "replace")
            errs = r.stderr.decode("utf-8", "replace").replace(d + "/", "")
            rc = r.returncode
            timed_out = False
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode("utf-8", "replace")
            errs = ""
            rc = -9
            timed_out = True
    if rc < 0 and not timed_out and rc in (-9, -24):
        timed_out = True
    trunc = len(out) > MAX_OUT
    return {"ok": rc == 0, "stage": "run", "stdout": out[:MAX_OUT], "stderr": errs[-8000:],
            "exit": rc, "timeout": timed_out, "truncated": trunc, "python": code}


class Handler(BaseHTTPRequestHandler):
    server_version = "LatentPlayground/1.0"

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
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_SRC * 4 + 1000:
            return self._send(413, json.dumps({"ok": False, "error": "too large"}), "application/json")
        try:
            src = json.loads(self.rfile.read(n) or b"{}").get("source", "")
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
