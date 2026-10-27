-- dataset: tpch
-- anti-pattern: correlated COUNT(*) subquery in WHERE evaluated per customer; GROUP BY ... HAVING is the rewrite
-- tags: antipattern, correlated-subquery
SELECT c.c_custkey, c.c_name
FROM customer c
WHERE (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) > 20
