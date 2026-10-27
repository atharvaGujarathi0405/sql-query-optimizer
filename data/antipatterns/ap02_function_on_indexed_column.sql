-- dataset: sakila
-- anti-pattern: function on an indexed column (DATE(rental_date)) defeats the unique index whose leading column is rental_date
-- tags: antipattern, function-on-column, non-sargable
SELECT rental_id, customer_id, inventory_id
FROM rental
WHERE DATE(rental_date) = '2005-07-08'
