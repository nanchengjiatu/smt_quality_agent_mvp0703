#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Iterator
from xml.etree import ElementTree as ET
from zipfile import ZipFile


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
EXCEL_EPOCH = datetime(1899, 12, 30)

TEXT_COLUMNS = {
    "machinename",
    "cmodel",
    "barcode",
    "printmode_plan",
    "printmode",
    "printdirection",
    "pcbsize",
    "compname",
    "comp_errname",
}


def normalize_column(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip()).strip("_").lower()
    if not normalized or normalized[0].isdigit():
        normalized = f"col_{normalized}"
    return normalized


def excel_timestamp(raw_value: str) -> str:
    value = float(raw_value)
    timestamp = EXCEL_EPOCH + timedelta(days=value)
    return timestamp.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")


def column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference)
    if letters is None:
        raise ValueError(f"Invalid Excel cell reference: {cell_reference}")

    result = 0
    for character in letters.group():
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def load_shared_strings(workbook: ZipFile) -> list[str]:
    path = "xl/sharedStrings.xml"
    if path not in workbook.namelist():
        return []

    root = ET.fromstring(workbook.read(path))
    return [
        "".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t"))
        for item in root
    ]


def first_sheet_path(workbook: ZipFile) -> str:
    workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
    first_sheet = workbook_root.find(f".//{{{MAIN_NS}}}sheet")
    if first_sheet is None:
        raise ValueError("Workbook does not contain a worksheet")

    relationship_id = first_sheet.attrib[f"{{{REL_NS}}}id"]
    relationships = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
    }
    target = targets[relationship_id].lstrip("/")
    return target if target.startswith("xl/") else f"xl/{target}"


def cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))

    value_node = cell.find(f"{{{MAIN_NS}}}v")
    if value_node is None or value_node.text is None:
        return ""
    if cell_type == "s":
        return shared_strings[int(value_node.text)]
    if cell_type == "b":
        return "true" if value_node.text == "1" else "false"
    return value_node.text


def worksheet_rows(
    worksheet: BinaryIO,
    shared_strings: list[str],
    column_count: int | None = None,
) -> Iterator[list[str]]:
    row_tag = f"{{{MAIN_NS}}}row"
    cell_tag = f"{{{MAIN_NS}}}c"

    for _, element in ET.iterparse(worksheet, events=("end",)):
        if element.tag != row_tag:
            continue

        cells = element.findall(cell_tag)
        if column_count is None:
            width = max((column_index(cell.attrib["r"]) for cell in cells), default=-1) + 1
        else:
            width = column_count
        values = [""] * width

        for cell in cells:
            index = column_index(cell.attrib["r"])
            if index < width:
                values[index] = cell_value(cell, shared_strings)

        yield values
        element.clear()


def read_headers(path: Path) -> list[str]:
    with ZipFile(path) as workbook:
        shared_strings = load_shared_strings(workbook)
        with workbook.open(first_sheet_path(workbook)) as worksheet:
            raw_headers = next(worksheet_rows(worksheet, shared_strings))
    headers = [normalize_column(value) for value in raw_headers]
    if len(headers) != len(set(headers)):
        raise ValueError(f"Duplicate columns after normalization: {headers}")
    return headers


def postgres_type(column: str) -> str:
    if column == "fdate":
        return "timestamp without time zone"
    if column in TEXT_COLUMNS:
        return "text"
    return "double precision"


def run_psql(database: str, sql: str) -> None:
    subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", database, "-c", sql],
        check=True,
    )


def table_exists(database: str, table: str) -> bool:
    result = subprocess.run(
        [
            "psql",
            "-X",
            "-At",
            "-d",
            database,
            "-c",
            (
                "select exists ("
                "select 1 from information_schema.tables "
                f"where table_schema='public' and table_name='{table}'"
                ");"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "t"


def escape_copy_value(value: str) -> str:
    if value == "":
        return r"\N"
    return value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def create_table(database: str, table: str, headers: list[str]) -> None:
    definitions = ", ".join(
        f'"{column}" {postgres_type(column)}' for column in headers
    )
    run_psql(database, f'create table public."{table}" ({definitions});')


def import_workbook(database: str, table: str, path: Path, headers: list[str]) -> int:
    quoted_columns = ", ".join(f'"{column}"' for column in headers)
    copy_sql = (
        f'copy public."{table}" ({quoted_columns}) '
        r"from stdin with (format text, delimiter E'\t', null '\N')"
    )
    process = subprocess.Popen(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", database, "-c", copy_sql],
        stdin=subprocess.PIPE,
        text=True,
    )
    if process.stdin is None:
        raise RuntimeError("Failed to open PostgreSQL COPY input")

    row_count = 0
    try:
        with ZipFile(path) as workbook:
            shared_strings = load_shared_strings(workbook)
            with workbook.open(first_sheet_path(workbook)) as worksheet:
                rows = worksheet_rows(worksheet, shared_strings, len(headers))
                next(rows)
                for values in rows:
                    if values[0]:
                        values[0] = excel_timestamp(values[0])
                    process.stdin.write("\t".join(escape_copy_value(value) for value in values))
                    process.stdin.write("\n")
                    row_count += 1
                    if row_count % 100_000 == 0:
                        print(f"{table}: streamed {row_count:,} rows", file=sys.stderr)
    except Exception:
        process.stdin.close()
        process.wait()
        raise

    process.stdin.close()
    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, process.args)
    return row_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the two SPI XLSX files into L780DB")
    parser.add_argument("--database", default="l780db")
    parser.add_argument(
        "--input",
        action="append",
        nargs=2,
        metavar=("TABLE", "XLSX"),
        required=True,
    )
    args = parser.parse_args()

    for table, filename in args.input:
        normalized_table = normalize_column(table)
        path = Path(filename).resolve()
        if table_exists(args.database, normalized_table):
            raise SystemExit(
                f"Refusing to overwrite existing table public.{normalized_table}"
            )

        headers = read_headers(path)
        print(f"Creating public.{normalized_table} with {len(headers)} columns")
        create_table(args.database, normalized_table, headers)
        try:
            row_count = import_workbook(
                args.database, normalized_table, path, headers
            )
        except Exception:
            run_psql(args.database, f'drop table if exists public."{normalized_table}";')
            raise
        print(f"Imported {row_count:,} rows into public.{normalized_table}")


if __name__ == "__main__":
    main()
