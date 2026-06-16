"""Single-command server for the SMT quality agent MVP.

Serves the static frontend (``web/``) and the generated data (``output/``) from
the project root on one port, and exposes a refresh endpoint that re-runs the
analysis pipeline on demand. Pure standard library, no dependencies.

    python3 serve.py                 # run pipeline once, then serve on :8502
    python3 serve.py --port 8080     # use a different port
    python3 serve.py --no-refresh-on-start

Endpoints:
    GET  /              -> redirect to /web/index.html
    GET  /web/*         -> static frontend files
    GET  /output/*      -> generated JSON data
    POST /api/refresh   -> re-run pipeline, return per-stage status JSON
    GET  /api/status    -> freshness of each output file (no recompute)
"""

import argparse
import json
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from smt_quality_agent.pipeline import (
    DEFAULT_DATABASE,
    OUTPUT_DIR,
    STAGE_FILES,
    run_pipeline,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8502
DATABASE = DEFAULT_DATABASE


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - required name
        if self.path.rstrip("/") == "/api/refresh":
            report = run_pipeline(DATABASE)
            self._send_json(report)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def do_GET(self) -> None:  # noqa: N802 - required name
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/web/index.html")
            self.end_headers()
            return
        if path.rstrip("/") == "/api/status":
            self._send_json(self._status_report())
            return
        super().do_GET()

    def _status_report(self) -> dict:
        stages = []
        for stage, files in STAGE_FILES.items():
            entries = []
            for name in files:
                file_path = OUTPUT_DIR / name
                if file_path.exists():
                    entries.append({
                        "file": name,
                        "exists": True,
                        "mtime": int(file_path.stat().st_mtime),
                    })
                else:
                    entries.append({"file": name, "exists": False})
            stages.append({"stage": stage, "files": entries})
        return {"stages": stages}

    def log_message(self, fmt: str, *args) -> None:
        # Keep API calls visible, mute the static-file noise.
        if isinstance(args[0], str) and "/api/" in args[0]:
            super().log_message(fmt, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument(
        "--no-refresh-on-start",
        action="store_true",
        help="skip the initial pipeline run (serve whatever is in output/)",
    )
    args = parser.parse_args()

    global DATABASE
    DATABASE = args.database
    os.chdir(ROOT)

    if not args.no_refresh_on_start:
        print("启动前先跑一次 pipeline ...")
        report = run_pipeline(DATABASE)
        for stage in report["stages"]:
            mark = "OK " if stage["ok"] else "FAIL"
            extra = f"{stage.get('rows', 0)} 行 {stage['ms']}ms" if stage["ok"] else stage.get("error", "")
            print(f"  [{mark}] {stage['stage']}: {extra}")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"\n服务已启动: http://0.0.0.0:{args.port}/  (Ctrl+C 停止)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
