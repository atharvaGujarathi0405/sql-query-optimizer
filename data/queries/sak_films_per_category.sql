-- dataset: sakila
-- tags: natural, aggregate, join
SELECT cat.name, COUNT(*) AS n_films
FROM category cat
JOIN film_category fc ON fc.category_id = cat.category_id
GROUP BY cat.name
ORDER BY cat.name
