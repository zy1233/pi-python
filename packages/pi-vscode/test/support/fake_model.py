"""A fake OpenAI-format model server for the tests of the extension, on 127.0.0.1.

    python fake_model.py [--expect-key KEY]

It reuses the server of ``packaging/agent/smoke.py``. It prints one line, ``PORT <n>``, and then
serves until it is killed. With ``--expect-key`` a request that does not carry ``Bearer KEY`` gets
a 401, as a provider answers a wrong key. Any other prompt than ``RUN-SMOKE`` is answered with the
text ``SUB-ANSWER``.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "packaging" / "agent"))

import smoke  # noqa: E402


def make_handler(expected_key: str | None) -> type[smoke.FakeModel]:
    class Handler(smoke.FakeModel):
        def do_POST(self) -> None:
            if expected_key is not None and self.headers.get("Authorization") != (
                f"Bearer {expected_key}"
            ):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                body = json.dumps(
                    {
                        "error": {
                            "message": "Incorrect API key provided.",
                            "type": "invalid_request_error",
                            "code": "invalid_api_key",
                        }
                    }
                ).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_POST()

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-key")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(args.expect_key))
    print(f"PORT {server.server_address[1]}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
