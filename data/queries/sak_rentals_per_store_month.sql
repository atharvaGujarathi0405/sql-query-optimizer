-- dataset: sakila
-- tags: natural, aggregate, join
SELECT i.store_id, DATE_FORMAT(r.rental_date, '%Y-%m') AS ym, COUNT(*) AS n
FROM rental r
JOIN inventory i ON i.inventory_id = r.inventory_id
GROUP BY i.store_id, ym
ORDER BY i.store_id, ym
