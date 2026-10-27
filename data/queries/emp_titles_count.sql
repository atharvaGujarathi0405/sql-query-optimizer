-- dataset: employees
-- tags: natural, aggregate
SELECT title, COUNT(*) AS n
FROM titles
GROUP BY title
ORDER BY title
