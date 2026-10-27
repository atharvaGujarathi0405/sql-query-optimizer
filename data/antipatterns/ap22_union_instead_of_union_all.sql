-- dataset: tpch
-- anti-pattern: UNION de-duplicates although the two branches are disjoint (different nations); UNION ALL skips the dedup
-- tags: antipattern, union-vs-union-all
SELECT c_custkey FROM customer WHERE c_nationkey = 1
UNION
SELECT c_custkey FROM customer WHERE c_nationkey = 2
