-- dataset: sakila
-- anti-pattern: NOT IN (subquery): not an anti-join in general, and NULL-unsafe; NOT EXISTS / LEFT JOIN IS NULL is the safe form
-- tags: antipattern, not-in, anti-join
SELECT f.film_id, f.title
FROM film f
WHERE f.film_id NOT IN (SELECT i.film_id FROM inventory i)
