# Threats to validity

Living list. Entries are added as they are found; none are removed. Written
before any evaluation run (2026-10-27, Phase 1). The Phase 3 report restates
the final list.

## Query set

1. **The anti-pattern set is author-constructed (22 of 40 queries, 55%).** The
   same person who wrote the anti-patterns designs the arms. Anti-patterns are
   by construction rewritable, so they favour any arm that is good at spotting
   textbook patterns, and likely favour an LLM that has seen the same
   textbooks. Results on the 22 must not be read as performance on real
   workloads. The 8 control queries (no deliberate anti-pattern, where keeping
   the original is a valid outcome) and the 10 TPC-H standard queries exist to
   bound this, but are few.
2. **Small test set.** Test has 25 queries (13 anti-pattern, 7 TPC-H standard,
   5 control); dev has 15. Per-category cells in test are as small as 3-5, so
   paired tests (Wilcoxon) have little power and per-category claims are
   anecdotal. Report effect sizes with intervals, not just p-values.
3. **Split skew.** TPC-H standard queries are 3 dev / 7 test (30/70 against a
   40/60 target); judged mild and not regenerated. The split is frozen from the
   first evaluation run onward (`data/splits.yaml`).
4. **Weak result oracles.** Several queries return one row (ap07, ap15,
   TPC-H Q6/Q14/Q17). The correctness check cannot distinguish a wrong
   rewrite from the original if both happen to give that one row by accident;
   the equal-multiset test is only as strong as the result is rich.
5. **Single-run feasibility numbers.** The 120 s timeout and the statement
   "no query times out at baseline" rest on one run per query, not on the
   rule-7 protocol.

## Environment and measurement

6. **Buffer pool vs data size.** `innodb_buffer_pool_size` is 3 GiB (raised from
   2 GiB on 2026-10-27, before any evaluation run, because the data left only
   ~14% headroom). The three datasets measure 1.71 GiB (TPC-H 1.56 GiB) per
   `information_schema`, a cached estimate, i.e. ~57% of the pool. They should
   sit in memory after warm-up, but residency has not been verified (check
   `Innodb_buffer_pool_reads` stays flat across timed runs). If it holds,
   timings reflect CPU and plan quality, not disk I/O, which is **not**
   representative of production systems with data larger than memory.
   Rewrites that win by reducing I/O will be under-credited.
7. **Warm cache by design.** Warm-up runs are discarded and all arms share the
   buffer-pool state. This measures steady-state, not cold-start, behaviour.
   Order effects between candidates (one rewrite warming pages for the next)
   are possible; the runner must randomise or interleave order.
8. **One machine, virtualised.** Windows 11 host, i5-8300H (8 logical cores),
   15.8 GiB RAM, MySQL in a Docker Desktop WSL2 VM (7.7 GiB). Background host
   activity adds noise; timings are not portable. Hardware is recorded in every
   results file (`app/eval/runinfo.py`).
9. **Timeouts are their own outcome.** A candidate that exceeds the 120 s cap
   (`config/run.yaml`) is recorded as `timeout` with no runtime, never as
   120 s. A timeout is a right-censored observation: averaging it in as 120 s
   would understate how bad it is, dropping it would hide it. The report
   counts timeouts separately per arm. An arm that picks a timed-out candidate
   has made a failing choice, not a slow one.
10. **Statistics freshness.** `ANALYZE TABLE` was run once after loading. Any
    index experiment (Phase 4) changes the plans and must re-analyse.

## Data provenance

11. **TPC-H data is from a public dbgen mirror**, `electrum/tpch-dbgen` at a
    pinned commit (dbgen 2.14.0), not the official TPC kit, and without the
    TPC audit. Row counts match SF1 exactly; the schema keeps only PK/FK
    indexes. Results are "TPC-H-like", not TPC-H benchmark results.
12. **Sakila payment is 16,044 rows, not 16,049.** The archive at the recorded
    SHA-256 omits five payment ids (424, 7011, 10840, 14675, 15458). The
    expected count was set to match the file by decision of the author after
    inspection. Sakila's download URL is unversioned, so only its hash pins it.
13. **Phase 0 config bug.** The Phase 0 commit claimed a 2 GiB buffer pool that
    was not in effect (MySQL ignored a world-writable `my.cnf`). No evaluation
    ran in that window. Server settings are now passed as flags and read back
    live from the server into each results file.

## To be added in Phase 3

LLM variance, provider mixing, prompt sensitivity, candidate-generator quality
(both arms are capped by the same cached pool), and selection bias in how the
pool was generated.
