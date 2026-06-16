"""Single-command server for the SMT quality agent MVP.

Serves the static frontend (``web/``) and the generated data (``output/``) from
the project root on one port, re-runs the analysis pipeline on demand, and
watches the over_volume table so new data triggers an automatic update. Pure
standard library, no dependencies.

    python3 serve.py                      # run pipeline, watch, serve on :8502
    python3 serve.py --port 8080
    python3 serve.py --watch-interval 10  # poll over_volume every 10s
    python3 serve.py --no-watch           # disable auto-update
    python3 serve.py --no-refresh-on-start

Endpoints:
    GET  /              -> redirect to /web/index.html
    GET  /web/*         -> static frontend files
    GET  /output/*      -> generated JSON data
    POST /api/refresh   -> re-run pipeline, return per-stage status + version
    GET  /api/status    -> freshness of each output file (no recompute)
    GET  /api/live      -> data version + last update time for live polling
"""

import argparse
import json
import os
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from smt_quality_agent.pipeline import (
    DEFAULT_DATABASE,
    OUTPUT_DIR,
    STAGE_FILES,
    over_volume_fingerprint,
    run_pipeline,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8502
DEFAULT_WATCH_INTERVAL = 5
DATABASE = DEFAULT_DATABASE

# Serializes pipeline runs so manual refresh and the watcher never write the
# output files concurrently.
_pipeline_lock = threading.Lock()

# Live state shared with the frontend via /api/live. ``version`` increments on
# every pipeline run; the frontend reloads its data when it sees a new one.
_state_lock = threading.Lock()
_live = {
    "version": 0,
    "updated_at": None,
    "fingerprint": None,
    "watching": False,
    "last_check": None,
    "last_error": None,
}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def run_and_record(database: str) -> dict:
    """Run the pipeline under the lock and bump the live version."""
    with _pipeline_lock:
        report = run_pipeline(database)
        try:
            fingerprint = over_volume_fingerprint(database)
        except Exception:  # noqa: BLE001 - fingerprint is best-effort here
            fingerprint = None
    with _state_lock:
        if fingerprint is not None:
            _live["fingerprint"] = fingerprint
        _live["version"] += 1
        _live["updated_at"] = report["generated_at"]
        report = {**report, "version": _live["version"]}
    return report


class Watcher(threading.Thread):
    """Polls over_volume; re-runs the pipeline when its fingerprint changes."""

    def __init__(self, database: str, interval: int):
        super().__init__(daemon=True)
        self.database = database
        self.interval = interval
        self._stop = threading.Event()

    def run(self) -> None:
        with _state_lock:
            _live["watching"] = True
        while not self._stop.is_set():
            try:
                fingerprint = over_volume_fingerprint(self.database)
                with _state_lock:
                    _live["last_check"] = _now()
                    _live["last_error"] = None
                    changed = fingerprint != _live["fingerprint"]
                if changed:
                    print(f"[watch] over_volume changed -> {fingerprint}, refreshing")
                    run_and_record(self.database)
            except Exception as exc:  # noqa: BLE001 - keep the watcher alive
                with _state_lock:
                    _live["last_check"] = _now()
                    _live["last_error"] = f"{type(exc).__name__}: {exc}"
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()


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
            self._send_json(run_and_record(DATABASE))
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
        if path.rstrip("/") == "/api/live":
            with _state_lock:
                self._send_json(dict(_live))
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
        # Keep API calls visible, mute the static-file and poll noise.
        request = args[0] if args and isinstance(args[0], str) else ""
        if "/api/" in request and "/api/live" not in request:
            super().log_message(fmt, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument(
        "--watch-interval",
        type=int,
        default=DEFAULT_WATCH_INTERVAL,
        help="seconds between over_volume change checks (default 5)",
    )
    parser.add_argument("--no-watch", action="store_true", help="disable auto-update")
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
        report = run_and_record(DATABASE)
        for stage in report["stages"]:
            mark = "OK " if stage["ok"] else "FAIL"
            extra = f"{stage.get('rows', 0)} 行 {stage['ms']}ms" if stage["ok"] else stage.get("error", "")
            print(f"  [{mark}] {stage['stage']}: {extra}")
    else:
        # Seed the fingerprint so the watcher does not fire spuriously at start.
        try:
            with _state_lock:
                _live["fingerprint"] = over_volume_fingerprint(DATABASE)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 初始指纹获取失败: {exc}")

    watcher = None
    if not args.no_watch:
        watcher = Watcher(DATABASE, args.watch_interval)
        watcher.start()
        print(f"实时监听已开启: 每 {args.watch_interval}s 检查 over_volume")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"\n服务已启动: http://0.0.0.0:{args.port}/  (Ctrl+C 停止)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        if watcher:
            watcher.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
