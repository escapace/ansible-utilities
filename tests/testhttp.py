"""Exercise installed GitHub helper roles against a local scripted server."""

from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = b"verified shared artifact contents\n"
CHECKSUM = "sha256:" + hashlib.sha256(ARTIFACT).hexdigest()


class HttpRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installation = tempfile.TemporaryDirectory(
            prefix="escapace-utilities-http-"
        )
        cls.addClassCleanup(cls.installation.cleanup)
        cls.environment = dict(os.environ)
        cls.environment.pop("ANSIBLE_COLLECTIONS_PATHS", None)
        cls.environment.pop("ANSIBLE_FILTER_PLUGINS", None)
        cls.environment.update(
            ANSIBLE_COLLECTIONS_PATH=str(Path(cls.installation.name) / "collections"),
            ANSIBLE_HOME=str(Path(cls.installation.name) / ".ansible"),
            ANSIBLE_NOCOLOR="1",
            ANSIBLE_INJECT_FACT_VARS="false",
        )
        result = subprocess.run(
            [
                str(Path(sys.executable).parent / "ansible-galaxy"),
                "collection",
                "install",
                str(ROOT),
                "--no-deps",
            ],
            cwd=ROOT,
            env=cls.environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode or "[WARNING]" in result.stdout + result.stderr:
            raise AssertionError(result.stdout + result.stderr)

    def setUp(self):
        self.events = defaultdict(list)
        self.lock = threading.Lock()
        events, lock = self.events, self.lock

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond(send_body=True)

            def do_HEAD(self):
                self.respond(send_body=False)

            def respond(self, send_body):
                parts = self.path.strip("/").split("/")
                if len(parts) != 2:
                    self.send_error(404)
                    return
                case, host = parts
                with lock:
                    events[(case, host)].append(time.monotonic())
                    attempt = len(events[(case, host)])
                status = 200
                headers = {}
                if case in ("transient", "reset-state") and attempt == 1:
                    status = 503
                elif case == "exhausted":
                    status = 503
                elif case == "retry-after" and attempt == 1:
                    status, headers = 429, {"Retry-After": "3"}
                elif case == "reset" and attempt == 1:
                    status = 403
                    headers = {
                        "Retry-After": "1",
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(time.time()) + 3),
                    }
                elif case == "over-budget":
                    status, headers = 429, {"Retry-After": "3600"}
                elif case == "bad-header":
                    status, headers = 429, {"Retry-After": "invalid"}
                elif case == "missing":
                    status = 404
                elif case == "permission":
                    status = 403
                elif (
                    case in ("archive-transient", "archive-reset-state")
                    and attempt == 1
                ):
                    status = 503
                elif case == "archive-exhausted":
                    status = 503
                elif case == "archive-missing":
                    status = 404
                elif case == "archive-permission":
                    status = 403
                elif case == "archive-limited":
                    status, headers = 429, {"Retry-After": "3600"}
                payload = json.dumps(
                    {
                        "host": host,
                        "attempt": attempt,
                        "message": "local server diagnostic",
                    }
                ).encode()
                content_type = "application/json"
                if case.startswith("archive-") and status == 200:
                    payload, content_type = ARTIFACT, "application/octet-stream"
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if send_body:
                    self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def run_playbook(self, cases, check=False, playbook="metadata.yaml"):
        with tempfile.TemporaryDirectory(
            prefix="escapace-utilities-http-case-"
        ) as directory:
            inventory = Path(directory) / "inventory"
            inventory.write_text(
                "\n".join(
                    "%s ansible_connection=local ansible_python_interpreter=%s"
                    % (host, sys.executable)
                    for host in ("host_a", "host_b")
                )
            )
            command = [
                str(Path(sys.executable).parent / "ansible-playbook"),
                "-i",
                str(inventory),
                str(ROOT / "tests/integration" / playbook),
                "-e",
                json.dumps(
                    {
                        "utilities_test_port": self.server.server_port,
                        "utilities_test_cases": cases,
                        "utilities_test_directory": directory,
                        "utilities_test_owner": str(os.getuid()),
                        "utilities_test_group": str(os.getgid()),
                        "utilities_test_checksum": CHECKSUM,
                        "utilities_test_content": ARTIFACT.decode(),
                    }
                ),
            ]
            if check:
                command.append("--check")
            environment = self.environment
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=240,
            )
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn("[WARNING]", output)
            return output

    def test_http_sequences_and_independent_hosts(self):
        cases = [
            {"name": "success", "success": True},
            {"name": "transient", "success": True},
            {"name": "retry-after", "success": True},
            {"name": "reset", "success": True},
            {
                "name": "missing",
                "success": False,
                "reason": "permanent or unclassified",
            },
            {
                "name": "permission",
                "success": False,
                "reason": "permanent or unclassified",
            },
            {"name": "bad-header", "success": False, "reason": "invalid retry headers"},
            {
                "name": "over-budget",
                "success": False,
                "reason": "retry no earlier than",
            },
            {"name": "exhausted", "success": False, "reason": "attempt limit reached"},
            {"name": "reset-state", "success": True, "return_content": True},
        ]
        self.run_playbook(cases)
        for host in ("host_a", "host_b"):
            for case in cases:
                name = case["name"]
                expected = (
                    4
                    if name == "exhausted"
                    else (
                        2
                        if name in ("transient", "retry-after", "reset", "reset-state")
                        else 1
                    )
                )
                with self.subTest(host=host, case=name):
                    requests = self.events[(name, host)]
                    self.assertEqual(len(requests), expected)
                    if expected > 1:
                        minimum = (
                            4 if name == "reset" else 3 if name == "retry-after" else 2
                        )
                        self.assertGreaterEqual(requests[1] - requests[0], minimum)
                    if name == "exhausted":
                        self.assertGreaterEqual(requests[-1] - requests[0], 14)

    def test_check_mode_still_observes_and_waits(self):
        output = self.run_playbook(
            [
                {"name": "retry-after", "success": True},
                {
                    "name": "missing",
                    "success": False,
                    "reason": "permanent or unclassified",
                },
            ],
            check=True,
        )
        self.assertIn("changed=0", output)
        for host in ("host_a", "host_b"):
            requests = self.events[("retry-after", host)]
            self.assertEqual(len(requests), 2)
            self.assertGreaterEqual(requests[1] - requests[0], 3)
            self.assertEqual(len(self.events[("missing", host)]), 1)

    def test_downloads_verify_checksum_and_bound_retries(self):
        cases = [
            {"name": "archive-success", "success": True},
            {"name": "archive-transient", "success": True},
            {
                "name": "archive-missing",
                "success": False,
                "reason": "permanent or unclassified",
            },
            {
                "name": "archive-permission",
                "success": False,
                "reason": "permanent or unclassified",
            },
            {
                "name": "archive-limited",
                "success": False,
                "reason": "permanent or unclassified",
            },
            {"name": "archive-checksum", "success": False, "reason": "checksum"},
            {
                "name": "archive-exhausted",
                "success": False,
                "reason": "attempt limit reached",
            },
            {"name": "archive-reset-state", "success": True},
        ]
        self.run_playbook(cases, playbook="downloads.yaml")
        for host in ("host_a", "host_b"):
            for case in cases:
                name = case["name"]
                expected = (
                    4
                    if name == "archive-exhausted"
                    else (
                        2 if name in ("archive-transient", "archive-reset-state") else 1
                    )
                )
                with self.subTest(host=host, case=name):
                    requests = self.events[(name, host)]
                    self.assertEqual(len(requests), expected)
                    if expected > 1:
                        self.assertGreaterEqual(requests[1] - requests[0], 2)

    def test_download_check_mode_does_not_write(self):
        self.run_playbook(
            [
                {"name": "archive-transient", "success": True},
                {
                    "name": "archive-limited",
                    "success": False,
                    "reason": "permanent or unclassified",
                },
            ],
            check=True,
            playbook="downloads.yaml",
        )
        for host in ("host_a", "host_b"):
            self.assertEqual(len(self.events[("archive-transient", host)]), 2)
            self.assertEqual(len(self.events[("archive-limited", host)]), 1)

    def test_identical_repeated_calls_preserve_saved_results(self):
        self.run_playbook([], playbook="repeated.yaml")
        for host in ("host_a", "host_b"):
            self.assertEqual(len(self.events[("success", host)]), 2)
            self.assertEqual(len(self.events[("archive-success", host)]), 1)

    def test_invalid_inputs_fail_before_requests(self):
        cases = [
            {
                "kind": "request",
                "url": "http://example.com/not-allowed",
                "reason": "HTTPS",
            },
            {
                "kind": "request",
                "url": "https://user:password@github.com/x",
                "reason": "credentials",
            },
            {"kind": "request", "return_content": "false", "reason": "boolean"},
            {
                "kind": "download",
                "url": "http://example.com/not-allowed",
                "reason": "HTTPS",
            },
            {"kind": "download", "checksum": "sha256:invalid", "reason": "SHA-256"},
            {"kind": "download", "dest": "relative-path", "reason": "absolute"},
            {"kind": "download", "missing_parent": True, "reason": "parent directory"},
            {"kind": "download", "dest_kind": "directory", "reason": "regular file"},
            {"kind": "download", "dest_kind": "link", "reason": "regular file"},
            {"kind": "download", "mode": "777", "reason": "octal"},
        ]
        self.run_playbook(cases, playbook="invalid-input.yaml")
        self.assertEqual(dict(self.events), {})


if __name__ == "__main__":
    unittest.main()
