-- dataset: sakila
-- anti-pattern: LIKE with a leading wildcard cannot use a B-tree range on idx_title
-- tags: antipattern, leading-wildcard-like, non-sargable
SELECT film_id, title
FROM film
WHERE title LIKE '%LOVE%'
