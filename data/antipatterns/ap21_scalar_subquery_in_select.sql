-- dataset: tpch
-- anti-pattern: scalar correlated subquery in the SELECT list runs once per output row; LEFT JOIN + GROUP BY is the rewrite
-- tags: antipattern, correlated-subquery, subquery-in-select
SELECT c.c_custkey, c.c_name,
       (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) AS n_orders
FROM customer c
WHERE c.c_nationkey = 7
