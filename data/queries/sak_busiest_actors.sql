-- dataset: sakila
-- tags: natural, aggregate, top-n, join
SELECT a.actor_id, a.first_name, a.last_name, COUNT(*) AS n_films
FROM actor a
JOIN film_actor fa ON fa.actor_id = a.actor_id
GROUP BY a.actor_id, a.first_name, a.last_name
ORDER BY n_films DESC, a.actor_id
LIMIT 10
