Phase 1 continues. Do these steps in order and stop where I say.

STEP 1: Audit scripts/fetch_data.py (do not run it)
Print the file and check each point. Report pass or fail for each, and fix any failure:
a. The dbgen docker run mounts only data/raw/ and nothing else (no repo root, no home directory).
b. Every GitHub download is pinned to a tag or commit hash, not main. Print the exact refs. Record them plus the SHA-256 of each downloaded archive in data/raw/SOURCES.txt.
c. dbgen generation runs with --network none. Only the build step may use the network.
d. Nothing is piped from curl or wget into a shell.
e. Fixed dbgen seed behavior is documented (state what dbgen version and default seed are used).
Then STOP and tell me to run `! python scripts/fetch_data.py` myself. Do not run it.

STEP 2: After I say the fetch is done
1. Run scripts/load_data.py. Enable local_infile only for the admin session during loading. Afterward confirm SET GLOBAL local_infile=0 (or that it is session-only) and show the live value.
2. Make check_counts.py use COUNT(*) as the reader user, never information_schema.TABLE_ROWS. Expected counts:
   TPC-H SF1: lineitem 6001215, orders 1500000, partsupp 800000, part 200000, customer 150000, supplier 10000, nation 25, region 5
   Employees: employees 300024, salaries 2844047, titles 443308, dept_emp 331603, departments 9
   Sakila: film 1000, actor 200, customer 599, rental 16044, payment 16049
   Print a table of expected vs actual. If anything differs, show me and stop. Do not adjust the expected values to match.
3. Run scripts/validate_queries.py as reader with the per-query timeout. Make it report, for each query: ok, error, empty result, timeout, or LIMIT without ORDER BY (nondeterministic).

STEP 3: Fix the query set (rules)
- An empty result is a defect, because two empty results always compare equal and the correctness check becomes meaningless. Fix every query that errors or returns zero rows (including ap07). Make fixes using only error messages and result emptiness, never any arm's performance.
- Queries with LIMIT and no ORDER BY: add a deterministic ORDER BY (with a unique tiebreaker) to both the query and its tag, or mark it nondeterministic and exclude it from correctness-dependent comparison. Report which you did, per query. Do not silently drop any query (rule 9).
- Decide and record a per-query timeout in config/ (suggest a value and justify it). Timeouts are their own outcome in results, separate from runtimes. Note which queries time out at baseline (expect TPC-H Q17 and Q18 may).
- Check the dev/test split. Print the count of dataset x category (anti-pattern, control, tpch) in dev and in test. If test has fewer than 3 controls or the imbalance is severe, regenerate the split ONCE now, stratified by category as well as dataset, with a fixed seed, and record why in data/splits.yaml. State clearly that after the first evaluation run the split is frozen.

STEP 4: Documentation and commit
- Add to a docs/threats_to_validity.md file: author-constructed anti-pattern set (22 of 40 queries), buffer pool 2 GB holding TPC-H SF1 in memory after warm-up (timings reflect CPU and plan quality, not disk I/O), timeouts as separate outcome, small test set.
- Make sure every results file will record: MySQL version (8.4.13), live buffer_pool size, collation, and hardware.
- Commit everything, including the my.cnf-to-compose-flags fix, as a single Phase 1 commit. The message must state explicitly that my.cnf was being ignored because Windows bind mounts are mode 777, and that the Phase 0 commit's "2G buffer pool" was not in effect.
- Show me the check_counts table, the validate_queries summary, the split table, and git log -1. Then STOP for my go-ahead before Phase 2.



