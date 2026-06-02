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
- `smt_quality_agent/dashboard.py`: dashboard summary and TOP ranking logic.
- `run_demo.py`: runs the sample data through the rule engine.
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
