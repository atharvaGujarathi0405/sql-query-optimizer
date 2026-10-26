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
