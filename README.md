# sql-query-optimizer

Academic testbed asking whether an operations-research decision engine (AHP + TOPSIS) picks better SQL rewrites than an LLM choosing for itself. Three arms run on identical queries, data, hardware and MySQL config: (1) MySQL's optimizer on the original query (baseline), (2) LLM self-selection (2a: SQL + schema only; 2b: also the EXPLAIN/metric table), (3) the OR engine. Arms 2 and 3 choose from the same cached, correctness-verified pool of candidate rewrites. See `CLAUDE.md` for the fairness rules.

## Setup

Requires Docker and Python 3.12+.

1. Copy `.env.example` to `.env` and fill in real values.
2. Start MySQL:
   ```sh
   docker compose up -d
   ```
3. Run the API:
   ```sh
   cd backend
   pip install -e ".[dev]"
   uvicorn app.main:app --reload
   ```
4. Check it: `curl http://127.0.0.1:8000/health` should return `{"status":"ok","db":"connected"}`. Or run `pytest` from `backend/`.

Init scripts in `docker/mysql/init/` only run on first start with an empty volume. If you change user credentials in `.env` afterwards, recreate with `docker compose down -v && docker compose up -d` (this deletes all data).

## Data

```sh
python scripts/fetch_data.py          # Sakila, Employees (pinned), TPC-H SF1 via dbgen in a no-network container -> data/raw/ (see SOURCES.txt)
python scripts/load_data.py           # loads as admin; local_infile is on only during the TPC-H load
python scripts/check_counts.py        # COUNT(*) as reader vs data/expected_counts.yaml
python scripts/build_registry.py      # data/queries.yaml (+ splits.yaml once; frozen)
python scripts/validate_queries.py    # one run per query, timeout from config/run.yaml
```

Threats to validity are tracked in `docs/threats_to_validity.md`.

## Evaluation (Phase 3)

```sh
# 1. candidate pools: one provider for the whole run, checked before caching, written to candidates/
python scripts/generate_pools.py --provider groq --split dev --limit 5
# 2. the three-arm run (arms 1, 2a, 2b, 3, plus 3-raw); refuses the test split without --allow-test
python -m app.eval.runner --run-id dev-001 --provider groq --split dev
# 3. report: per-arm medians, win/tie/loss, OR-vs-LLM Wilcoxon + effect size, failure table
python -m app.eval.report results/dev-001.jsonl
```

Tune on dev only. Run the test split once, from a clean git tree, with `--allow-test`.

## Workload Advisor (Phase 4)

Which indexes are worth their storage for a workload? For each candidate index (parsed from the dev queries of one dataset with SQLGlot) the advisor really `CREATE INDEX`es it as `admin`, `ANALYZE`s, measures the affected queries as the read-only user against a paired baseline, then drops it. An ILP (PuLP + CBC) picks the best set under a storage budget and index cap; a greedy heuristic gets the same measurements. Each chosen set is then created together and the workload re-measured (predicted vs actual gain). Settings: `config/advisor.yaml`. Results: `results/advisor/*.json`.

```sh
curl -X POST localhost:8000/advisor/run -H 'Content-Type: application/json' -d '{"dataset":"sakila"}'   # -> {"job_id": ...}
curl localhost:8000/advisor/jobs/<job_id>                                                                # progress log, then result
```

Dev split only; one run at a time; do not run it during an evaluation run (it re-ANALYZEs tables). Caveats: `docs/threats_to_validity.md` items 46-52 (and 53-54 for unfinished test runs and statistics drift).

## Paper figures

`python scripts/make_figures.py` writes `docs/figures/fig1..fig5` (PNG 300 dpi + SVG) from `results/dev-006.jsonl`, `results/advisor/*.json` and one recorded Query Studio run. They are dev-split reference figures; regenerate Figs 2-3 from the test run once it exists.

## UI (Phase 6, partial)

Overview (project status, headline run, what the OR engine found, AHP weights), Query Studio (optimize a dev query or an ad-hoc one; before/after runtime and SQL), Workload Advisor (run an index experiment, watch its log, read the ILP vs greedy result) and a Results page that reads `results/*.jsonl`. Unfinished test-split runs are withheld by the API (403). The Scale Simulator tab comes with Phase 5.

```sh
cd backend && uvicorn app.main:app --port 8000     # API
cd frontend && npm install && npm run dev          # http://localhost:5173 (proxies the API)
```

Only the dev split is offered in the UI (`GET /queries`), so test-split timings and pools stay unseen before the single frozen test run. Studio runs the real pipeline (timed runs, and an LLM call when no pool is cached), so a new query can take minutes.
