-- dataset: sakila
-- anti-pattern: correlated subquery re-evaluated per outer row (payments above the customer's own average)
-- tags: antipattern, correlated-subquery
SELECT p.payment_id, p.customer_id, p.amount
FROM payment p
WHERE p.amount > (SELECT AVG(p2.amount) FROM payment p2 WHERE p2.customer_id = p.customer_id)
