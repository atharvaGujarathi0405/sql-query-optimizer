-- dataset: tpch
-- anti-pattern: NOT IN (subquery) over 1.5M orders used as an anti-join
-- tags: antipattern, not-in, anti-join
SELECT c_custkey, c_name
FROM customer
WHERE c_custkey NOT IN (SELECT o_custkey FROM orders)
