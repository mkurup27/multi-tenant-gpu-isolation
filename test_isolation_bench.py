#!/usr/bin/env python3
"""Local protocol smoke test; no GPU or external network required."""

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        body = (
            "# TYPE vllm:num_requests_waiting gauge\n"
            "vllm:num_requests_waiting 0\n"
            "# TYPE vllm:num_preemptions_total counter\n"
            "vllm:num_preemptions_total 0\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        chunks = [
            {"choices": [{"delta": {"content": "one"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": " two"}, "finish_reason": None}]},
            {
                "choices": [{"delta": {}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        ]
        payload = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        payload += "data: [DONE]\n\n"
        body = payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BenchmarkSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_writes_complete_schema_v2_result(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "smoke"
            url = f"http://127.0.0.1:{self.server.server_port}"
            subprocess.run(
                [
                    sys.executable,
                    "isolation_bench.py",
                    "--base-url",
                    f"{url}/v1",
                    "--metrics-url",
                    f"{url}/metrics",
                    "--model",
                    "mock",
                    "--arrival",
                    "poisson",
                    "--rate",
                    "5",
                    "--max-outstanding",
                    "4",
                    "--warmup",
                    "0.2",
                    "--duration",
                    "0.5",
                    "--label",
                    "smoke",
                    "--out",
                    str(prefix),
                ],
                check=True,
                timeout=20,
            )
            summary = json.loads(Path(f"{prefix}.summary.json").read_text())
            self.assertEqual(summary["schema_version"], 2)
            self.assertGreater(summary["counts"]["completed_ok"], 0)
            self.assertEqual(summary["counts"]["request_errors"], 0)
            self.assertGreater(summary["ttft_s"]["n"], 0)
            self.assertGreater(summary["tpot_s"]["n"], 0)
            self.assertGreaterEqual(summary["server_metrics"]["successful_samples"], 1)
            self.assertTrue(Path(f"{prefix}.jsonl").exists())
            self.assertTrue(Path(f"{prefix}.metrics.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
