-- dataset: sakila
-- anti-pattern: OR across two different columns forces index_merge or a scan; UNION of two index lookups is the usual rewrite
-- tags: antipattern, or-across-columns
SELECT rental_id, customer_id, inventory_id
FROM rental
WHERE customer_id = 130 OR inventory_id = 2000
