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

## Candidate generation and the OR engine (found in Phase 2)

14. **TOPSIS amplifies measurement noise when criteria tie.** Observed on
    `ap19` (MySQL already turns NOT IN into an anti-join, so every candidate has
    the identical plan, cost and rows examined): runtimes of 417 vs 453 ms (IQR
    25-41 ms) became closeness 1.00 vs 0.19. With three of four criteria equal,
    vector normalisation lets noise decide the winner. Ties go to the original
    only on *exact* equality. Open design question for Phase 3: require a
    rewrite to beat the original by more than its IQR (a fixed threshold, set
    before evaluation) or accept the noise-driven choices and report them.
15. **Selecting and scoring on the same measurement.** The OR engine ranks by
    measured runtime; if the reported runtime of its pick is that same
    measurement, the result is biased (winner's curse). Phase 3 must re-measure
    the chosen query in a fresh timing pass for every arm.
16. **Information asymmetry is by design.** Arm 3 and arm 2b see measured
    runtime, rows examined and EXPLAIN numbers; arm 2a sees SQL and schema
    only. A 3-vs-2a gap partly measures access to measurements, not decision
    quality. The 3-vs-2b comparison is the like-for-like one.
17. **The pool is generated by one LLM and capped at 5 candidates.** Both arms
    inherit its blind spots; neither arm can pick a rewrite the generator never
    proposed. The generator also receives static-analysis hints
    (`optimizer/analysis.py`, derived from SQL text and schema only, never from
    the registry's labels). Duplicates of the original are recorded, not scored.
    Candidates beyond the cap are counted (`n_truncated`), not hidden.
18. **Groq fallback is a different model** (default `qwen/qwen3.8-27b`,
    not Qwen2.5-Coder; `llama-3.3-70b-versatile` was tried first and no longer exists on Groq). Pools or arm-2 answers produced by the fallback are
    not comparable with Ollama ones; each pool records its provider, and an
    evaluation run must pin one (`provider=` disables the fallback).
19. **Correctness checker limits.** (a) Floats compare exactly (the datasets use
    DECIMAL). (b) A rewrite that matches on the current data is accepted even if
    it is not equivalent in general (e.g. a NOT IN to NOT EXISTS change that
    only differs when NULLs appear and the data has none). Validity here means
    "same result on this database", not a proof of equivalence. (c) Order-
    sensitive comparison is a strict sequence match; the registry has no
    ORDER BY ties on the loaded data (checked), so valid rewrites are not
    rejected for tie order.
20. **`est_rows` is a proxy.** It sums per-scan row estimates and does not
    multiply through nested loops. `rows_examined` is measured and exact.
    Weights (AHP, CR 0.007) were set a priori on reasoning, not tuned, and
    are fixed in `config/ahp.yaml`.

## Evaluation harness (found while building Phase 3)

21. **Sample size.** Dev has 15 queries, test 25 (13 anti-pattern, 7 TPC-H standard, 5 control).
    Many queries will have original-only pools (the generator proposed nothing for 3 of 5 dry-run
    queries), and every arm ties there by construction, so the number of *contested* queries is
    smaller still (the report prints it). Wilcoxon on a handful of non-identical pairs has almost no
    power: with 2 non-identical pairs the smallest possible two-sided p is 0.5. Read effect sizes
    and counts; do not claim significance from the dry run or from per-category cells.
22. **LLM variance is only partly measured.** Temperature 0 and a fixed seed make repeats
    near-identical, so the 5 repeats of arms 2a/2b differ mainly by option order (seeded shuffle,
    identical for 2a and 2b within a repeat). The reported variance is therefore order sensitivity
    plus whatever nondeterminism Groq's serving adds (a seed is sent but not guaranteed
    deterministic). It is not sampling variance at a nonzero temperature. Arm 3 has no repeat
    variance at all: one selection pass per query, so its measurement noise is not resampled.
23. **Cache state and pass-to-pass noise.** The selection and evaluation passes run back to back
    in one process on a warm 3 GiB pool (residency still unverified). In the dry run the same
    alternative's median differed between the two passes by up to 19% (`ap03` c3: 855 vs 1021 ms),
    more than the 5% tie band, and the IQR differed by up to 10x (`ap03` original: 8.5 vs 63.2 ms).
    Wins and losses near the band are noise-limited on this machine (Windows host, Docker VM,
    background load). Scoring from a single evaluation pass per alternative gives all arms the
    same number, which removes noise *between arms* but not noise *in* the number.
24. **Query selection bias.** The anti-pattern set is author-written (threat 1), pools come from
    one LLM that returned `[]` for several queries, and queries without a rewrite cannot
    discriminate between arms. Pool generation is conservative, which understates what an OR
    engine could gain from a more productive generator. Pools are not re-rolled after seeing arm
    results.
25. **The generator and the arm-2 selector are the same model** (`qwen/qwen3.8-27b` via Groq). Arm 2
    chooses among options its own sibling call proposed, which may favour it (self-preference) and
    means arm 2 and the pool share blind spots. The OR engine has no such coupling.
26. **Test-split queries were seen before evaluation.** During Phase 2 acceptance I ran
    `ap08` (real Groq pool and timings: 924 ms to 1.9 ms with a trivial rewrite), `ap19` (scripted
    candidates and timings) and `ap02` (scripted candidates in unit/API tests), all in the **test**
    split. Nothing was tuned on them (AHP weights, the noise guard, the tie band and the prompts
    were all fixed without reference to their results), but their baseline timings, and for `ap08` the
    pool, were seen. Their pools and timings are not hidden or re-rolled.
27. **Failure scoring policy.** A failed or unparseable LLM answer, an unusable OR result or a
    missing/mismatched pool is logged and scored as the original (baseline time, a tie). This is the
    same for every arm but biases win/loss counts toward ties. Failures are counted separately.
28. **The OR noise guard is a design choice made without evaluation data** (1.0 x IQR). The raw
    TOPSIS pick (`3-raw`) is scored from the same measurements, so the guard can be judged on the
    dev set; it must not be retuned on test. After the first test-split run it is frozen like the
    split.
29. **Traceability.** The runner records the git commit and whether the tree was dirty (the dry run
    was dirty). It refuses to run the test split from a dirty tree. The test run should happen
    once, from a committed state; dev runs are for tuning and may be repeated.
30. **Wall-clock timing includes fetching rows to the client** over the loopback/Docker network,
    identically for all candidates, but queries returning many rows are partly network-bound.
31. **`rows_examined` was defective in dev-001 (found and fixed).** The criterion was read from
    performance_schema's ROWS_EXAMINED, which misses reads done inside grouped subqueries and
    derived tables. TPC-H Q18: it reported 1,500,513 (original) and 627 (`c3`) where MySQL's own
    Handler_read_* counters show 9,002,201 and 6,002,313; a plain COUNT(*) over 16,044 rows showed
    0. Arms 2b and 3 both saw the wrong column in dev-001 (equally, so their comparison stayed
    like-for-like, but it did not mean what config/ahp.yaml says). It is now the Handler_read_*
    delta around the last timed run. dev-001 is kept as a record of the defective run and must not
    be reported as a result; dev-002 is the first run with the fixed metric. Found by looking at
    the one query where OR and 2b disagreed, not by looking at which arm won overall.
32. **TOPSIS design weaknesses seen in dev-001 (not yet changed).** (a) Vector normalisation lets a
    large outlier alternative (Q18 `c2`: 9 s) shrink the runtime gap between the others. (b) The
    EXPLAIN estimates (weights 0.12 + 0.07) can disagree with measurement: for Q18 `c3`, EXPLAIN
    says cost 8.7M vs 1.6M for the original while it actually runs 27% faster. Changing the
    normalisation or dropping the estimate criteria would be tuning arm 3 on the dev set, which is
    allowed (tune on dev, report on test) but is based on very few contested queries; it must be
    decided deliberately and frozen before the test run.
33. **Dev-003: the OR engine trailed 2b on the contested queries (post-hoc diagnosis).** On the 6
    contested dev queries OR won 2 and 2b won 4 (wins vs baseline). Both OR misses (`ap18`,
    `tpch_q18`) have the same cause: the genuinely faster candidate had far worse EXPLAIN
    estimates (cost 536,529 vs 15,304; 8.7M vs 1.6M), and the estimate criteria, despite the
    smallest AHP weights, outvote runtime because their values differ by orders of magnitude while
    runtime differs by ~30% (vector normalisation works on raw magnitudes). A post-hoc dev-only
    diagnostic scored four designs from the same evaluation pass (sum of ln ratio over the 6
    queries, lower is better): current 4-criteria -3.69; measured-only (runtime + rows examined)
    -4.28; 4 criteria on a log scale -3.93; runtime-only -4.28. Measured-only and runtime-only make
    identical picks and match the hindsight-best alternative everywhere. Caveats: two queries,
    one family (derived-table rewrites), chosen after seeing the result. If measured-only is
    adopted it is dev-tuned and must be disclosed as such; and it implies that on this data the
    multi-criteria machinery adds nothing over "pick the fastest measured candidate with a noise
    guard". Not adopted yet; arm 3 is unchanged from its a-priori design.
34. **dev-002 is contaminated by a network outage** (21 Groq calls failed with DNS errors on `ap03`,
    `ap15`, `ap17`, scored as the original). The arms that do not call the LLM are unaffected.
    The client now retries dropped connections (5/15/45 s) and the runner labels such failures
    `llm_unreachable` instead of `llm_failure`. dev-003 is the first clean dev run.
35. **Declared secondary arm `3-measured` (decision: option C, 2026-10-27).** The primary arm 3 is
    unchanged (four criteria, a-priori weights). `3-measured` is the same engine on runtime and
    rows examined only (weights 0.75 / 0.25, the same judgments restricted, CR 0), with the same
    noise guard. It is declared in `config/ahp.yaml` after the clean dev run `dev-003` and before any
    test run. **Its design was informed by dev data** (two queries; threat 33), so it is a
    post-dev arm, not an a-priori one; it is reported next to arm 3, never instead of it. Caveats:
    (a) three OR variants (`3`, `3-measured`, `3-raw`) are compared against two LLM arms with no
    multiplicity correction, so the paired p-values are descriptive; (b) `3-measured` uses two
    columns while 2b sees four, so 3 vs 2b stays the like-for-like comparison and 3-measured vs 2b
    is not; (c) if `3-measured` and a runtime-only rule make identical picks on test, the AHP/TOPSIS
    machinery contributes nothing beyond "pick the fastest measured candidate", and that is the
    finding to report.
36. **Provider rate limits shape the run.** Groq's free tier gives this model 8,000 tokens per minute
    and 1,000 requests per day. The model reasons at length (calls of 60+ s), so a burst of arm-2
    calls can exhaust the per-minute budget and return 429. dev-004 was aborted for this: the client
    capped its wait at 60 s although Groq asked for longer, so calls were retried too early and
    failed after ~182 s each. The client now honours Retry-After (cap 15 min). A call that still
    fails is logged as a failure and scored as the original; a run with such failures in the LLM arms
    should be repeated rather than reported (as with the outage in dev-002). Run time therefore
    depends on the provider's limits, and the daily cap bounds how many full runs fit in a day.

## Provider switch to Ollama (after dev-005; recorded 2026-11-22)

37. **The final provider is Ollama `qwen2.5-coder:7b` (temperature 0, seed 42), not Groq.** Chosen because it is
    CLAUDE.md's primary provider, has no rate limits (threat 36) and is one provider for the whole run. The
    Groq pools (`qwen/qwen3.8-27b`) are kept in `candidates_groq/` and are never mixed in (the harness refuses a
    pool from another provider). dev-001..dev-004 are Groq runs and are not comparable with dev-006. A 7B model is
    a much weaker generator and selector than the 27B reasoning model, so absolute numbers (how often a rewrite
    exists, how often arm 2 errs) do not transfer to stronger LLMs. Threats 17 and 25 (same model generates the pool
    and acts as arm 2) apply unchanged to the 7B model.
38. **dev-006 is the reference dev run but is not a frozen-state run.** It ran from a dirty git tree
    (`c966d154`, dirty), and its hardware record has `docker_vm_ram_bytes: null` (the `docker info` call failed
    silently; the VM had 8.2 GB in dev-003). Rule 7 asks for hardware to be recorded; `run_metadata` now refuses to
    start without it. The test run must come from a clean tree with the field present. dev-005 is an aborted
    Groq record (429s, no `run_end`).
39. **On dev-006 arm 3 and 3-measured are identical on all 15 queries.** The EXPLAIN-estimate criteria did not flip
    any pick with the Ollama pools, so the a-priori and the post-dev OR variants cannot be told apart on dev. On
    the 12 contested queries OR had 3 wins / 0 losses, 2b 14 wins (query x repeat) / 2 losses, picks identical on
    9 of 15 (Wilcoxon p = 0.0625 for 3 vs 2b, 0.027 for 3 vs 2a). Read as counts and effect sizes; n is small.
40. **Open item: is the multi-criteria machinery needed at all?** If a runtime-only rule picks the same candidates
    on test, AHP/TOPSIS adds nothing beyond "fastest measured candidate with a noise guard" (threat 35c). Whether
    to score an explicit runtime-argmin arm from the same data is decided BEFORE the freeze, not after seeing test.
