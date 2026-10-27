-- dataset: sakila
-- tags: natural, aggregate, top-n
SELECT c.customer_id, c.first_name, c.last_name, SUM(p.amount) AS total
FROM customer c
JOIN payment p ON p.customer_id = c.customer_id
GROUP BY c.customer_id, c.first_name, c.last_name
ORDER BY total DESC, c.customer_id
LIMIT 10
