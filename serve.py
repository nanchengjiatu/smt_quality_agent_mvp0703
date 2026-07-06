"""Single-command server for the SMT quality agent MVP.

Serves the static frontend (``web/``) and the generated data (``output/``) from
the project root on one port, re-runs the analysis pipeline on demand, and
watches the active full SPI table so new data triggers an automatic update. Pure
standard library, no dependencies.

    python3 serve.py                      # run pipeline, watch, serve on :8502
    python3 serve.py --port 8080
    python3 serve.py --watch-interval 10  # poll full_excel0623 every 10s
    python3 serve.py --no-watch           # disable auto-update
    python3 serve.py --no-refresh-on-start

Endpoints:
    GET  /              -> redirect to /web/index.html
    GET  /web/*         -> static frontend files
    GET  /output/*      -> generated JSON data
    POST /api/refresh   -> re-run pipeline over the realtime window, return
                           per-stage status + version; body {"window_boards": 0}
                           forces an on-demand full-table run
    GET  /api/datasource -> current database config, password masked
    POST /api/datasource -> save database config
    POST /api/datasource/test -> test a database config
    POST /api/drilldown/chat -> rule-based Q&A for one trigger
    GET  /api/ontology  -> code-native SMT ontology snapshot
    GET  /api/rules     -> executable rule catalog for review
    GET  /api/status    -> freshness of each output file (no recompute)
    GET  /api/live      -> data version + last update time for live polling
"""

import argparse
import json
import os
import posixpath
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from smt_quality_agent.datasource import (
    masked_datasource,
    save_datasource,
    test_datasource,
)
from smt_quality_agent.drilldown_chat import build_chat_response
from smt_quality_agent.knowledge_base import rule_catalog
from smt_quality_agent.llm import masked_llm_config, save_llm_config, test_llm
from smt_quality_agent.ontology import ontology_snapshot
from smt_quality_agent.pipeline import (
    OUTPUT_DIR,
    STAGE_FILES,
    WARNING_ACKS_PATH,
    run_pipeline,
    source_fingerprint,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8502
DEFAULT_WATCH_INTERVAL = 5
DATABASE_OVERRIDE: str | None = None

# Live state survives restarts here so the data version keeps increasing and an
# unchanged datasource does not trigger a spurious refresh after a restart.
STATE_PATH = OUTPUT_DIR / "live_state.json"

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
    "window_boards": None,
    "loaded_boards": None,
}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_persisted_state() -> None:
    try:
        with STATE_PATH.open("r", encoding="utf-8") as file:
            saved = json.load(file)
    except (OSError, ValueError):
        return
    with _state_lock:
        _live["version"] = int(saved.get("version") or 0)
        _live["updated_at"] = saved.get("updated_at")
        _live["fingerprint"] = saved.get("fingerprint")
        _live["window_boards"] = saved.get("window_boards")
        _live["loaded_boards"] = saved.get("loaded_boards")


def _persist_state_locked() -> None:
    """Write the durable subset of _live; caller must hold _state_lock."""
    payload = {
        key: _live[key]
        for key in ("version", "updated_at", "fingerprint", "window_boards", "loaded_boards")
    }
    try:
        OUTPUT_DIR.mkdir(exist_ok=True)
        with STATE_PATH.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
    except OSError as exc:
        print(f"[state] persist failed: {exc}")


def run_and_record(database: str | None, window_boards: int | None = None) -> dict:
    """Run the pipeline under the lock and bump the live version."""
    with _pipeline_lock:
        report = run_pipeline(database, window_boards)
        try:
            fingerprint = source_fingerprint(database)
        except Exception:  # noqa: BLE001 - fingerprint is best-effort here
            fingerprint = None
    with _state_lock:
        if fingerprint is not None:
            _live["fingerprint"] = fingerprint
        _live["version"] += 1
        _live["updated_at"] = report["generated_at"]
        _live["window_boards"] = report.get("window_boards")
        _live["loaded_boards"] = report.get("loaded_boards")
        _persist_state_locked()
        report = {**report, "version": _live["version"]}
    return report


def record_warning_ack(warning_id: str) -> None:
    """Append one accepted-baseline id to output/warning_acks.json."""
    try:
        with WARNING_ACKS_PATH.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, ValueError):
        payload = {}
    accepted = payload.get("accepted")
    if not isinstance(accepted, dict):
        accepted = {key: "" for key in (accepted or [])}
    accepted[warning_id] = _now()
    OUTPUT_DIR.mkdir(exist_ok=True)
    with WARNING_ACKS_PATH.open("w", encoding="utf-8") as file:
        json.dump({"accepted": accepted}, file, ensure_ascii=False, indent=2)
        file.write("\n")


def load_drilldown_trigger(trigger_id: str) -> dict:
    path = OUTPUT_DIR / "drilldown.json"
    if not path.exists():
        raise FileNotFoundError("output/drilldown.json does not exist")
    with path.open("r", encoding="utf-8") as file:
        report = json.load(file)
    for trigger in report.get("triggers", []):
        if trigger.get("trigger_id") == trigger_id:
            return trigger
    raise KeyError(f"unknown trigger_id: {trigger_id}")


class Watcher(threading.Thread):
    """Polls the configured full SPI table and refreshes on change."""

    def __init__(self, database: str | None, interval: int):
        super().__init__(daemon=True)
        self.database = database
        self.interval = interval
        self._stop = threading.Event()

    def run(self) -> None:
        with _state_lock:
            _live["watching"] = True
        while not self._stop.is_set():
            try:
                fingerprint = source_fingerprint(self.database)
                with _state_lock:
                    _live["last_check"] = _now()
                    _live["last_error"] = None
                    changed = fingerprint != _live["fingerprint"]
                if changed:
                    print(f"[watch] datasource changed -> {fingerprint}, refreshing")
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

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def do_POST(self) -> None:  # noqa: N802 - required name
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/api/refresh":
            # Optional body {"window_boards": 0} forces an on-demand full-table
            # run; realtime runs use the configured sliding window.
            try:
                payload = self._read_json()
                window_boards = payload.get("window_boards")
                if window_boards is not None:
                    window_boards = max(0, int(window_boards))
            except (ValueError, TypeError) as exc:
                self._send_json(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json(run_and_record(DATABASE_OVERRIDE, window_boards))
            return
        if path == "/api/datasource/test":
            try:
                self._send_json(test_datasource(self._read_json()))
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.BAD_REQUEST,
                )
            return
        if path == "/api/datasource":
            try:
                config = save_datasource(self._read_json())
                self._send_json(masked_datasource(config))
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.BAD_REQUEST,
                )
            return
        if path == "/api/warning/accept-baseline":
            # Engineer accepted a step-shifted level as the pad's new normal:
            # persist the deterministic warning id, then recompute so the
            # early_warning stage restarts that pad's baseline from the shift.
            try:
                payload = self._read_json()
                accepted_id = str(payload.get("warning_id") or "").strip()
                if not accepted_id.startswith("WRN-"):
                    raise ValueError(f"invalid warning_id: {accepted_id!r}")
                record_warning_ack(accepted_id)
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json(run_and_record(DATABASE_OVERRIDE))
            return
        if path == "/api/llm":
            try:
                self._send_json(masked_llm_config(save_llm_config(self._read_json())))
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.BAD_REQUEST,
                )
            return
        if path == "/api/llm/test":
            try:
                self._send_json(test_llm(self._read_json()))
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.BAD_REQUEST,
                )
            return
        if path == "/api/drilldown/chat":
            try:
                payload = self._read_json()
                trigger = load_drilldown_trigger(str(payload.get("trigger_id") or ""))
                self._send_json(build_chat_response(trigger, str(payload.get("question") or "")))
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.BAD_REQUEST,
                )
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
        if path.rstrip("/") == "/api/datasource":
            self._send_json(masked_datasource())
            return
        if path.rstrip("/") == "/api/llm":
            self._send_json(masked_llm_config())
            return
        if path.rstrip("/") == "/api/live":
            with _state_lock:
                self._send_json(dict(_live))
            return
        if path.rstrip("/") == "/api/ontology":
            self._send_json(ontology_snapshot())
            return
        if path.rstrip("/") == "/api/rules":
            self._send_json(rule_catalog())
            return
        if self._static_allowed(path):
            super().do_GET()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_HEAD(self) -> None:  # noqa: N802 - required name
        if self._static_allowed(self.path.split("?", 1)[0]):
            super().do_HEAD()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    @staticmethod
    def _static_allowed(path: str) -> bool:
        """Only the frontend and generated JSON may be served statically.

        The project root also holds .git, raw production exports (*.xlsx), and
        config/datasource.json with database credentials; the port is open to
        the internet, so everything outside web/ and output/ stays private.
        Normalize before checking so encoded ../ cannot escape the whitelist.
        """
        clean = posixpath.normpath(unquote(path))
        return clean in ("/web", "/output") or clean.startswith(("/web/", "/output/"))

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
    parser.add_argument("--database", default=None, help="override datasource database name")
    parser.add_argument(
        "--watch-interval",
        type=int,
        default=DEFAULT_WATCH_INTERVAL,
        help="seconds between datasource change checks (default 5)",
    )
    parser.add_argument("--no-watch", action="store_true", help="disable auto-update")
    parser.add_argument(
        "--no-refresh-on-start",
        action="store_true",
        help="skip the initial pipeline run (serve whatever is in output/)",
    )
    args = parser.parse_args()

    global DATABASE_OVERRIDE
    DATABASE_OVERRIDE = args.database
    os.chdir(ROOT)

    load_persisted_state()
    if not args.no_refresh_on_start:
        print("启动前先跑一次 pipeline ...")
        report = run_and_record(DATABASE_OVERRIDE)
        for stage in report["stages"]:
            mark = "OK " if stage["ok"] else "FAIL"
            extra = f"{stage.get('rows', 0)} 行 {stage['ms']}ms" if stage["ok"] else stage.get("error", "")
            print(f"  [{mark}] {stage['stage']}: {extra}")
    # In cached mode the persisted fingerprint (if any) carries over, so the
    # watcher refreshes only when the datasource actually changed since the
    # last run; without persisted state it establishes a fingerprint when
    # PostgreSQL is reachable and refreshes from that point onward.

    watcher = None
    if not args.no_watch:
        watcher = Watcher(DATABASE_OVERRIDE, args.watch_interval)
        watcher.start()
        print(f"实时监听已开启: 每 {args.watch_interval}s 检查数据源")

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
