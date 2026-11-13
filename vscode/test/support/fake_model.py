"""A fake OpenAI-format model server for the tests of the extension, on 127.0.0.1.

    python fake_model.py [--expect-key KEY] [--log FILE]

It reuses the server of ``packaging/agent/smoke.py``. It prints one line, ``PORT <n>``, and then
serves until it is killed. With ``--expect-key`` a request that does not carry ``Bearer KEY`` gets
a 401, as a provider answers a wrong key. Any other prompt than ``RUN-SMOKE`` is answered with the
text ``SUB-ANSWER``. With ``--log`` each request body is appended to FILE as one JSON line, so a
test can see what the agent asked the provider for (for example ``reasoning_effort``). A body holds
the messages and the parameters, and never the key.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "packaging" / "agent"))

import smoke  # noqa: E402


def make_handler(expected_key: str | None, log: Path | None) -> type[smoke.FakeModel]:
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
            if log is not None:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(raw.decode("utf-8", "replace").replace("\n", " ") + "\n")
                self.rfile = io.BytesIO(raw)
            super().do_POST()

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-key")
    parser.add_argument("--log")
    args = parser.parse_args()
    log = Path(args.log) if args.log else None
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(args.expect_key, log))
    print(f"PORT {server.server_address[1]}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
