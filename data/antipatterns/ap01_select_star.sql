-- dataset: sakila
-- anti-pattern: SELECT * across a 3-table join returns every column (incl. duplicates) instead of the ones needed
-- tags: antipattern, select-star, join
SELECT *
FROM rental r
JOIN customer c ON c.customer_id = r.customer_id
JOIN inventory i ON i.inventory_id = r.inventory_id
WHERE c.last_name = 'SMITH'
