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
- `data/sample_spi_data.csv`: sample SPI data for rule verification.
- `smt_quality_agent/rules_engine.py`: core rule engine.
- `smt_quality_agent/affected_model.py`: adapter for `l780db.public.affected_model_0601`.
- `smt_quality_agent/dashboard.py`: dashboard summary and TOP ranking logic.
- `run_demo.py`: runs the sample data through the rule engine.
- `run_l780db_demo.py`: reads PostgreSQL `l780db.public.affected_model_0601` with `psql`.
- `web/`: static MVP page for realtime abnormalities, quality cases, and dashboard.

## Run

```bash
cd /home/xianghappyman/smt_quality_agent_mvp
python3 run_demo.py
```

The demo prints abnormal records and quality-case candidates as JSON.
It also writes these frontend/API-ready files:

- `output/abnormal_results.json`
- `output/quality_cases.json`
- `output/dashboard_summary.json`
- `output/dashboard_top.json`

## View The Page

Generate the JSON files first:

```bash
python3 run_demo.py
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
