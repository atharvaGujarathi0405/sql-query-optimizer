-- dataset: employees
-- anti-pattern: SELECT * over a join pulls all columns of both tables (and duplicate emp_no)
-- tags: antipattern, select-star, join
SELECT *
FROM employees e
JOIN salaries s ON s.emp_no = e.emp_no
WHERE e.emp_no BETWEEN 10001 AND 10200
