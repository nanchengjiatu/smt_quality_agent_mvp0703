"""Database datasource configuration for the SMT quality agent."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "datasource.json"

DEFAULT_DATASOURCE: dict[str, Any] = {
    "type": "postgresql",
    "host": "",
    "port": 5432,
    "database": "l780db",
    "user": "",
    "password": "",
    "tables": {
        "full_spi": "full_excel0623",
        "ng_events": "over_volume",
    },
    "fields": {
        "time": "fdate",
        "board": "barcode",
        "component_pad": "compname",
        "defect": "comp_errname",
    },
    "refresh_interval_seconds": 30,
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_datasource() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return deep_merge(DEFAULT_DATASOURCE, {})
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return normalize_datasource(payload)


def normalize_datasource(payload: dict[str, Any]) -> dict[str, Any]:
    config = deep_merge(DEFAULT_DATASOURCE, payload or {})
    config["type"] = "postgresql"
    config["port"] = int(config.get("port") or 5432)
    config["refresh_interval_seconds"] = int(config.get("refresh_interval_seconds") or 30)
    config["database"] = str(config.get("database") or DEFAULT_DATASOURCE["database"]).strip()
    config["host"] = str(config.get("host") or "").strip()
    config["user"] = str(config.get("user") or "").strip()
    config["password"] = str(config.get("password") or "")
    config["tables"]["full_spi"] = str(config["tables"].get("full_spi") or "full_excel0623").strip()
    config["tables"]["ng_events"] = str(config["tables"].get("ng_events") or "over_volume").strip()
    config["fields"]["time"] = str(config["fields"].get("time") or "fdate").strip()
    return config


def masked_datasource(config: dict[str, Any] | None = None) -> dict[str, Any]:
    masked = json.loads(json.dumps(config or load_datasource(), ensure_ascii=False))
    if masked.get("password"):
        masked["password"] = "******"
        masked["password_set"] = True
    else:
        masked["password"] = ""
        masked["password_set"] = False
    masked["config_exists"] = CONFIG_PATH.exists()
    return masked


def save_datasource(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_datasource()
    incoming = dict(payload or {})
    if incoming.get("password") == "******":
        incoming["password"] = current.get("password", "")
    config = normalize_datasource(incoming)
    CONFIG_DIR.mkdir(exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return config


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def qualified_table(config: dict[str, Any], table_key: str = "full_spi") -> str:
    table = config["tables"][table_key]
    if "." in table:
        return ".".join(quote_identifier(part) for part in table.split(".") if part)
    return f"public.{quote_identifier(table)}"


def psql_base_command(config: dict[str, Any]) -> list[str]:
    command = ["psql", "-X", "-d", config["database"]]
    if config.get("host"):
        command.extend(["-h", config["host"]])
    if config.get("port"):
        command.extend(["-p", str(config["port"])])
    if config.get("user"):
        command.extend(["-U", config["user"]])
    return command


def psql_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    if config.get("password"):
        env["PGPASSWORD"] = config["password"]
    return env


def run_psql(config: dict[str, Any], query: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*psql_base_command(config), "-t", "-A", "-c", query],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=psql_env(config),
    )


def source_table_label(config: dict[str, Any]) -> str:
    return f"{config['database']}.{qualified_table(config)}"


def test_datasource(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    config = normalize_datasource(payload or load_datasource())
    table = qualified_table(config)
    time_field = quote_identifier(config["fields"]["time"])
    query = (
        "select json_build_object("
        "'row_count', count(*), "
        f"'latest_time', coalesce(max({time_field})::text, ''), "
        "'table', current_schema()"
        f") from {table};"
    )
    completed = run_psql(config, query, timeout=10)
    info = json.loads(completed.stdout)
    return {
        "ok": True,
        "database": config["database"],
        "table": table,
        "row_count": info.get("row_count", 0),
        "latest_time": info.get("latest_time", ""),
    }
