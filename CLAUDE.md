# SQL Query Optimizer: OR engine vs LLM self-selection (academic research)

## Thesis
An operations-research decision engine picks better query optimizations than an LLM choosing for itself.
Three arms: (1) plain MySQL optimizer on the original query (baseline), (2) LLM self-selection, (3) OR engine (AHP + TOPSIS).
The OR-vs-LLM comparison is the core contribution. Every design decision must keep it fair.

## Fairness rules (non-negotiable)
1. All arms get the same queries, data, hardware, MySQL config, and measurement method.
2. Arms 2 and 3 choose from the SAME pool of candidate rewrites, generated once per query and cached to disk (candidates/<query_id>.json). Arm 2 never sees a different pool than arm 3.
3. Every candidate passes the correctness check BEFORE either arm sees it. A rewrite is valid only if it returns the same result set as the original (row-count match, then order-insensitive multiset comparison; order-sensitive when the original has ORDER BY). Failures are logged, never silently dropped.
4. Information asymmetry must be explicit and configurable. Arm 2 has two variants: 2a sees only SQL text + schema; 2b also sees the same EXPLAIN/metric table the OR engine uses. Report both.
5. AHP weights are fixed in config/ahp.yaml BEFORE any evaluation run. Never tune weights on the evaluation queries. Split queries into dev and test sets in data/splits.yaml; tune on dev only, report on test.
6. LLM nondeterminism: fixed temperature and seed where supported, and N>=5 repeats per query for arm 2. Report mean and variance.
7. Timing: warm-up runs discarded, then >=5 timed runs, report median and IQR. Same buffer-pool state for all arms. Record MySQL version, config, and hardware.
8. If an arm picks the original query (no rewrite), that is a valid outcome and is scored as the baseline time.
9. Log every failure (LLM timeout, parse error, invalid rewrite) in the results. Never exclude queries after seeing results.

## Security and config
- Two MySQL users: `reader` (SELECT only) runs all candidate queries; `admin` is used only for seeding and index experiments. Candidate execution must never use admin.
- No secrets in code. All config in .env; .env.example is committed with placeholders; .env is gitignored.
- Candidate SQL is executed with the read-only user and a per-query timeout.

## Stack (do not add libraries without stating why)
Backend: FastAPI. Frontend: React + Vite + Tailwind. DB: MySQL 8.4 in Docker.
SQLGlot (parsing), NumPy (AHP/TOPSIS), PuLP + CBC (ILP), SimPy (simulation).
LLM: Ollama with Qwen2.5-Coder locally; Groq free tier as fallback. Log which provider answered each call, because mixing providers across arms threatens validity. Use one provider per evaluation run.
Testing: pytest. API collection for Bruno in /bruno.

## Datasets
MySQL Employees, Sakila, TPC-H SF1, and a hand-built anti-pattern set (data/antipatterns/*.sql). Target at least 40 queries across datasets, and report the count honestly if fewer.

## Layout
backend/app/{api,core,llm,optimizer,advisor,simulator,eval}
backend/tests, frontend/, data/, config/, candidates/, results/, docker/, bruno/, scripts/

## Working style
- Build in phases. At the end of each phase run its acceptance check, print the output, commit, and STOP for my go-ahead.
- Complete, runnable code with file paths. Tests for the correctness checker and for AHP/TOPSIS (known-answer tests).
- Say so when something is unverified. Do not claim a phase passed without running its check.