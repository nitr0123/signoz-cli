"""End-to-end test for install.sh index mode (stubbed installer)."""

import base64
import os
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INDEX_HTML = """<!DOCTYPE html><html><body>
<h1>Links for signoz-cli</h1>
<a href="signoz_cli-0.5.16-py3-none-any.whl#sha256=aaa">signoz_cli-0.5.16-py3-none-any.whl</a><br>
<a href="signoz_cli-0.5.16.tar.gz#sha256=bbb">signoz_cli-0.5.16.tar.gz</a><br>
<a href="signoz_cli-0.5.18-py3-none-any.whl#sha256=ccc">signoz_cli-0.5.18-py3-none-any.whl</a><br>
<a href="other-pkg-1.0-py3-none-any.whl">other-pkg-1.0-py3-none-any.whl</a><br>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    seen_auth = []

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        type(self).seen_auth.append(auth)
        if auth != "Basic " + base64.b64encode(b"u:t0k").decode():
            self.send_response(401)
            self.end_headers()
            return
        body = INDEX_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _write_stub(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP)


def test_install_sh_index_mode(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bindir = tmp_path / "bin"
        bindir.mkdir()
        pipx_log = tmp_path / "pipx.log"
        _write_stub(
            str(bindir / "pipx"),
            "#!/bin/sh\necho \"$@\" >> '" + str(pipx_log) + "'\n",
        )
        _write_stub(
            str(bindir / "signoz"),
            '#!/bin/sh\necho "signoz-cli, version 0.5.18"\n',
        )
        env = dict(os.environ)
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        env["SIGNOZ_PYPI_INDEX"] = (
            f"http://u:t0k@127.0.0.1:{port}/api/packages/hamza/pypi/simple"
        )
        proc = subprocess.run(
            ["sh", os.path.join(REPO_ROOT, "install.sh")],
            capture_output=True, text=True, env=env, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        # Newest version resolved from the index (not the older 0.5.16).
        assert "signoz-cli==0.5.18" in pipx_log.read_text()
        assert "0.5.18" in proc.stdout or "0.5.18" in proc.stderr
        # Credentials traveled in a header, and at least one request
        # carried them (proves the userinfo/auth plumbing end to end).
        assert any(a.startswith("Basic ") for a in _Handler.seen_auth)
    finally:
        server.shutdown()
        thread.join(timeout=5)
