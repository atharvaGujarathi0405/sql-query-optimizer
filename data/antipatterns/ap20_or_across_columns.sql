-- dataset: tpch
-- anti-pattern: OR across o_custkey (indexed via FK) and o_orderdate (not indexed) forces a scan
-- tags: antipattern, or-across-columns
SELECT o_orderkey, o_custkey
FROM orders
WHERE o_custkey = 1234 OR o_orderdate = '1995-03-15'
