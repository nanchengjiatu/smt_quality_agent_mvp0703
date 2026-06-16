# SMT Solder Paste SPI Quality Agent MVP

This MVP focuses on solder paste printing and SPI-stage quality management for
more-solder and less-solder defects.

## Scope

- Import SPI pad-level data from CSV.
- Judge more solder / less solder from `Volume`, then `Area` and `Height`.
- Classify abnormal patterns:
  - single point occasional abnormal
  - same point repeated across 3 boards
  - multiple pads abnormal on the same component
  - board-level trend abnormal
- Recommend likely causes and actions from a deterministic rule matrix.
- Mark medium and high risk abnormalities as quality-case candidates.

## Files

- `sql/schema.sql`: PostgreSQL table definitions and initial knowledge rules.
- `sql/over_volume.sql`: real `l780db.public.over_volume` table definition.
- `data/sample_spi_data.csv`: sample SPI data for rule verification.
- `smt_quality_agent/rules_engine.py`: core rule engine.
- `smt_quality_agent/over_volume.py`: adapter for `l780db.public.over_volume`.
- `smt_quality_agent/affected_model.py`: adapter for `l780db.public.affected_model_0601`.
- `smt_quality_agent/dashboard.py`: dashboard summary and TOP ranking logic.
- `smt_quality_agent/param_correlation.py`: full-data event clustering,
  precursor drift detection, parameter exclusion, and recheck analysis.
- `run_param_analysis_demo.py`: reads `l780db.public.full_excel0608` and
  writes `output/param_analysis.json` for the 事件与根因分析 view.
- `smt_quality_agent/drilldown.py`: three-consecutive-board same-pad trigger
  detection plus the deep-dive analysis package (±300-record window, step vs
  gradual classification, scope, recovery, periodicity, setpoint-change events).
- `run_drilldown_demo.py`: writes `output/drilldown.json` for the drill-down
  workbench (web/drilldown.js).
- `run_over_volume_demo.py`: main entry, reads `l780db.public.over_volume` with `psql`.
- `run_demo.py`: runs the sample CSV data through the rule engine.
- `run_l780db_demo.py`: reads PostgreSQL `l780db.public.affected_model_0601` with `psql`.
- `scripts/import_xlsx_to_l780db.py`: streams SPI XLSX exports into PostgreSQL.
- `web/`: static MVP page for realtime abnormalities, quality cases, and dashboard.
- `smt_quality_agent/pipeline.py`: orchestrates all three analysis stages
  (over_volume / param_analysis / drilldown) and writes every `output/*.json`.
- `serve.py`: one-command launcher — runs the pipeline then serves `web/` +
  `output/` on a single port, with a refresh endpoint (pure stdlib).

## Quick start (one command)

```bash
cd /home/xianghappyman/smt_quality_agent_mvp0608
python3 serve.py            # runs the pipeline once, then serves on :8502
```

Open `http://<host>:8502/` — it redirects to the web app. The ↻ button in the
header re-runs the analysis pipeline on the server (`POST /api/refresh`) and
reloads the data; if a data source is unreachable, the affected tab shows an
honest failure message while the rest keep working.

Options: `--port <n>` (default 8502), `--database <name>` (default `l780db`),
`--no-refresh-on-start` (serve existing `output/` without recomputing).

Endpoints: `GET /` → web app · `GET /output/*` data · `POST /api/refresh`
re-run pipeline (per-stage status JSON) · `GET /api/status` file freshness.

The individual `run_*_demo.py` scripts below remain available for debugging a
single stage from the CLI; the server runs all of them via the pipeline.

## Run (over_volume, primary)

The primary data source is the PostgreSQL table `l780db.public.over_volume`,
which uses the real SPI export structure (printing parameters as
`<name>_plan` / `<name>` / `diff_<name>` columns plus `Comp_*` defect fields).

```bash
cd /home/xianghappyman/smt_quality_agent_mvp0608
python3 run_over_volume_demo.py
```

The demo prints the dashboard summary and writes these frontend/API-ready
files consumed by `web/`:

- `output/abnormal_results.json`
- `output/quality_cases.json`
- `output/dashboard_summary.json`
- `output/dashboard_top.json`

Field mapping (same convention as `affected_model_0601`):

- `fdate` -> `inspect_time`
- `machinename` -> `machine`
- `cmodel` -> `work_order` and `product_name`
- `barcode` -> `board_sn`
- `compname` -> `component` and `pad` when the name ends with `_数字`
- `comp_errname` -> defect type (`Over Volume` -> `多锡`, ...)
- `comp_avdp` / `comp_aadp` / `comp_ahdp` -> volume/area/height deviation
- printing parameter triples (`printspeed`, `frontsqgpress`, `rearsqgpress`,
  `printgap`, `snapoffdistance`, `snapoffspeed`, `snapoffdelay`,
  `cleaningfrequency`, ...) plus `temperature` / `humidity` are carried along
  for later cause correlation.

Note: `over_volume` contains abnormal rows only, so board-level trend rules
stay disabled on this path.

## Run (full-data event & root-cause analysis)

`l780db.public.full_excel0608` holds the complete SPI export (PASS rows
included), which enables analyses the NG-only tables cannot support:

```bash
python3 run_param_analysis_demo.py
```

This writes `output/param_analysis.json`, rendered by the 事件与根因分析 tab:

- data overview: defect rate, board first-pass rate, recheck effective rate;
- event clustering: NG boards of the same model within 30 minutes form one
  event, with board-wide vs local-pad scope classification;
- precursor drift: deviation trend of normal boards before each event
  (sudden vs gradual verdict);
- parameter exclusion: per-event check whether any `abs_<param>` deviation
  exceeded normal production levels (same-model baseline preferred);
- recheck tracking: re-inspections of the same barcode are detected and the
  rework outcome is reported per event.

Dates in `fdate` are stored as text (`2024/1/9 3:12`); the module parses them
before sorting, so do not rely on SQL `order by fdate` for this table.

## Run (three-board same-pad drill-down)

When one pad fails on 3 or more consecutive production boards (re-inspections
neither break nor extend the run), a drill-down package is generated:

```bash
python3 run_drilldown_demo.py
```

This writes `output/drilldown.json`. Entry points in the web UI: the
"三板连发下钻" cards on the 事件与根因分析 tab, and the red "三板连发" badge on
matching rows of the 实时异常 table. The workbench shows:

- run chart of the pad's records around the trigger (requested ±300, actual
  counts reported honestly), with baseline mean / ±3σ band, trigger region,
  recheck hollow points, and setpoint-change (`*_Plan`) event lines;
- step vs gradual classification (reuses the precursor thresholds), scope
  (isolated pad / component-wide / board-wide), recovery tracking (linked to
  setpoint changes when one precedes the recovery), NG-run periodicity check;
- comparison tabs: sibling pads of the same component, a Comp_PX/PY pad map
  colored by trigger-board NG share, and the parameter deviation table;
- findings are clickable and highlight the matching chart range. The chat
  panel is an offline placeholder until an LLM backend is configured — all
  rule-based analysis works without it.

## Run (sample CSV)

```bash
python3 run_demo.py
```

This writes `output/sample_*.json` variants so it does not overwrite the
`over_volume` results.

## View The Page

Generate the JSON files first:

```bash
python3 run_over_volume_demo.py
```

Then start a local static server:

```bash
python3 -m http.server 8000
```

Open:

```text
http://localhost:8000/web/
```

## Reference L780DB Affected Model Table

The MVP can also reference the PostgreSQL table
`l780db.public.affected_model_0601`.

Useful field mapping:

- `fdate` -> `inspect_time`
- `machinename` -> `machine`
- `cmodel` -> `work_order` and `product_name`
- `barcode` -> `board_sn`
- `compname` -> `component` and `pad` when the name ends with `_数字`
- `comp_errname` -> defect type:
  - `Under Volume`, `Under Area`, `Under Height` -> `少锡`
  - `Over Volume`, `Over Height`, `AREAOVER` -> `多锡`
- `comp_avdp`, `comp_aadp`, `comp_ahdp` -> volume/area/height deviation evidence
- printing parameters such as `printspeed`, `frontsqgpress`, `rearsqgpress`,
  `snapoffspeed`, and `cleaningfrequency` remain available for later cause
  correlation.

Run:

```bash
python3 run_l780db_demo.py
```

This writes:

- `output/l780db_abnormal_results.json`
- `output/l780db_quality_cases.json`
- `output/l780db_dashboard_summary.json`
- `output/l780db_dashboard_top.json`

Note: `affected_model_0601` contains affected/abnormal rows, not the full SPI
pad population. Board-level trend rules are only enabled when true full-board
pad counts are supplied.
