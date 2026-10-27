-- dataset: tpch
-- anti-pattern: YEAR() on o_orderdate prevents any range access on the date
-- tags: antipattern, function-on-column, non-sargable
SELECT o_orderpriority, COUNT(*) AS n
FROM orders
WHERE YEAR(o_orderdate) = 1995
GROUP BY o_orderpriority
ORDER BY o_orderpriority
